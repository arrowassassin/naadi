"""Importer guards that need no database: content-schema validation of the converted bodies and the
local/remote target check."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from naadi_api.seed.data import (
    CONTENT_MARK_TYPES,
    CONTENT_NODE_TYPES,
    CONTENT_SCHEMA_VERSION,
    SECTION_BODIES,
    SeedDataError,
    body_problems,
    load_bundle,
)
from naadi_api.seed.importer import SeedOptions, SplitTarget, check_targets
from naadi_api.seed.storage import StorageTarget

REPO = Path(__file__).resolve().parents[4]
DATA, CONFIG = REPO / "data", REPO / "config"


def test_pinned_schema_matches_the_generated_file() -> None:
    header = json.loads((DATA / SECTION_BODIES).read_text())
    assert header["schemaVersion"] == CONTENT_SCHEMA_VERSION
    assert set(header["nodeTypes"]) == CONTENT_NODE_TYPES
    assert set(header["markTypes"]) == CONTENT_MARK_TYPES


def _doc(*content: dict[str, object]) -> dict[str, object]:
    return {"type": "doc", "content": list(content)}


def test_body_problems() -> None:
    ok = _doc(
        {"type": "paragraph", "content": [{"type": "text", "text": "a", "marks": [{"type": "bold"}]}]},
        {"type": "figure", "attrs": {"mediaId": "f1"}},
    )
    assert body_problems("s", ok, ["f1"]) == []
    bad = _doc(
        {"type": "blockquote", "content": [{"type": "text", "text": "[[figure:x]]", "marks": [{"type": "link"}]}]},
        {"type": "figure", "attrs": {"mediaId": "f2"}},
    )
    problems = "\n".join(body_problems("s", bad, ["f1"]))
    assert "unknown node type 'blockquote'" in problems
    assert "unknown mark type 'link'" in problems
    assert "[[figure: placeholder left" in problems
    assert "figure nodes ['f2'] differ from figureIds ['f1']" in problems
    assert body_problems("s", {"type": "paragraph"}, []) == ["section s: body is not a doc"]


def _tampered(tmp_path: Path, edit: object) -> Path:
    data = tmp_path / "data"
    shutil.copytree(DATA, data)
    path = data / SECTION_BODIES
    header = json.loads(path.read_text())
    edit(header)  # type: ignore[operator]
    path.write_text(json.dumps(header))
    return data


def test_unknown_node_in_a_body_is_refused(tmp_path: Path) -> None:
    def edit(h: dict) -> None:  # type: ignore[type-arg]
        h["sections"]["ch01-s01"]["body"]["content"][0]["type"] = "iframe"

    with pytest.raises(SeedDataError, match="unknown node type 'iframe'"):
        load_bundle(_tampered(tmp_path, edit), CONFIG)


def test_unknown_schema_version_or_type_list_is_refused(tmp_path: Path) -> None:
    def edit(h: dict) -> None:  # type: ignore[type-arg]
        h["schemaVersion"] = CONTENT_SCHEMA_VERSION + 1
        h["nodeTypes"].append("iframe")

    with pytest.raises(SeedDataError) as e:
        load_bundle(_tampered(tmp_path, edit), CONFIG)
    text = "\n".join(e.value.problems)
    assert "content schema version" in text
    assert "unknown node types ['iframe']" in text


def _opts(db: str, storage: StorageTarget | None, **kw: object) -> SeedOptions:
    return SeedOptions(database_url=db, data_dir=DATA, config_dir=CONFIG, storage=storage, **kw)  # type: ignore[arg-type]


LOCAL_DB = "postgresql://postgres:postgres@localhost:54322/postgres"
REMOTE_DB = "postgresql://postgres:x@db.example.supabase.co:5432/postgres"
LOCAL_STORAGE = StorageTarget(key="k", storage_url="http://127.0.0.1:54335")
REMOTE_STORAGE = StorageTarget(key="k", supabase_url="https://example.supabase.co")


@pytest.mark.parametrize(
    ("db", "storage", "refused"),
    [
        (LOCAL_DB, LOCAL_STORAGE, False),
        (REMOTE_DB, REMOTE_STORAGE, False),
        (LOCAL_DB, REMOTE_STORAGE, True),
        (REMOTE_DB, LOCAL_STORAGE, True),
        (LOCAL_DB, None, False),
    ],
)
def test_split_targets_are_refused(db: str, storage: StorageTarget | None, refused: bool) -> None:
    if refused:
        with pytest.raises(SplitTarget, match="refusing to seed two different projects"):
            check_targets(_opts(db, storage))
        check_targets(_opts(db, storage, allow_split_target=True))  # explicit opt-in
        check_targets(_opts(db, storage, only=("sources",)))  # no Storage involved
    else:
        check_targets(_opts(db, storage))
