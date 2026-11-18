"""The extraction sandbox: every parser that reads untrusted bytes runs in a short-lived child process.

    parent: await run_sandboxed("pdf", data, params)  ->  (ExtractedDoc, {name: bytes})
    child:  python -I -m naadi_api.ingest.sandbox <workdir>

Isolation (defence in depth; nothing in an upload is ever executed):
- a fresh interpreter in isolated mode (`-I`: no user site, no PYTHON* variables, no cwd on sys.path), started in
  a private 0700 temp directory that holds only the input and the outputs;
- an environment with NO secret: only PATH, HOME/TMPDIR (the temp dir), locale, thread counts and the model cache
  directories are passed, so a parser exploit finds no API key, database URL or service-role key to steal;
- resource limits set by the child before it reads a byte: address space (`mem_mb`), CPU seconds, largest file it
  may write, open files, no core dumps; a wall-clock timeout in the parent kills the process group;
- outputs are size-capped and the JSON is validated with `ExtractedDoc` (hard limits) before the parent trusts it.

The child's stderr is discarded (third-party parsers may print document fragments); its exit code and an error code
in `out.json` are all the parent reads besides the result.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import signal
import sys
import tempfile
from pathlib import Path
from typing import Any

from ..logs import get_logger
from . import errors
from .errors import IngestRejected
from .extract.model import ExtractedDoc

log = get_logger("naadi.ingest.sandbox")

DEFAULT_MEM_MB = 2048
MEDIA_MEM_MB = 3072
DEFAULT_TIMEOUT_S = 240.0
MAX_OUTPUT_FILE = 30 * 1024 * 1024
MAX_OUTPUT_TOTAL = 200 * 1024 * 1024
_SAFE_NAME = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-_")
_ENV_PASS = ("PATH", "LANG", "LC_ALL", "HF_HOME", "HF_HUB_OFFLINE", "NAADI_WHISPER_DIR")


def _child_env(workdir: str) -> dict[str, str]:
    env = {k: os.environ[k] for k in _ENV_PASS if k in os.environ}
    env.update(
        {
            "HOME": workdir,
            "TMPDIR": workdir,
            "OMP_NUM_THREADS": "2",
            "OPENBLAS_NUM_THREADS": "2",
            "MKL_NUM_THREADS": "2",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "NO_PROXY": "*",  # parsers have no business on the network
            "no_proxy": "*",
        }
    )
    env.setdefault("PATH", "/usr/bin:/bin")
    return env


async def run_sandboxed(
    op: str,
    data: bytes,
    params: dict[str, Any] | None = None,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    mem_mb: int | None = None,
    cpu_s: int | None = None,
) -> tuple[ExtractedDoc, dict[str, bytes]]:
    workdir = await asyncio.to_thread(tempfile.mkdtemp, prefix="naadi-ingest-")
    os.chmod(workdir, 0o700)
    try:
        await asyncio.to_thread(Path(workdir, "in.bin").write_bytes, data)
        req = {
            "op": op,
            "params": params or {},
            "mem_mb": mem_mb or (MEDIA_MEM_MB if op == "media" else DEFAULT_MEM_MB),
            "cpu_s": cpu_s or int(timeout_s),
        }
        await asyncio.to_thread(Path(workdir, "req.json").write_text, json.dumps(req), "utf-8")
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-m",
            "naadi_api.ingest.sandbox",
            workdir,
            cwd=workdir,
            env=_child_env(workdir),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            async with asyncio.timeout(timeout_s):
                code = await proc.wait()
        except TimeoutError as exc:
            _kill(proc.pid)
            await proc.wait()
            raise IngestRejected(errors.EXTRACT_TIMEOUT) from exc
        return await asyncio.to_thread(_read_outputs, workdir, op, code)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _kill(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, signal.SIGKILL)


def _read_outputs(workdir: str, op: str, code: int) -> tuple[ExtractedDoc, dict[str, bytes]]:
    out_path = Path(workdir, "out.json")
    if not out_path.is_file() or out_path.stat().st_size > 80 * 1024 * 1024:
        # Killed by a limit (memory, CPU, file size) or crashed in native code.
        log.warning("ingest.sandbox_failed", op=op, exit_code=code)
        raise IngestRejected(
            errors.EXTRACT_TIMEOUT if code in (-signal.SIGXCPU, -signal.SIGKILL) else errors.EXTRACT_FAILED
        )
    try:
        payload = json.loads(out_path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise IngestRejected(errors.EXTRACT_FAILED) from exc
    if not isinstance(payload, dict):
        raise IngestRejected(errors.EXTRACT_FAILED)
    if payload.get("error"):
        err = str(payload["error"])
        raw_detail = payload.get("detail")
        detail: dict[str, Any] = raw_detail if isinstance(raw_detail, dict) else {}
        safe = {k: v for k, v in detail.items() if isinstance(k, str) and isinstance(v, int | str) and len(str(v)) < 40}
        raise IngestRejected(
            err if err.replace("_", "").isalnum() and len(err) <= 64 else errors.EXTRACT_FAILED, **safe
        )
    doc = ExtractedDoc.model_validate(payload.get("doc") or {})
    files: dict[str, bytes] = {}
    total = 0
    for name in payload.get("files") or []:
        if not isinstance(name, str) or not name or len(name) > 40 or set(name) - _SAFE_NAME:
            raise IngestRejected(errors.EXTRACT_FAILED)
        path = Path(workdir, "out", name)
        size = path.stat().st_size if path.is_file() else -1
        if size < 0 or size > MAX_OUTPUT_FILE:
            raise IngestRejected(errors.EXTRACT_FAILED)
        total += size
        if total > MAX_OUTPUT_TOTAL:
            raise IngestRejected(errors.EXTRACT_FAILED)
        files[name] = path.read_bytes()
    return doc, files


# ── child ──


def _limit(mem_mb: int, cpu_s: int) -> None:
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (mem_mb * 1024 * 1024, mem_mb * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s + 5))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT_TOTAL + 64 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _child(workdir: str) -> int:
    req = json.loads(Path(workdir, "req.json").read_text(encoding="utf-8"))
    _limit(int(req.get("mem_mb", DEFAULT_MEM_MB)), int(req.get("cpu_s", DEFAULT_TIMEOUT_S)))
    from .extract.workers import OPS

    op = OPS.get(str(req.get("op")))
    out: dict[str, Any]
    if op is None:
        out = {"error": errors.EXTRACT_FAILED}
    else:
        data = Path(workdir, "in.bin").read_bytes()
        try:
            doc, files = op(data, dict(req.get("params") or {}))
            outdir = Path(workdir, "out")
            outdir.mkdir()
            for name, blob in files.items():
                Path(outdir, name).write_bytes(blob)
            out = {"doc": doc.model_dump(mode="json", exclude_defaults=True), "files": sorted(files)}
        except IngestRejected as exc:
            out = {"error": exc.code, "detail": exc.detail}
        except MemoryError:
            out = {"error": errors.EXTRACT_FAILED}
        except Exception:  # noqa: BLE001 - any parser failure is an unreadable file; nothing else is reported
            out = {"error": errors.UNREADABLE}
    Path(workdir, "out.json").write_text(json.dumps(out), encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through run_sandboxed
    sys.exit(_child(sys.argv[1]))
