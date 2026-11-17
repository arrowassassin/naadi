"""A data/ directory with the sample content pack added (tests only): every top-level entry of the real data/ is
symlinked, and `content/` holds the shipped packs plus tests/fixtures/content/sample-pack."""

from __future__ import annotations

from pathlib import Path

from naadi_api.settings import REPO_ROOT

FIXTURE_PACK = Path(__file__).resolve().parents[1] / "fixtures" / "content" / "sample-pack"


def data_dir_with_sample_pack(tmp: Path) -> Path:
    real = REPO_ROOT / "data"
    out = tmp / "data"
    out.mkdir()
    for entry in real.iterdir():
        if entry.name != "content":
            (out / entry.name).symlink_to(entry, target_is_directory=entry.is_dir())
    content = out / "content"
    content.mkdir()
    for pack in sorted((real / "content").iterdir()):
        if pack.is_dir():
            (content / pack.name).symlink_to(pack, target_is_directory=True)
    (content / FIXTURE_PACK.name).symlink_to(FIXTURE_PACK, target_is_directory=True)
    return out
