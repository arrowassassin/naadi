"""Every line the production command writes is JSON, including uvicorn's parent process (--workers 2)."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from .conftest import REPO_ROOT

API_DIR = Path(__file__).resolve().parents[1]
LOG_CONFIG = API_DIR / "naadi_api" / "uvicorn_logging.json"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_dockerfile_uses_the_json_log_config() -> None:
    dockerfile = (REPO_ROOT / "deploy" / "space" / "Dockerfile").read_text()
    # The CMD is shell form (migrate, then exec uvicorn), so the flag is a plain `--log-config <path>` pair.
    assert "uvicorn naadi_api.agent.host:app" in dockerfile
    assert "--log-config naadi_api/uvicorn_logging.json" in dockerfile


def test_production_command_logs_only_json() -> None:
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DATABASE_", "SUPABASE_", "OPENROUTER_"))}
    env.update({"APP_ENV": "prod", "NAADI_CONFIG_DIR": str(REPO_ROOT / "config"), "PYTHONUNBUFFERED": "1"})
    cmd = [sys.executable, "-m", "uvicorn", "naadi_api.main:app", "--host", "127.0.0.1", "--port", str(port)]
    cmd += ["--workers", "2", "--no-access-log", "--log-config", str(LOG_CONFIG)]
    proc = subprocess.Popen(  # noqa: S603 - fixed argv
        cmd, cwd=API_DIR, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    lines: list[str] = []
    try:
        assert proc.stdout is not None
        deadline = time.monotonic() + 30
        startups = 0
        while time.monotonic() < deadline and startups < 2:
            line = proc.stdout.readline()
            if not line:
                break
            lines.append(line)
            startups += '"event": "startup"' in line
    finally:
        proc.terminate()
        out, _ = proc.communicate(timeout=20)
        lines += out.splitlines()
    lines = [ln for ln in lines if ln.strip()]
    assert startups == 2, lines[:20]
    assert any("Uvicorn running" in ln or "Started parent process" in ln for ln in lines), lines[:20]
    for ln in lines:
        json.loads(ln)  # raises on any plain-text line
