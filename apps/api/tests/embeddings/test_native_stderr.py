"""Native (C++) writes to stderr during the model load are silenced; stderr works again afterwards."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[2]

SCRIPT = """
import os
from naadi_api.embeddings.engine import _native_stderr_silenced

with _native_stderr_silenced():
    os.write(2, b"[W:onnxruntime:Default, device_discovery.cc:95] native warning\\n")
os.write(2, b"after\\n")
"""


def test_fd2_is_silenced_only_inside_the_block() -> None:
    proc = subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, "-c", SCRIPT], cwd=API_DIR, capture_output=True, text=True, timeout=60, check=False
    )
    assert proc.stderr == "after\n"
    assert proc.returncode == 0
