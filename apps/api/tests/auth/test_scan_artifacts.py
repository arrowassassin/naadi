from __future__ import annotations

from pathlib import Path

import jwt
import pytest

from tests.auth.scan_artifacts import main, scan


def test_clean_tree_passes(tmp_path: Path) -> None:
    (tmp_path / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    (tmp_path / "notes.txt").write_text("Signed in as a student. eyJ is just a prefix here.")
    assert scan([tmp_path]) == []
    assert main([str(tmp_path)]) == 0


@pytest.mark.parametrize(
    "content",
    [
        jwt.encode({"role": "service_role"}, "k" * 32, algorithm="HS256"),
        '{"access_token":"x","refresh_token":"abcdefghijklmnop"}',
        "token hf_abcdefghijklmnopqrstuvwxyz",
    ],
    ids=["jwt", "session", "hf-key"],
)
def test_credentials_are_found_without_printing_them(
    tmp_path: Path, content: str, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "trace.json").write_text(content)
    assert main([str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "trace.json" in out
    assert content not in out


def test_secret_env_values_are_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "a-minted-throwaway-secret-value")
    (tmp_path / "log.txt").write_text("leaked a-minted-throwaway-secret-value here")
    assert scan([tmp_path]) == [f"{tmp_path / 'log.txt'}: secret-env-value"]
