"""Content packs (data/content/<pack>/) through the real importer: the sample pack (tests/fixtures/content) adds a
chapter with a figure, a single item, an abstract set, a sequential set and two cards, retags one legacy item;
a second run changes nothing; answers and why-not text never reach clients; the new tables keep their RLS."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from psycopg import errors

from naadi_api.seed.data import SeedBundle, load_bundle
from naadi_api.seed.importer import run_seed
from naadi_api.seed.packs import card_uuid

from ..seed.content_fixture import data_dir_with_sample_pack
from .rls import DB_MARKS, as_anon, as_user, create_auth_user, require_local
from .seed_helpers import CONFIG, options, remove_seed

pytestmark = DB_MARKS


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return data_dir_with_sample_pack(tmp_path_factory.mktemp("packs"))


@pytest.fixture(scope="module")
def b(data_dir: Path) -> SeedBundle:
    return load_bundle(data_dir, CONFIG)


@pytest.fixture(scope="module")
def url(migrated_url: str, b: SeedBundle, data_dir: Path) -> Iterator[str]:
    require_local(migrated_url)
    remove_seed(migrated_url, b)
    report = run_seed(options(migrated_url, data_dir=data_dir), bundle=b)
    assert report.ok, report.summary()
    yield migrated_url
    remove_seed(migrated_url, b)


def _one(url: str, query: str, *params: object) -> tuple:  # type: ignore[type-arg]
    with psycopg.connect(url) as c:
        row = c.execute(query, params).fetchone()
    assert row is not None
    return tuple(row)


def test_pack_rows_land_with_their_shape(url: str) -> None:
    ch = _one(url, "select pack_id, source_id, exam from public.chapters where id = 'smp-ch01'")
    assert ch == ("sample-pack", "naadi-original-v1", "step1")
    sec = _one(
        url,
        "select pack_id, taxonomy_ids, further_reading->0->>'sourceId', body->>'type' from public.chapter_sections"
        " where id = 'smp-s01'",
    )
    assert sec == ("sample-pack", ["usmle2026.08.05.01", "usmle2026.08.05", "usmle2026.08"], "statpearls", "doc")
    q = _one(
        url,
        "select item_format, set_id, set_order, difficulty_level, difficulty, why_not ? 'A', why_not ? 'B',"
        " educational_objective is not null, status, taxonomy_ids from public.questions where id = 'smp-q05'",
    )
    assert q[:9] == ("sequential", "smp-set02", 2, 3, "medium", True, True, True, "published")
    assert "usmle2026.task.pc_pharmacotherapy" in q[9] and "usmle2026.08" in q[9]
    assert _one(url, "select why_not_html from public.questions where id = 'smp-q05'")[0].startswith(
        "<p><strong>A.</strong>"
    )
    sets = _one(url, "select count(*), count(*) filter (where kind = 'abstract') from public.question_sets")
    assert sets == (2, 1)
    card = _one(
        url,
        "select origin, owner_user_id, section_id, pack_id, taxonomy_ids[1] from public.flashcards where id = %s",
        card_uuid("smp-c01"),
    )
    assert card == ("seed", None, "smp-s01", "sample-pack", "usmle2026.08.05.01")
    fig = _one(url, "select source_id, url from public.media_assets where id = 'smp-fig01'")
    assert fig == ("naadi-original-v1", "content-public/figures/smp-fig01.svg")


def test_legacy_items_carry_usmle2026_areas_and_a_pack_retag(url: str) -> None:
    tags = _one(url, "select taxonomy_ids from public.questions where id = 'ch05-q01'")[0]
    assert "topic.cardio.physiology_hemodynamics" in tags  # legacy ids kept
    assert "usmle2026.08.08.01" in tags  # the sample pack's retag
    unmapped = _one(
        url,
        "select count(*) from public.questions where pack_id is null and id ~ '^(ch|mock)'"
        " and not exists (select 1 from unnest(taxonomy_ids) t where t ~ '^usmle2026\\.[0-9]{2}\\.')",
    )[0]
    assert unmapped == 0


def test_blueprint_and_taxonomy(url: str) -> None:
    counts = _one(
        url,
        "select count(*) filter (where kind = 'outline_system'), count(*) filter (where kind = 'outline_category'),"
        " count(*) filter (where kind = 'outline_area'), count(distinct source_id) filter (where outline_version"
        " = 'USMLE-2026') from public.taxonomy_nodes",
    )
    assert counts == (18, 147, 276, 1)
    per_step = _one(
        url,
        "select count(*) filter (where exam = 'step1'), count(*) filter (where exam = 'step2ck'),"
        " min(target_items) from public.blueprint_targets where min_items = 6",
    )
    assert per_step == (278, 278, 6)


def test_second_run_changes_nothing(url: str, b: SeedBundle, data_dir: Path) -> None:
    again = run_seed(options(url, data_dir=data_dir), bundle=b)
    assert again.ok and again.changes == 0, again.summary()


def test_clients_never_read_answers_or_why_not(url: str, pg: psycopg.Connection) -> None:
    with as_anon(pg) as conn:
        for col in ("answer", "why_not", "why_not_html", "explanation_html", "educational_objective"):
            with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
                conn.execute(f"select {col} from public.questions where id = 'smp-q01'")  # noqa: S608
        # the abstract (stem-level) is readable; nothing answer-like lives on the set
        assert conn.execute("select intro_html from public.question_sets where id = 'smp-set01'").fetchone()
        assert conn.execute("select count(*) from public.blueprint_targets").fetchone()[0] > 0  # type: ignore[index]
        for table in ("taxonomy_crosswalk", "content_packs"):
            with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
                conn.execute(f"select 1 from public.{table}")  # noqa: S608
        # a seed card linked to a question restates its answer: anon never sees it
        assert (
            conn.execute("select count(*) from public.flashcards where id = %s", (card_uuid("smp-c02"),)).fetchone()[0]
            == 0
        )


def test_pack_tables_are_read_only_for_students(url: str, pg: psycopg.Connection) -> None:
    user = create_auth_user(pg)
    with as_user(pg, user) as conn:
        for stmt in (
            "update public.question_sets set title = 'x'",
            "delete from public.blueprint_targets",
            "insert into public.question_sets (id, exam, kind) values ('evil', 'step1', 'sequential')",
        ):
            with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
                conn.execute(stmt)
