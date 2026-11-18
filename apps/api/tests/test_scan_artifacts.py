"""scripts/scan_artifacts.py: the CI guard that keeps HF_TOKEN out of uploaded artifacts."""

from __future__ import annotations

import base64
import gzip
import importlib.util
import io
import json
import urllib.parse
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

from .conftest import REPO_ROOT

SECRET = "hf_UnitTestTokenValue0123456789abcdefXYZ"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("scan_artifacts", REPO_ROOT / "scripts" / "scan_artifacts.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


scan_artifacts = _load()


def _zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    monkeypatch.setenv("HF_TOKEN", SECRET)
    code = scan_artifacts.main([str(tmp_path / "report"), str(tmp_path / "missing-dir")])
    out = capsys.readouterr()
    return code, out.out + out.err


def test_clean_artifacts_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "report").mkdir()
    (tmp_path / "report" / "junit.xml").write_text("<testsuites tests='1' failures='0'/>")
    (tmp_path / "report" / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 64)
    code, out = _run(tmp_path, monkeypatch, capsys)
    assert code == 0
    assert "2 file(s) scanned" in out


def _trace_zip(header_value: str) -> bytes:
    """Shaped like a Playwright trace: a zip whose network log records request headers."""
    event = {
        "type": "resource-snapshot",
        "snapshot": {"request": {"headers": [{"name": "Authorization", "value": header_value}]}},
    }
    return _zip({"trace.network": json.dumps(event).encode(), "resources/page@1.html": b"<html></html>"})


@pytest.mark.parametrize(
    ("label", "make"),
    [
        (
            "raw text file",
            lambda: {"report/index.html": f"<script>headers={{'authorization':'Bearer {SECRET}'}}</script>".encode()},
        ),
        ("trace zip", lambda: {"report/data/abc.zip": _trace_zip(f"Bearer {SECRET}")}),
        ("zip in zip", lambda: {"report/outer.zip": _zip({"inner/trace.zip": _trace_zip(f"Bearer {SECRET}")})}),
        ("gzip", lambda: {"report/har.json.gz": gzip.compress(f'{{"value":"Bearer {SECRET}"}}'.encode())}),
        ("url-encoded", lambda: {"report/log.txt": ("u=" + urllib.parse.quote(f"x/{SECRET}?", safe="")).encode()}),
    ],
)
def test_secret_is_found_and_never_printed(
    label: str, make: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert callable(make)
    for rel, data in make().items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    code, out = _run(tmp_path, monkeypatch, capsys)
    assert code == 1, label
    assert "appears in" in out
    # Not the value, and not a recognisable fragment of it either.
    assert SECRET not in out and SECRET[3:15] not in out and SECRET[-12:] not in out


@pytest.mark.parametrize("prefix_len", [0, 1, 2, 3, 4, 5])
@pytest.mark.parametrize("urlsafe", [False, True])
def test_base64_embedded_secret_is_found_at_every_alignment(
    prefix_len: int,
    urlsafe: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = b"A" * prefix_len + f"Authorization: Bearer {SECRET}\r\n".encode() + b"Z" * 7
    enc = base64.urlsafe_b64encode(payload) if urlsafe else base64.b64encode(payload)
    (tmp_path / "report").mkdir()
    (tmp_path / "report" / "body.b64").write_bytes(enc)
    code, out = _run(tmp_path, monkeypatch, capsys)
    assert code == 1, out
    assert "base64" in out


def test_unset_secret_fails_loudly(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    assert scan_artifacts.main(["."]) == 2
    assert "HF_TOKEN is not set" in capsys.readouterr().err


def test_corrupt_archive_is_a_failure_not_a_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "report").mkdir()
    (tmp_path / "report" / "trace.zip").write_bytes(b"PK\x03\x04" + b"\xff" * 40)
    code, out = _run(tmp_path, monkeypatch, capsys)
    assert code == 1
    assert "could not be scanned" in out


def test_ci_scans_live_smoke_artifacts_and_uploads_no_report() -> None:
    """The live smoke uploads screenshots and a JUnit summary only, after the guard has passed."""
    import yaml

    wf = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "deploy.yml").read_text())
    steps = wf["jobs"]["deploy"]["steps"]
    names = [s.get("name", s.get("uses", "")) for s in steps]
    guard = next(i for i, s in enumerate(steps) if "scan_artifacts.py" in s.get("run", ""))
    upload = next(i for i, s in enumerate(steps) if str(s.get("uses", "")).startswith("actions/upload-artifact"))
    assert guard < upload, names
    paths = steps[upload]["with"]["path"]
    assert "playwright-report" not in paths and "test-results/playwright" not in paths.replace("playwright-live", "")
    for p in paths.split():
        if p.startswith("!"):  # an exclusion pattern uploads nothing (segment 22: !**/error-context.md)
            continue
        assert p in steps[guard]["run"], p
    assert "!**/error-context.md" in paths
    assert "steps.scan.outcome == 'success'" in steps[upload]["if"]
    assert steps[guard]["id"] == "scan"
