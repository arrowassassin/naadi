"""The seed importer against the real Supabase Postgres + storage-api stack (tests/db/stack.sh).

Full import into an empty database, dry run, idempotence, edits that must survive a re-seed, Storage
self-healing, the one-import-at-a-time lock, and RLS on the seeded content.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import psycopg
import pytest
from psycopg.types.json import Jsonb

from naadi_api.config_api.schemas import KEY_SPECS
from naadi_api.seed.data import SeedBundle
from naadi_api.seed.importer import FIGURE_FOLDER, LOCK_KEY, SeedFailed, SeedLocked, run_seed
from naadi_api.seed.report import SeedReport
from naadi_api.seed.storage import list_folder
from naadi_api.settings import get_settings

from .rls import DB_MARKS, as_anon, as_user, create_auth_user, require_local
from .seed_helpers import BUCKET, CONFIG, DATA, bundle, options, remove_seed, storage

pytestmark = DB_MARKS

BLUEPRINT_ROWS = len(json.loads((DATA / "taxonomy" / "usmle2026.json").read_text(encoding="utf-8"))["blueprint"])

EXPECTED = {
    "chapters": 14,
    "chapter_sections": 259,
    "questions": 685,
    "media_assets": 95,
    "taxonomy_nodes": 688,  # 217 legacy + 471 USMLE-2026 (root, 18 systems, 147 categories, 276 areas, 29 axes)
    "question_map_entries": 685,
    "pathway_stages": 16,
    "pathway_tasks": 186,
    "pathway_guides": 17,
    "plan_template_days": 60,
    "sources": 31,  # + the 2026 outline and 10 S23 research rows
    "question_sets": 0,
    "content_packs": 7,  # one empty pack per S23 author task
    "blueprint_targets": BLUEPRINT_ROWS,
    "pack_flashcards": 0,
    "section_media": 95,
    "app_config": len(KEY_SPECS),  # every registered key (other segments add their own)
}


@pytest.fixture(scope="module")
def b() -> SeedBundle:
    return bundle()


@pytest.fixture(scope="module")
def url(migrated_url: str, b: SeedBundle) -> Iterator[str]:
    require_local(migrated_url)
    remove_seed(migrated_url, b)
    yield migrated_url
    remove_seed(migrated_url, b)


@pytest.fixture(scope="module")
def first_run(url: str, b: SeedBundle) -> tuple[SeedReport, SeedReport]:
    """Dry run on the empty database, then the real import."""
    dry = run_seed(options(url, dry_run=True), bundle=b)
    real = run_seed(options(url), bundle=b)
    return dry, real


def _count(url: str, query: str, *params: object) -> int:
    with psycopg.connect(url) as c:
        row = c.execute(query, params).fetchone()
    assert row is not None
    return int(row[0])


def test_dry_run_reports_the_plan_and_writes_nothing(url: str, b: SeedBundle) -> None:
    dry = run_seed(options(url, dry_run=True), bundle=b)
    assert dry.dry_run and dry.ok
    # the module starts from an empty database (the `url` fixture removed any earlier seed)
    assert dry.tables["questions"].inserted == 685
    assert dry.tables["chapter_sections"].inserted == 259
    assert dry.storage.uploaded == 95
    assert dry.actual == EXPECTED  # counted inside the rolled-back transaction
    assert _count(url, "select count(*) from public.questions where id like 'ch01-q%%'") == 0
    assert _count(url, "select count(*) from public.chapters where id = 'ch01'") == 0
    assert _count(url, "select count(*) from public.app_config") == 0
    assert list_folder(storage().client().from_(BUCKET), FIGURE_FOLDER) == {}


def test_full_import_matches_the_expected_counts(first_run: tuple[SeedReport, SeedReport], url: str) -> None:
    dry, real = first_run
    assert real.ok and not real.dry_run
    assert real.expected == EXPECTED
    assert real.actual == EXPECTED
    assert real.storage.uploaded == 95
    for name, counts in real.tables.items():
        # `user-provided` exists from migration 0028 and the seed finds it unchanged
        migrated = 1 if name == "sources" else 0
        assert (counts.inserted, counts.unchanged) == (counts.total - migrated, migrated), name
    assert all(c.ok for c in real.checks)
    assert {c.name for c in real.checks} >= {
        "no [[figure: placeholder left",
        "every figure node resolves to an approved media_assets row",
        "every figure's Storage object exists",
        "Storage objects match the sanitised figures (MD5)",
    }
    # the dry run predicted exactly what the real run did
    assert {k: v.inserted for k, v in dry.tables.items()} == {k: v.inserted for k, v in real.tables.items()}


def test_second_run_changes_nothing(first_run: object, url: str, b: SeedBundle) -> None:
    again = run_seed(options(url), bundle=b)
    assert again.ok
    assert again.changes == 0, again.summary()
    assert again.storage.unchanged == 95
    assert _count(url, "select count(*) from public.app_config_history") == 0
    assert _count(url, "select count(*) from public.chapter_section_revisions where section_id like 'ch%%'") == 0


def test_seeded_rows_have_the_contracted_shape(first_run: object, url: str) -> None:
    with psycopg.connect(url) as c:
        fig = c.execute(
            "select kind, source_id, review_status, url, width, height, caption, title, attribution, content_sha256"
            " from public.media_assets where id = 'ch01-fig01'"
        ).fetchone()
        assert fig is not None
        assert fig[:6] == ("svg", "naadi-original-v1", "approved", "content-public/figures/ch01-fig01.svg", 760, 420)
        assert fig[6].startswith("Figure 1.")
        assert fig[7] == "Figure 1"
        assert fig[8].startswith("© ") and fig[8].endswith(" Naadi")
        assert len(fig[9]) == 64
        sec = c.execute(
            "select chapter_id, sort, heading, body->>'type', reading_minutes, body_html"
            " from public.chapter_sections where id = 'ch01-s01'"
        ).fetchone()
        assert sec is not None
        assert sec[:4] == ("ch01", 0, "Overview", "doc")
        assert sec[4] >= 1
        q = c.execute(
            "select status, source_id, block, taxonomy_ids, review->>'humanExpertReviewed', educational_objective,"
            " jsonb_array_length(options) from public.questions where id = 'mock1-q01'"
        ).fetchone()
        assert q is not None
        assert q[0:3] == ("published", "naadi-original-v1", 1)
        assert q[3]
        assert q[4] == "false"
        assert q[5] is None
        assert q[6] == 5
        placements = c.execute(
            "select section_id, media_id, sort from public.section_media where section_id = %s order by sort",
            ("ch01-s02",),
        ).fetchall()
        assert [p[2] for p in placements] == list(range(len(placements)))
        guide = c.execute("select stage_key, title from public.pathway_guides where key = 'overview'").fetchone()
        assert guide is not None
        assert guide[0] is None
        day2 = c.execute(
            "select weekday, chapter_ids, target_hours from public.plan_template_days where template_id = 'step1-60d'"
            " and day = 2"
        ).fetchone()
        assert day2 == ("Tue", ["ch03"], 8)
        src = c.execute("select verified, verified_url from public.sources where id = 'youtube'").fetchone()
        assert src == (True, "https://developers.google.com/youtube/terms/developer-policies")
        cfg = c.execute("select value, updated_by from public.app_config where key = 'feature_flags'").fetchone()
        assert cfg is not None
        assert cfg[0]["assistant"]["enabled"] is True
        assert cfg[1] is None


def test_rls_on_seeded_content(first_run: object, url: str) -> None:
    with psycopg.connect(url) as c, c.transaction(force_rollback=True):
        uid = create_auth_user(c)
        for ctx in (as_anon(c), as_user(c, uid)):
            with ctx:
                n = c.execute("select count(*) from public.chapters where id = any(%s)", (list({"ch01", "mock2"}),))
                assert n.fetchone() == (2,)
                assert c.execute("select count(*) from public.media_assets where id like 'ch%%-fig%%'").fetchone() == (
                    95,
                )
                assert c.execute(
                    "select count(*) from public.section_media where section_id like 'ch%%'"
                ).fetchone() == (95,)
                assert c.execute("select count(*) from public.questions where id like 'ch01-q%%'").fetchone() == (40,)
                assert c.execute("select count(*) from public.pathway_guides").fetchone()[0] >= 17  # type: ignore[index]
                assert c.execute("select count(*) from public.plan_template_days").fetchone()[0] >= 60  # type: ignore[index]
                for col in ("answer", "answer_html", "explanation_html", "why_not_html", "review"):
                    with c.transaction(force_rollback=True), pytest.raises(psycopg.errors.InsufficientPrivilege):
                        c.execute(f"select {col} from public.questions limit 1")  # noqa: S608 - fixed column names
                for table in ("app_config", "seed_runs", "app_config_history"):
                    with c.transaction(force_rollback=True), pytest.raises(psycopg.errors.InsufficientPrivilege):
                        c.execute(f"select 1 from public.{table} limit 1")  # noqa: S608 - fixed table names
        # an unapproved figure disappears for clients
        c.execute("update public.media_assets set review_status = 'unreviewed' where id = 'ch01-fig01'")
        with as_anon(c):
            assert c.execute("select count(*) from public.media_assets where id = 'ch01-fig01'").fetchone() == (0,)
            assert c.execute("select count(*) from public.section_media where media_id = 'ch01-fig01'").fetchone() == (
                0,
            )


def test_admin_edits_survive_a_reseed_and_force_overwrites(first_run: object, url: str, b: SeedBundle) -> None:
    with psycopg.connect(url, autocommit=True) as c:
        admin = create_auth_user(c)
        edited = {"agent.quota_reset": "Quota back at {time}", "content.report_issue": "Report a problem"}
        c.execute(
            # what the admin PUT writes; the mark, not updated_by, is what protects the row (the admin's account
            # may be deleted later)
            "update public.app_config set value = %s, updated_by = %s, admin_edited_at = now() where key = 'ui_copy'",
            (Jsonb(edited), admin),
        )
        c.execute(
            "insert into public.chapter_section_revisions (section_id, body, body_html, author_user_id, origin)"
            " select id, body, body_html, %s, 'admin' from public.chapter_sections where id = 'ch02-s01'",
            (admin,),
        )
        c.execute("update public.chapter_sections set body_html = '<p>admin edit</p>' where id = 'ch02-s01'")
    try:
        r = run_seed(options(url), bundle=b)
        assert r.tables["app_config"].kept == 1
        assert r.tables["chapter_sections"].kept == 1
        assert r.changes == 0
        assert (
            _count(url, "select count(*) from public.app_config where key = 'ui_copy' and value ? 'agent.offline'") == 0
        )
        assert _count(url, "select count(*) from public.chapter_sections where body_html = '<p>admin edit</p>'") == 1
        # the admin edit itself went to history (trigger), with the seeded value
        assert _count(url, "select count(*) from public.app_config_history where key = 'ui_copy'") == 1

        forced = run_seed(options(url, force=True), bundle=b)
        assert forced.tables["app_config"].updated == 1
        assert forced.tables["chapter_sections"].updated == 1
        assert forced.tables["chapter_section_revisions"].inserted == 1
        assert (
            _count(
                url,
                "select count(*) from public.app_config where key = 'ui_copy' and updated_by is null"
                " and admin_edited_at is null",
            )
            == 1
        )
        assert _count(url, "select count(*) from public.chapter_sections where admin_edited_at is not null") == 0
        # the replaced (admin) body is kept as a 'seed' revision
        assert (
            _count(
                url,
                "select count(*) from public.chapter_section_revisions where section_id = 'ch02-s01'"
                " and origin = 'seed' and body_html = '<p>admin edit</p>'",
            )
            == 1
        )
    finally:
        with psycopg.connect(url, autocommit=True) as c:
            c.execute("delete from public.chapter_section_revisions where section_id = 'ch02-s01'")
            c.execute("delete from public.app_config_history where key = 'ui_copy'")
            c.execute("delete from auth.users where id = %s", (admin,))


def test_drift_is_repaired_with_a_revision(first_run: object, url: str, b: SeedBundle) -> None:
    """Seed-owned data that drifted without an admin mark (a stray write in a seed session) is repaired."""
    with psycopg.connect(url, autocommit=True) as c, c.transaction():
        c.execute("select set_config('naadi.seed', 'on', true)")  # not an admin edit (migration 0036 trigger)
        c.execute(
            "update public.chapter_sections set body = jsonb_set(body, '{content,0,type}', '\"paragraph\"')"
            " where id = 'ch03-s02'"
        )
        c.execute(
            "insert into public.section_media (section_id, media_id, sort, origin)"
            " values ('ch03-s02', 'ch01-fig01', 99, 'seed') on conflict do nothing"
        )
        c.execute(
            "update public.questions set answer = case when answer = 'A' then 'B' else 'A' end where id = 'ch03-q01'"
        )
    try:
        r = run_seed(options(url), bundle=b)
        assert r.tables["chapter_sections"].updated == 1
        assert r.tables["chapter_section_revisions"].inserted == 1
        assert r.tables["section_media"].deleted == 1
        assert r.tables["questions"].updated == 1
        assert run_seed(options(url), bundle=b).changes == 0
    finally:
        with psycopg.connect(url, autocommit=True) as c:
            c.execute("delete from public.chapter_section_revisions where section_id = 'ch03-s02'")


def test_admin_edits_and_attachments_survive_a_seed_unless_forced(first_run: object, url: str, b: SeedBundle) -> None:
    """review19 defect 1: the admin's fixes to seed questions, chapters and figures, and the admin's own
    section_media attachments, survive the next deploy seed; --force takes rows back but never deletes admin
    attachments."""
    q = next(x for x in b.questions if x.id == "ch01-q01")
    other = "A" if q.answer != "A" else "B"
    with psycopg.connect(url, autocommit=True) as c:
        # What the admin API does (any non-seed write marks the row).
        c.execute("update public.questions set answer = %s, status = 'retired' where id = 'ch01-q01'", (other,))
        c.execute("update public.chapters set status = 'retired', title = 'Admin-retitled' where id = 'ch01'")
        c.execute("update public.media_assets set caption = 'Admin caption' where id = 'ch01-fig01'")
        c.execute("insert into public.section_media (section_id, media_id, sort) values ('ch02-s01', 'ch01-fig01', 50)")
        marks = c.execute(
            "select (select admin_edited_at is not null from public.questions where id = 'ch01-q01'),"
            " (select admin_edited_at is not null from public.chapters where id = 'ch01'),"
            " (select admin_edited_at is not null from public.media_assets where id = 'ch01-fig01'),"
            " (select origin from public.section_media where section_id = 'ch02-s01' and media_id = 'ch01-fig01')"
        ).fetchone()
        assert marks == (True, True, True, "admin")
        # Bookkeeping writes are not edits: psychometrics on another question leave it unmarked.
        c.execute("update public.questions set stats = '{\"n\": 1}'::jsonb where id = 'ch01-q02'")
        assert c.execute("select admin_edited_at from public.questions where id = 'ch01-q02'").fetchone() == (None,)
    try:
        r = run_seed(options(url), bundle=b)
        assert r.ok
        with psycopg.connect(url) as c:
            row = c.execute(
                "select (select answer || status from public.questions where id = 'ch01-q01'),"
                " (select title || status from public.chapters where id = 'ch01'),"
                " (select caption from public.media_assets where id = 'ch01-fig01'),"
                " (select count(*) from public.section_media where section_id = 'ch02-s01' and media_id = 'ch01-fig01')"
            ).fetchone()
        assert row == (f"{other}retired", "Admin-retitledretired", "Admin caption", 1)
        forced = run_seed(options(url, force=True), bundle=b)
        assert forced.ok
        with psycopg.connect(url) as c:
            row = c.execute(
                "select (select answer || status from public.questions where id = 'ch01-q01'),"
                " (select status from public.chapters where id = 'ch01'),"
                " (select admin_edited_at from public.chapters where id = 'ch01'),"
                " (select count(*) from public.section_media where section_id = 'ch02-s01' and media_id = 'ch01-fig01')"
            ).fetchone()
        assert row == (f"{q.answer}published", "published", None, 1)  # attachment kept even when forced
    finally:
        with psycopg.connect(url, autocommit=True) as c:
            c.execute("delete from public.section_media where section_id = 'ch02-s01' and media_id = 'ch01-fig01'")
            c.execute("update public.questions set stats = '{}'::jsonb where id = 'ch01-q02'")
        run_seed(options(url, force=True), bundle=b)


def test_a_seed_figure_an_admin_removed_stays_removed(first_run: object, url: str, b: SeedBundle) -> None:
    """review19 R3: in an admin-edited section the published body decides the figures; a non-forced seed neither
    re-adds the seed placement the admin removed nor fails its checks, and --force takes the section back."""
    sec = next(s for _c, _i, s in b.sections if b.bodies.sections[s.id].figure_ids)
    fid = b.bodies.sections[sec.id].figure_ids[0]
    with psycopg.connect(url, autocommit=True) as c:
        # What an admin publish of a body without the figure does (admin/chapters.py publish_body).
        c.execute("update public.chapter_sections set admin_edited_at = now() where id = %s", (sec.id,))
        c.execute("delete from public.section_media where section_id = %s and media_id = %s", (sec.id, fid))
    try:
        r = run_seed(options(url), bundle=b)
        assert r.ok, [ch for ch in r.checks if not ch.ok]
        with psycopg.connect(url) as c:
            n = c.execute(
                "select count(*) from public.section_media where section_id = %s and media_id = %s", (sec.id, fid)
            ).fetchone()
        assert n == (0,)
    finally:
        forced = run_seed(options(url, force=True), bundle=b)
    assert forced.ok
    with psycopg.connect(url) as c:
        n = c.execute(
            "select count(*) from public.section_media where section_id = %s and media_id = %s and origin = 'seed'",
            (sec.id, fid),
        ).fetchone()
    assert n == (1,)


def test_a_missing_storage_object_is_restored(first_run: object, url: str, b: SeedBundle) -> None:
    bucket = storage().client().from_(BUCKET)
    bucket.remove([f"{FIGURE_FOLDER}/ch05-fig02.svg"])
    r = run_seed(options(url, only=("figures",)), bundle=b)
    assert r.storage.uploaded == 1
    assert r.storage.unchanged == 94
    assert r.ok
    assert "ch05-fig02.svg" in list_folder(bucket, FIGURE_FOLDER)


def test_only_one_import_at_a_time(first_run: object, url: str, b: SeedBundle) -> None:
    with psycopg.connect(url, autocommit=True) as holder:
        holder.execute("select pg_advisory_lock(%s)", (LOCK_KEY,))
        try:
            with pytest.raises(SeedLocked):
                run_seed(options(url, dry_run=True), bundle=b)
        finally:
            holder.execute("select pg_advisory_unlock(%s)", (LOCK_KEY,))


def test_live_check_seed_checks_pass_and_catch_a_missing_figure(first_run: object, url: str) -> None:
    from psycopg.rows import dict_row

    from .live_check import bundle_expectations, seed_checks

    expected, keys = bundle_expectations()

    def run(c: psycopg.Connection) -> dict[str, bool]:
        out = {}
        for chk in seed_checks(expected, keys):
            with c.cursor(row_factory=dict_row) as cur:
                cur.execute(chk.sql)
                out[chk.name] = chk.evaluate(cur.fetchall())[0]
        return out

    with psycopg.connect(url) as c, c.transaction(force_rollback=True):
        assert all(run(c).values()), run(c)
        # (rolled back below; Storage guards direct deletes, so opt in for this transaction only)
        c.execute("select set_config('storage.allow_delete_query', 'true', true)")
        c.execute("delete from storage.objects where bucket_id = 'content-public' and name = 'figures/ch01-fig01.svg'")
        failing = [k for k, ok in run(c).items() if not ok]
        assert failing == ["seed: every figure node resolves to an approved media row with a Storage object"]


def test_admin_mark_survives_deleting_the_admin(first_run: object, url: str, b: SeedBundle) -> None:
    with psycopg.connect(url, autocommit=True) as c:
        admin = create_auth_user(c)
        c.execute(
            'update public.app_config set value = value || \'{"agent.offline": "Offline."}\','
            " updated_by = %s, admin_edited_at = now() where key = 'ui_copy'",
            (admin,),
        )
        c.execute("delete from auth.users where id = %s", (admin,))  # updated_by -> null
    try:
        r = run_seed(options(url, only=("app_config",)), bundle=b)
        assert r.tables["app_config"].kept == 1
        assert _count(url, "select count(*) from public.app_config where value->>'agent.offline' = 'Offline.'") == 1
    finally:
        run_seed(options(url, only=("app_config",), force=True), bundle=b)
        with psycopg.connect(url, autocommit=True) as c:
            c.execute("delete from public.app_config_history where key = 'ui_copy'")


def test_a_count_mismatch_fails_the_seed_and_the_cli(
    first_run: object, url: str, b: SeedBundle, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from naadi_api.seed import cli, importer

    real = importer.expected_counts

    def off_by_one(bundle: SeedBundle) -> dict[str, int]:
        out = real(bundle)
        out["chapters"] += 1
        return out

    monkeypatch.setattr(importer, "expected_counts", off_by_one)
    with pytest.raises(SeedFailed) as e:
        run_seed(options(url, dry_run=True, only=("chapters", "sources")), bundle=b)
    assert not e.value.report.ok
    assert [c.name for c in e.value.report.checks if not c.ok] == ["count chapters"]
    assert "count chapters (14 / 15)" in str(e.value)  # the message carries the failing count and its detail

    monkeypatch.setenv("DATABASE_URL", url)
    for name in ("SUPABASE_URL", "VITE_SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "NAADI_STORAGE_URL"):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    try:
        code = cli.main(["--dry-run", "--only", "sources,chapters", "--data", str(DATA), "--config", str(CONFIG)])
    finally:
        get_settings.cache_clear()
    assert code == 1
    assert "[FAIL] count chapters" in capsys.readouterr().out


def test_license_gate_rejections_are_reported_not_fatal(url: str, b: SeedBundle, tmp_path: Path) -> None:
    import shutil

    config = tmp_path / "config"
    shutil.copytree(CONFIG, config)
    sources = config / "sources.yaml"
    sources.write_text(sources.read_text() + "\n  - id: no-license\n    name: Missing license fields\n")
    from naadi_api.seed.data import load_bundle

    gated = load_bundle(DATA, config)
    assert gated.sources.rejected == [
        ("no-license", "missing license fields: license, allow_copy, allow_modify, allow_commercial, allow_ai_ingest")
    ]
    r = run_seed(replace(options(url, dry_run=True, only=("sources",)), config_dir=config), bundle=gated)
    assert r.ok
    assert r.license_gate["rejected"][0]["id"] == "no-license"
    assert "1 rejected" in r.summary()


def test_report_round_trips_as_json(first_run: tuple[SeedReport, SeedReport]) -> None:
    _dry, real = first_run
    again = SeedReport.model_validate(json.loads(real.model_dump_json()))
    assert again.actual == real.actual
    assert "changes:" in real.summary()


def test_unknown_group_is_refused(url: str, b: SeedBundle) -> None:
    with pytest.raises(ValueError, match="unknown groups"):
        run_seed(replace(options(url, dry_run=True), only=("everything",)), bundle=b)
