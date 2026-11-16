"""Loading the engine with no cached models writes only JSON lines (fastembed's loguru output is silenced)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from tests.conftest import REPO_ROOT

API_DIR = Path(__file__).resolve().parents[2]

SCRIPT = """
import sys
from pathlib import Path
from naadi_api.embeddings.engine import Engine, ModelsNotCached, spec_from_config
from naadi_api.logs import configure_logging, get_logger

configure_logging("INFO")
engine = Engine(spec_from_config(Path(sys.argv[1])))
try:
    engine.warm()
except ModelsNotCached:
    get_logger("test").warning("models_missing")
"""


def test_an_empty_model_cache_produces_json_lines_only(tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DATABASE_", "SUPABASE_", "OPENROUTER_"))}
    env.update({"FASTEMBED_CACHE_PATH": str(tmp_path / "empty"), "HF_HUB_OFFLINE": "1", "PYTHONUNBUFFERED": "1"})
    proc = subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, "-c", SCRIPT, str(REPO_ROOT / "config")],
        cwd=API_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    lines = [ln for ln in (proc.stdout + proc.stderr).splitlines() if ln.strip()]
    assert any("models_missing" in ln for ln in lines), lines
    for ln in lines:
        json.loads(ln)  # raises on any plain-text line (loguru, tqdm, ...)
