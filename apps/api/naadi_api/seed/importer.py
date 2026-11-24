"""The seed import: data/ + config/ -> Postgres + Storage, idempotent (upsert by id), one transaction per group.

    run_seed(SeedOptions(database_url=..., storage=StorageTarget(...), dry_run=False)) -> SeedReport

- Groups (each committed on its own, so an interrupted run resumes where it stopped): sources, taxonomy,
  figures (Storage, then media_assets), chapters + sections + section_media, questions, pathway, plan,
  app_config. Sources go first so every asset's `source_id` resolves.
- A row is written only when a column differs (`is distinct from`), so a second run reports 0 changes and
  fires no updated_at / history triggers. A row an admin (or, for sections, the assistant) edited has
  `admin_edited_at` set and is kept: chapters, sections, questions, media_assets and app_config keys
  (migration 0036 marks chapters, questions and media by trigger on every non-seed write; the importer's own
  transactions set `naadi.seed` so they never mark). `force` overwrites marked rows and clears the mark.
  `section_media` placements the seed did not make (origin 'admin': admin console, YouTube and generated-media
  approvals) are never replaced or deleted, forced or not. When the seed replaces a section body, the previous
  body is saved as a 'seed' revision.
- License gate: sources.yaml rows without complete license fields are skipped and listed in the report; they
  do not fail the run (nothing references them). Every other check, the counts included, must pass.
- Targets: a local database with a remote Storage (or the reverse) is refused unless allow_split_target, so a
  local run can never upload into the real project's bucket, nor a Space run write into a stray database.
- `dry_run`: the same statements run inside one transaction that is rolled back, and nothing is uploaded,
  so the report shows exactly what a real run would insert/update. (Identity sequences may advance; no row
  is kept.)
- A session advisory lock allows one import at a time per database.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, LiteralString
from urllib.parse import unquote, urlparse

import psycopg
import yaml
from psycopg import sql

from ..config_api.defaults import file_values
from ..logs import get_logger
from ..pathway.rules import host_matches
from .data import ORIGINAL_SOURCE, SeedBundle, load_bundle, topological_taxonomy
from .mock import strip_answer_key
from .packs import OUTLINE_VERSION, card_uuid
from .report import Check, SeedReport, StorageCounts, TableCounts
from .storage import StorageTarget, md5_hex, sync_objects
from .svg import SvgError, sanitize_svg

log = get_logger("naadi.seed")

LOCK_KEY = 0x6E616164_69736564  # "naadised": one seed at a time per database
STATEMENT_TIMEOUT_MS = 120_000
FIGURE_FOLDER = "figures"
GROUPS = ("sources", "taxonomy", "figures", "chapters", "videos", "questions", "cards", "pathway", "plan", "app_config")
_FIGURE_TITLE = re.compile(r"^(Figure\s+\d+[A-Za-z]?)\.\s")


class SeedLocked(RuntimeError):
    pass


class SeedFailed(RuntimeError):
    def __init__(self, message: str, report: SeedReport) -> None:
        super().__init__(message)
        self.report = report


@dataclass(frozen=True)
class SeedOptions:
    database_url: str
    data_dir: Path
    config_dir: Path
    storage: StorageTarget | None
    dry_run: bool = False
    only: tuple[str, ...] = GROUPS
    force: bool = False
    allow_split_target: bool = False


LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})


class SplitTarget(ValueError):
    """The database and Storage are not the same kind of target (one local, one remote)."""


def _is_local(url: str | None) -> bool:
    host = (urlparse(url).hostname or "") if url else ""
    return host in LOOPBACK or host.endswith(".localhost")


def check_targets(opts: SeedOptions) -> None:
    if opts.storage is None or "figures" not in opts.only or opts.allow_split_target:
        return
    db_local = _is_local(opts.database_url)
    storage_local = _is_local(opts.storage.storage_url or opts.storage.supabase_url)
    if db_local != storage_local:
        where = ("local", "remote") if db_local else ("remote", "local")
        raise SplitTarget(
            f"the database is {where[0]} but Storage is {where[1]}: refusing to seed two different projects"
            " (pass allow_split_target / --allow-split-target if this is really intended)"
        )


# ── generic upsert ──


_column_types: dict[str, dict[str, str]] = {}


def _types(cur: psycopg.Cursor[Any], table: str) -> dict[str, str]:
    if table not in _column_types:
        cur.execute(
            "select a.attname, format_type(a.atttypid, a.atttypmod) from pg_attribute a"
            " where a.attrelid = %s::regclass and a.attnum > 0 and not a.attisdropped",
            (f"public.{table}",),
        )
        _column_types[table] = {r[0]: r[1] for r in cur.fetchall()}
    return _column_types[table]


def _jsonable(v: Any) -> Any:
    if isinstance(v, dt.date | dt.datetime):
        return v.isoformat()
    return v


def _payload(rows: Sequence[Mapping[str, Any]]) -> str:
    return json.dumps(
        [{k: _jsonable(v) for k, v in r.items()} for r in rows], separators=(",", ":"), ensure_ascii=False
    )


def upsert(
    cur: psycopg.Cursor[Any],
    table: str,
    keys: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
    *,
    guard: LiteralString | None = None,
    keep_existing: Sequence[str] = (),
) -> TableCounts:
    """Insert or update `rows` (dicts with identical keys); unchanged rows are not touched.

    `guard`: extra SQL condition on the existing row (alias `t`) that must hold for an update.
    `keep_existing`: columns kept when the existing row already has a non-null value.
    """
    counts = TableCounts(total=len(rows))
    if not rows:
        return counts
    cols = list(rows[0].keys())
    types = _types(cur, table)
    unknown = [c for c in cols if c not in types]
    if unknown:
        raise ValueError(f"{table}: unknown columns {unknown}")
    ident = sql.Identifier
    col_defs = sql.SQL(", ").join(sql.SQL("{} {}").format(ident(c), sql.SQL(types[c])) for c in cols)
    col_list = sql.SQL(", ").join(ident(c) for c in cols)
    updatable = [c for c in cols if c not in keys]

    def new_value(c: str) -> sql.Composable:
        if c in keep_existing:
            return sql.SQL("coalesce(t.{c}, excluded.{c})").format(c=ident(c))
        return sql.SQL("excluded.{}").format(ident(c))

    conflict: sql.Composable
    if updatable:
        sets = sql.SQL(", ").join(sql.SQL("{} = {}").format(ident(c), new_value(c)) for c in updatable)
        old = sql.SQL(", ").join(sql.SQL("t.{}").format(ident(c)) for c in updatable)
        new = sql.SQL(", ").join(new_value(c) for c in updatable)
        cond = sql.SQL("row({}) is distinct from row({})").format(old, new)
        if guard:
            cond = sql.SQL("{} and ({})").format(cond, sql.SQL(guard))
        conflict = sql.SQL("do update set {} where {}").format(sets, cond)
    else:
        conflict = sql.SQL("do nothing")
    stmt = sql.SQL(
        "insert into public.{table} as t ({cols}) select {cols} from jsonb_to_recordset(%s::jsonb) as x({defs})"
        " on conflict ({keys}) {conflict} returning (xmax = 0) as inserted"
    ).format(
        table=ident(table),
        cols=col_list,
        defs=col_defs,
        keys=sql.SQL(", ").join(ident(k) for k in keys),
        conflict=conflict,
    )
    cur.execute(stmt, (_payload(rows),))
    flags = [r[0] for r in cur.fetchall()]
    counts.inserted = sum(1 for f in flags if f)
    counts.updated = len(flags) - counts.inserted
    counts.unchanged = counts.total - len(flags)
    return counts


# ── groups ──


def _sources(cur: psycopg.Cursor[Any], b: SeedBundle) -> dict[str, TableCounts]:
    rows = [
        {
            "id": s.id,
            "name": s.name,
            "url": s.url,
            "license": s.license,
            "allow_copy": s.allow_copy,
            "allow_modify": s.allow_modify,
            "allow_commercial": s.allow_commercial,
            "allow_ai_ingest": s.allow_ai_ingest,
            "attribution_template": s.attribution_template,
            "notes": s.notes,
            "verified": s.verified,
            "verified_url": s.verified_url,
        }
        for s in b.sources.accepted
    ]
    return {"sources": upsert(cur, "sources", ["id"], rows)}


def _taxonomy(cur: psycopg.Cursor[Any], b: SeedBundle) -> dict[str, TableCounts]:
    rows = [
        {
            "id": t.id,
            "kind": t.kind,
            "parent_id": t.parent_id,
            "label": t.label,
            "exam": t.exam,
            "weight_min": t.weight_min,
            "weight_max": t.weight_max,
            "sort": t.sort,
            "outline_version": t.outline_version,
            "code": t.code,
            "level": t.level,
            "source_id": t.source_id,
        }
        for t in topological_taxonomy(b.taxonomy)
    ]
    out = {"taxonomy_nodes": upsert(cur, "taxonomy_nodes", ["id"], rows)}
    if b.outline is not None:
        out["blueprint_targets"] = upsert(cur, "blueprint_targets", ["node_id", "exam"], blueprint_rows(b))
        out["taxonomy_crosswalk"] = upsert(cur, "taxonomy_crosswalk", ["from_id", "to_id"], crosswalk_rows(b))
    out["content_packs"] = upsert(
        cur,
        "content_packs",
        ["id"],
        [
            {
                "id": p.meta.id,
                "title": p.meta.title,
                "owner": p.meta.owner,
                "outline_version": p.meta.outline_version,
                "systems": p.meta.systems,
            }
            for p in b.packs
        ],
    )
    return out


def blueprint_rows(b: SeedBundle) -> list[dict[str, Any]]:
    assert b.outline is not None
    return [
        {
            "node_id": r.node_id,
            "exam": r.exam,
            "outline_version": OUTLINE_VERSION,
            "row_key": r.row,
            "row_label": r.row_label,
            "weight_min": r.weight_min,
            "weight_max": r.weight_max,
            "share": r.share,
            "min_items": r.min_items,
            "target_items": r.target_items,
            "tested": r.tested,
        }
        for r in b.outline.blueprint
    ]


def crosswalk_rows(b: SeedBundle) -> list[dict[str, Any]]:
    assert b.outline is not None
    return [
        {"from_id": src, "to_id": dst, "to_version": OUTLINE_VERSION}
        for src, dsts in sorted(b.outline.crosswalk.items())
        for dst in dsts
    ]


@dataclass
class PreparedFigure:
    id: str
    name: str  # object file name within FIGURE_FOLDER
    data: bytes
    sha256: str
    width: int
    height: int
    fixed: list[str]
    removed: list[str]


def prepare_figures(b: SeedBundle) -> list[PreparedFigure]:
    import hashlib

    out: list[PreparedFigure] = []
    problems: list[str] = []
    for f in b.figures:
        raw = (b.data_dir / f.file).read_bytes()
        try:
            s = sanitize_svg(raw, label=f.caption)
        except SvgError as exc:
            problems.append(f"figure {f.id}: {exc}")
            continue
        assert s.width is not None
        assert s.height is not None
        out.append(
            PreparedFigure(
                f.id, f"{f.id}.svg", s.data, hashlib.sha256(s.data).hexdigest(), s.width, s.height, s.fixed, s.removed
            )
        )
    if problems:
        from .data import SeedDataError

        raise SeedDataError(problems)
    return out


def _attribution(b: SeedBundle, source_id: str = ORIGINAL_SOURCE) -> str:
    template = next((s.attribution_template for s in b.sources.accepted if s.id == source_id), None)
    return (template or "Naadi").replace("{year}", str(dt.datetime.now(dt.UTC).year))


def _media_rows(b: SeedBundle, figures: list[PreparedFigure], bucket: str) -> list[dict[str, Any]]:
    by_id = {f.id: f for f in b.figures}
    rows = []
    for p in figures:
        f = by_id[p.id]
        m = _FIGURE_TITLE.match(f.caption)
        rows.append(
            {
                "id": p.id,
                "kind": "svg",
                "source_id": f.source,
                "title": m.group(1) if m else None,
                "caption": f.caption,
                "url": f"{bucket}/{FIGURE_FOLDER}/{p.name}",
                "attribution": f.attribution or _attribution(b, f.source),
                "width": p.width,
                "height": p.height,
                "content_sha256": p.sha256,
                "review_status": "approved",
                "admin_edited_at": None,  # a forced run takes the figure back from the admin
            }
        )
    return rows


# A section edited outside the seed (admin editor, assistant) is not overwritten unless forced.
SECTION_GUARD: LiteralString = "t.admin_edited_at is null"
# Chapters, questions and media an admin changed (migration 0036 trigger) are kept unless forced.
ADMIN_EDIT_GUARD: LiteralString = "t.admin_edited_at is null"
SEED_PLACEMENT_GUARD: LiteralString = "t.origin = 'seed'"


EPOCH = "1970-01-01T00:00:00Z"  # a YouTube row's metadata is unfetched until the 30-day refresh fills it


def _file_title(url: str | None, fallback: str | None) -> str | None:
    """ "…/Acute_cholecystitis_1.webm" -> "Acute cholecystitis 1"."""
    name = unquote(urlparse(url or "").path.rsplit("/", 1)[-1])
    return name.rsplit(".", 1)[0].replace("_", " ").strip() or fallback


def _videos(cur: psycopg.Cursor[Any], b: SeedBundle, *, force: bool) -> dict[str, TableCounts]:
    """Video topics, the videos' `media_assets` rows and `video_catalog`. Topics and catalog rows the data no
    longer has are deleted (seed-owned); a refresh's title, duration, attribution and fetch time are kept."""

    def media(v: Any) -> dict[str, Any]:
        yt = v.provider == "youtube"
        return {
            "id": v.id,
            "kind": "video_youtube" if yt else "video_file",
            "source_id": "youtube" if yt else "wikimedia-commons",
            # A Commons file's title is its file name (CC metadata), not the topic label: many files share a topic.
            "title": (v.label or None) if yt else _file_title(v.file_url, v.label),
            "url": None if yt else v.file_url,
            "page_url": None if yt else v.file_page,
            "licence": v.licence_snapshot,
            "attribution": None if yt else v.attribution,
            "youtube_id": v.youtube_id,
            "channel_id": v.channel_id,
            "duration_s": v.duration_s,
            "embeddable": True if yt else None,
            "metadata_fetched_at": EPOCH if yt else None,
            "review_status": "approved",
            "admin_edited_at": None,  # a forced run takes the video back from the admin
        }

    guard = None if force else ADMIN_EDIT_GUARD
    # A YouTube row that exists belongs to the 30-day refresh (title, duration, attribution, fetch time, and
    # review_status / embeddable when it retires the video): the seed only keeps its identity columns, force or not.
    yt_rows = [media(v) for v in b.videos if v.provider == "youtube"]
    cur.execute("select id from public.media_assets where id = any(%s)", ([r["id"] for r in yt_rows],))
    known = {r[0] for r in cur.fetchall()}
    yt_new = [r for r in yt_rows if r["id"] not in known]
    yt_known = [
        {k: r[k] for k in ("id", "kind", "source_id", "youtube_id", "channel_id")} for r in yt_rows if r["id"] in known
    ]
    file_rows = [media(v) for v in b.videos if v.provider == "commons"]
    topics = upsert(
        cur,
        "video_topics",
        ["id"],
        [t.model_dump() for t in b.video_topics],
    )
    topics.deleted = _delete_missing(cur, "video_topics", "id", [t.id for t in b.video_topics])
    counts = upsert(cur, "media_assets", ["id"], yt_new)
    for more in (
        upsert(cur, "media_assets", ["id"], yt_known, guard=guard),
        upsert(cur, "media_assets", ["id"], file_rows, guard=guard),
    ):
        counts.total += more.total
        counts.inserted += more.inserted
        counts.updated += more.updated
        counts.unchanged += more.unchanged
    catalog = upsert(
        cur,
        "video_catalog",
        ["media_id"],
        [
            {
                "media_id": v.id,
                "subject": v.subject,
                "step": v.step,
                "style": v.style,
                "core": v.core,
                "topic_ids": v.topic_ids,
                "provider": v.provider,
                "sort": i,
            }
            for i, v in enumerate(b.videos)
        ],
    )
    catalog.deleted = _delete_missing(cur, "video_catalog", "media_id", [v.id for v in b.videos])
    return {"video_topics": topics, "video_media_assets": counts, "video_catalog": catalog}


def _delete_missing(cur: psycopg.Cursor[Any], table: str, key: str, keep: list[str]) -> int:
    cur.execute(
        sql.SQL("delete from public.{} where {} <> all(%s)").format(sql.Identifier(table), sql.Identifier(key)), (keep,)
    )
    return cur.rowcount


def _chapters(cur: psycopg.Cursor[Any], b: SeedBundle, *, force: bool) -> dict[str, TableCounts]:
    chapters = [
        {
            "id": c.id,
            "exam": c.exam,
            "kind": c.kind,
            "title": c.title,
            "meta": c.meta,
            "sort": c.order,
            "status": "published",
            "source_id": c.source,
            "pack_id": c.pack_id,
            "admin_edited_at": None,  # a forced run takes the chapter back from the admin
        }
        for c in b.chapters
    ]
    out = {"chapters": upsert(cur, "chapters", ["id"], chapters, guard=None if force else ADMIN_EDIT_GUARD)}
    sections = []
    for c, i, s in b.sections:
        body = b.bodies.sections[s.id]
        sec_body: Any = body.body
        sec_html: Any = body.body_html
        if c.kind == "mock_exam":  # answer keys are never content (seed/mock.py)
            sec_body, sec_html = strip_answer_key(sec_body, sec_html)
        sections.append(
            {
                "id": s.id,
                "chapter_id": c.id,
                "sort": i,
                "heading": s.heading,
                "body": sec_body,
                "body_html": sec_html,
                "reading_minutes": body.reading_minutes,
                "admin_edited_at": None,  # a forced run takes the section back from the editor
            }
        )
    # Remember what the seed is about to replace, then upsert, then keep the replaced bodies as revisions.
    cur.execute(
        "create temp table if not exists naadi_seed_prev (id text primary key, body jsonb, body_html text)"
        " on commit drop"
    )
    cur.execute("truncate naadi_seed_prev")
    cur.execute(
        "insert into naadi_seed_prev select s.id, s.body, s.body_html from public.chapter_sections s"
        " join jsonb_to_recordset(%s::jsonb) as x(id text, body jsonb, body_html text) on x.id = s.id"
        " where row(s.body, s.body_html) is distinct from row(x.body, x.body_html)",
        (_payload([{"id": r["id"], "body": r["body"], "body_html": r["body_html"]} for r in sections]),),
    )
    guard = None if force else SECTION_GUARD
    if not force:
        cur.execute(
            "select count(*) from naadi_seed_prev p join public.chapter_sections s on s.id = p.id"
            " where s.admin_edited_at is not null"
        )
        row = cur.fetchone()
        kept = int(row[0]) if row else 0
    else:
        kept = 0
    # Legacy sections keep their original column set (their taxonomy_ids belong to whoever set them); pack
    # sections also carry their pack, USMLE-2026 areas and link-only further reading.
    pack_of = {s.id: (c.pack_id, s) for c, _i, s in b.sections if c.pack_id}
    counts = upsert(cur, "chapter_sections", ["id"], [r for r in sections if r["id"] not in pack_of], guard=guard)
    packed = upsert(
        cur,
        "chapter_sections",
        ["id"],
        [
            r
            | {
                "pack_id": pack_of[r["id"]][0],
                "taxonomy_ids": pack_of[r["id"]][1].taxonomy_ids or [],
                "further_reading": pack_of[r["id"]][1].further_reading or [],
            }
            for r in sections
            if r["id"] in pack_of
        ],
        guard=guard,
    )
    counts.total += packed.total
    counts.inserted += packed.inserted
    counts.updated += packed.updated
    counts.unchanged += packed.unchanged
    counts.kept = kept
    counts.unchanged -= kept
    out["chapter_sections"] = counts
    cur.execute(
        "insert into public.chapter_section_revisions (section_id, body, body_html, origin, note)"
        " select p.id, p.body, p.body_html, 'seed', 'replaced by a seed import' from naadi_seed_prev p"
        " join public.chapter_sections s on s.id = p.id"
        " where row(s.body, s.body_html) is distinct from row(p.body, p.body_html)"
    )
    out["chapter_section_revisions"] = TableCounts(total=cur.rowcount, inserted=cur.rowcount)

    # An admin-edited section's placements follow its published body (admin/chapters.py), not the seed's: a non-forced
    # run neither re-adds a seed figure the admin removed nor deletes anything there (review19 R3).
    edited: set[str] = set()
    if not force:
        cur.execute(
            "select id from public.chapter_sections where id = any(%s) and admin_edited_at is not null",
            ([s.id for _c, _i, s in b.sections],),
        )
        edited = {str(r[0]) for r in cur.fetchall()}
    placements = [
        {"section_id": s.id, "media_id": fid, "sort": n, "origin": "seed"}
        for _c, _i, s in b.sections
        if s.id not in edited
        for n, fid in enumerate(b.bodies.sections[s.id].figure_ids)
    ]
    # Only the seed's own placements are replaced: an admin's attachment of the same media keeps its row.
    sm = upsert(cur, "section_media", ["section_id", "media_id"], placements, guard=SEED_PLACEMENT_GUARD)
    # Figures moved or removed from a seeded section: drop the seed placements the data no longer has. Placements
    # an admin, a YouTube approval or a generated-media approval made (origin 'admin') are never deleted.
    cur.execute(
        "delete from public.section_media m where m.section_id = any(%s) and m.origin = 'seed' and not exists ("
        " select 1 from jsonb_to_recordset(%s::jsonb) as x(section_id text, media_id text)"
        " where x.section_id = m.section_id and x.media_id = m.media_id)",
        (
            [s.id for _c, _i, s in b.sections if s.id not in edited],
            _payload([{k: p[k] for k in ("section_id", "media_id")} for p in placements]),
        ),
    )
    sm.deleted = cur.rowcount
    out["section_media"] = sm
    return out


def _questions(cur: psycopg.Cursor[Any], b: SeedBundle, *, force: bool) -> dict[str, TableCounts]:
    rows = [
        {
            "id": q.id,
            "chapter_id": q.chapter_id,
            "exam": q.exam,
            "block": q.block,
            "stem_html": q.stem_html,
            "options": [o.model_dump() for o in q.options],
            "answer": q.answer,
            "answer_html": q.answer_html,
            "explanation_html": q.explanation_html,
            "why_not_html": q.why_not_html,
            "educational_objective": None,  # not present in the seed; never invented
            "difficulty": q.difficulty,
            "taxonomy_ids": b.question_map[q.id],
            "tags": q.tags,
            "source_id": q.source,
            "status": q.status,
            "review": q.review.model_dump(by_alias=True),
            "admin_edited_at": None,  # a forced run takes the item back from the admin
        }
        for q in b.questions
    ]
    guard = None if force else ADMIN_EDIT_GUARD
    out = {
        "question_sets": upsert(
            cur,
            "question_sets",
            ["id"],
            [
                {
                    "id": s.id,
                    "exam": s.exam,
                    "kind": s.kind,
                    "title": s.title,
                    "intro_html": s.intro_html,
                    "source_id": s.source,
                    "pack_id": pid,
                    "status": "published",
                }
                for pid, s in b.sets
            ],
        )
    }
    legacy = [r for r, q in zip(rows, b.questions, strict=True) if q.pack_id is None]
    packed = [
        r
        | {
            "educational_objective": q.objective,
            "pack_id": q.pack_id,
            "difficulty_level": q.difficulty_level,
            "item_format": q.item_format,
            "set_id": q.set_id,
            "set_order": q.set_order,
            "why_not": q.why_not,
            "figure_ids": q.figure_ids or [],
        }
        for r, q in zip(rows, b.questions, strict=True)
        if q.pack_id is not None
    ]
    counts = upsert(cur, "questions", ["id"], legacy, guard=guard)
    more = upsert(cur, "questions", ["id"], packed, guard=guard)
    counts.total += more.total
    counts.inserted += more.inserted
    counts.updated += more.updated
    counts.unchanged += more.unchanged
    out["questions"] = counts
    return out


def _cards(cur: psycopg.Cursor[Any], b: SeedBundle) -> dict[str, TableCounts]:
    """Pack flashcards: shared (no owner), origin 'seed', with a stable uuid per pack card id."""
    rows = [
        {
            "id": card_uuid(c.id),
            "owner_user_id": None,
            "deck": "Naadi core",
            "front_html": c.front_html,
            "back_html": c.back_html,
            "question_id": c.question_id,
            "section_id": c.section_id,
            "taxonomy_ids": b.outline.with_ancestors(c.areas) if b.outline else [],
            "origin": "seed",
            "source_id": c.source,
            "pack_id": pid,
            "tags": c.tags,
        }
        for pid, c in b.cards
    ]
    return {"flashcards": upsert(cur, "flashcards", ["id"], rows)}


PATHWAY_OFFICIAL_SOURCE = "pathway-official-pages"
PATHWAY_THIRD_PARTY_SOURCE = "pathway-third-party-pages"


def _official_domains(b: SeedBundle) -> list[str]:
    """`search.include_domains` of config/models.yaml: the hosts Naadi calls official."""
    with (b.config_dir / "models.yaml").open(encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    return [str(d) for d in (doc.get("search") or {}).get("include_domains") or []]


def pathway_source_id(url: str | None, domains: list[str]) -> str | None:
    """The `sources` row a pathway link belongs to: official when its host is on an official domain."""
    if not url:
        return None
    host = urlparse(url).hostname or ""
    return PATHWAY_OFFICIAL_SOURCE if host_matches(host, domains) else PATHWAY_THIRD_PARTY_SOURCE


def _pathway(cur: psycopg.Cursor[Any], b: SeedBundle) -> dict[str, TableCounts]:
    domains = _official_domains(b)
    stages = [
        {
            "key": s.key,
            "sort": s.sort,
            "title": s.title,
            "summary_md": s.summary_md,
            "official_links": [
                {**link.model_dump(), "source_id": pathway_source_id(link.url, domains)} for link in s.official_links
            ],
            "last_verified": s.last_verified,
        }
        for s in b.stages
    ]
    tasks = [{**t.model_dump(), "source_id": pathway_source_id(t.official_url, domains)} for t in b.tasks]
    guides = [
        {
            "key": g.key,
            "stage_key": g.stage_key,
            "sort": g.sort,
            "title": g.title,
            "body_md": g.body_md,
            "source_id": ORIGINAL_SOURCE,
        }
        for g in b.guides
    ]
    return {
        "pathway_stages": upsert(cur, "pathway_stages", ["key"], stages),
        "pathway_tasks": upsert(cur, "pathway_tasks", ["id"], tasks),
        "pathway_guides": upsert(cur, "pathway_guides", ["key"], guides),
    }


def _plan(cur: psycopg.Cursor[Any], b: SeedBundle) -> dict[str, TableCounts]:
    p = b.plan
    template = {"id": p.id, "exam": p.exam, "title": p.title, "days": len(p.days), "source_id": ORIGINAL_SOURCE}
    days = [{"template_id": p.id, **d.__dict__} for d in p.days]
    out = {
        "plan_templates": upsert(cur, "plan_templates", ["id"], [template]),
        "plan_template_days": upsert(cur, "plan_template_days", ["template_id", "day"], days),
    }
    cur.execute("delete from public.plan_template_days where template_id = %s and day > %s", (p.id, len(p.days)))
    out["plan_template_days"].deleted = cur.rowcount
    return out


def _app_config(cur: psycopg.Cursor[Any], b: SeedBundle, *, force: bool) -> dict[str, TableCounts]:
    values = file_values(b.config_dir)
    rows = [{"key": k, "value": v, "updated_by": None, "admin_edited_at": None} for k, v in sorted(values.items())]
    kept = 0
    if not force:
        cur.execute(
            "select count(*) from public.app_config a join jsonb_to_recordset(%s::jsonb) as x(key text, value jsonb)"
            " on x.key = a.key where a.admin_edited_at is not null and a.value is distinct from x.value",
            (_payload(rows),),
        )
        row = cur.fetchone()
        kept = int(row[0]) if row else 0
    counts = upsert(cur, "app_config", ["key"], rows, guard=None if force else ADMIN_EDIT_GUARD)
    counts.kept = kept
    counts.unchanged -= kept
    return {"app_config": counts}


# ── verification ──


def verify(cur: psycopg.Cursor[Any], b: SeedBundle, *, storage_pending: set[str]) -> tuple[dict[str, int], list[Check]]:
    section_ids = [s.id for _c, _i, s in b.sections]
    ids = {
        "chapters": [c.id for c in b.chapters],
        "chapter_sections": section_ids,
        "questions": [q.id for q in b.questions],
        "media_assets": [f.id for f in b.figures],
        "video_topics": [t.id for t in b.video_topics],
        "taxonomy_nodes": [t.id for t in b.taxonomy],
        "pathway_tasks": [t.id for t in b.tasks],
        "question_sets": [s.id for _p, s in b.sets],
        "content_packs": [p.meta.id for p in b.packs],
    }
    actual: dict[str, int] = {}
    for table, wanted in ids.items():
        cur.execute(
            sql.SQL("select count(*) from public.{} where id = any(%s)").format(sql.Identifier(table)), (wanted,)
        )
        row = cur.fetchone()
        actual[table] = int(row[0]) if row else 0
    one = _scalar
    actual["video_media_assets"] = one(
        cur,
        "select count(*) from public.media_assets where id = any(%s) and kind in ('video_youtube','video_file')",
        [v.id for v in b.videos],
    )
    actual["video_catalog"] = one(
        cur, "select count(*) from public.video_catalog where media_id = any(%s)", [v.id for v in b.videos]
    )
    actual["pathway_stages"] = one(
        cur, "select count(*) from public.pathway_stages where key = any(%s)", [s.key for s in b.stages]
    )
    actual["pathway_guides"] = one(
        cur, "select count(*) from public.pathway_guides where key = any(%s)", [g.key for g in b.guides]
    )
    actual["plan_template_days"] = one(
        cur, "select count(*) from public.plan_template_days where template_id = %s", b.plan.id
    )
    actual["sources"] = one(
        cur, "select count(*) from public.sources where id = any(%s)", [s.id for s in b.sources.accepted]
    )
    # questions-scope: seed (verifies the bundle's own ids only)
    actual["question_map_entries"] = one(
        cur,
        "select count(*) from public.questions q join jsonb_each(%s::jsonb) m on m.key = q.id"
        " where q.taxonomy_ids = array(select jsonb_array_elements_text(m.value))",
        json.dumps(b.question_map),
    )
    # Every placement the data has is there, unless its section is admin-edited (then its body decides, R3).
    actual["section_media"] = one(
        cur,
        "select count(*) from jsonb_to_recordset(%s::jsonb) as x(section_id text, media_id text)"
        " where exists (select 1 from public.chapter_sections s where s.id = x.section_id"
        "               and s.admin_edited_at is not null)"
        "    or exists (select 1 from public.section_media m where m.section_id = x.section_id"
        "               and m.media_id = x.media_id and m.origin = 'seed')",
        _payload(
            [
                {"section_id": s.id, "media_id": fid}
                for _c, _i, s in b.sections
                for fid in b.bodies.sections[s.id].figure_ids
            ]
        ),
    )
    actual["blueprint_targets"] = one(
        cur,
        "select count(*) from public.blueprint_targets where outline_version = %s",
        OUTLINE_VERSION,
    )
    actual["pack_flashcards"] = one(
        cur, "select count(*) from public.flashcards where id = any(%s::uuid[])", [card_uuid(c.id) for _p, c in b.cards]
    )
    actual["app_config"] = one(
        cur, "select count(*) from public.app_config where key = any(%s)", list(file_values(b.config_dir))
    )

    checks: list[Check] = []
    leftover = one(
        cur,
        "select count(*) from public.chapter_sections where id = any(%s)"
        " and (body::text like '%%[[figure:%%' or coalesce(body_html, '') like '%%[[figure:%%')",
        section_ids,
    )
    checks.append(Check(name="no [[figure: placeholder left", ok=leftover == 0, detail=f"{leftover} sections"))
    cur.execute(
        "select n.media_id from public.chapter_sections s"
        " cross join lateral jsonb_path_query(s.body, 'strict $.** ? (@.type == \"figure\").attrs.mediaId') as j(v)"
        " cross join lateral (select j.v #>> '{}' as media_id) n"
        " left join public.media_assets m on m.id = n.media_id and m.review_status = 'approved' and m.url is not null"
        " where s.id = any(%s) and m.id is null",
        (section_ids,),
    )
    dangling = sorted({r[0] for r in cur.fetchall()})
    checks.append(
        Check(
            name="every figure node resolves to an approved media_assets row",
            ok=not dangling,
            detail=", ".join(dangling[:10]) or "all resolve",
        )
    )
    # Sections an admin edited may hold the admin's own figures and placements; the 1:1 check covers the rest.
    figure_nodes = one(
        cur,
        "select count(*) from public.chapter_sections s"
        " cross join lateral jsonb_path_query(s.body, 'strict $.** ? (@.type == \"figure\")')"
        " where s.id = any(%s) and s.admin_edited_at is null",
        section_ids,
    )
    seed_placements = one(
        cur,
        "select count(*) from public.section_media m join public.chapter_sections s on s.id = m.section_id"
        " where m.section_id = any(%s) and m.origin = 'seed' and s.admin_edited_at is null",
        section_ids,
    )
    checks.append(
        Check(
            name="figure nodes = section_media rows",
            ok=figure_nodes == seed_placements,
            detail=f"{figure_nodes} nodes",
        )
    )
    cur.execute(
        "select m.id from public.media_assets m where m.id = any(%s) and not exists (select 1 from storage.objects o"
        " where o.bucket_id = split_part(m.url, '/', 1)"
        " and o.name = substr(m.url, length(split_part(m.url, '/', 1)) + 2))",
        (ids["media_assets"],),
    )
    missing_objects = sorted({r[0] for r in cur.fetchall()} - storage_pending)
    checks.append(
        Check(
            name="every figure's Storage object exists",
            ok=not missing_objects,
            detail=(", ".join(missing_objects[:10]) or "all present")
            + (f" ({len(storage_pending)} pending upload in a dry run)" if storage_pending else ""),
        )
    )
    # questions-scope: seed (verifies the bundle's own ids only)
    unpublished = one(
        cur,
        # an item an admin retired or sent back to review (admin_edited_at, migration 0036) is the admin's call;
        # otherwise the status is the bundle's (published, or retired by a pack / held back as draft in a pack)
        "select count(*) from public.questions q join jsonb_each_text(%s::jsonb) s on s.key = q.id"
        " where q.status <> s.value and q.admin_edited_at is null",
        json.dumps({q.id: q.status for q in b.questions}),
    )
    checks.append(
        Check(name="seeded questions have the bundle's status", ok=unpublished == 0, detail=f"{unpublished} differ")
    )
    # questions-scope: seed (verifies the bundle's own ids only)
    expert = one(
        cur,
        "select count(*) from public.questions where id = any(%s) and (review->>'humanExpertReviewed')::boolean",
        ids["questions"],
    )
    checks.append(Check(name="humanExpertReviewed stays false", ok=expert == 0, detail=f"{expert} marked reviewed"))
    # questions-scope: seed (verifies the bundle's own ids only)
    mock_blocks = one(
        cur,
        "select count(*) from public.questions q join public.chapters c on c.id = q.chapter_id"
        " where c.kind = 'mock_exam' and q.id = any(%s) and q.block is null",
        ids["questions"],
    )
    checks.append(
        Check(name="mock exam questions keep their block", ok=mock_blocks == 0, detail=f"{mock_blocks} without block")
    )
    # questions-scope: seed (verifies the bundle's own ids only)
    orphan_sources = one(
        cur,
        "select count(*) from (select source_id from public.chapters where id = any(%s) union all"
        " select source_id from public.questions where id = any(%s) union all"
        " select source_id from public.media_assets where id = any(%s)) a"
        " left join public.sources s on s.id = a.source_id where s.id is null",
        ids["chapters"],
        ids["questions"],
        ids["media_assets"],
    )
    checks.append(
        Check(
            name="every asset references a sources row", ok=orphan_sources == 0, detail=f"{orphan_sources} unresolved"
        )
    )
    unlinked = one(
        cur,
        "select count(*) from (select 1 from public.pathway_tasks t where t.id = any(%s) and t.official_url is not null"
        " and not exists (select 1 from public.sources s where s.id = t.source_id)) a",
        ids["pathway_tasks"],
    )
    checks.append(
        Check(
            name="every pathway link references a sources row",
            ok=unlinked == 0,
            detail=f"{unlinked} task links without a source",
        )
    )
    return actual, checks


def _scalar(cur: psycopg.Cursor[Any], query: LiteralString, *params: Any) -> int:
    cur.execute(query, params)
    row = cur.fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def expected_counts(b: SeedBundle) -> dict[str, int]:
    return {
        "chapters": len(b.chapters),
        "chapter_sections": len(b.sections),
        "questions": len(b.questions),
        "media_assets": len(b.figures),
        "taxonomy_nodes": len(b.taxonomy),
        "question_map_entries": len(b.question_map),
        "pathway_stages": len(b.stages),
        "pathway_tasks": len(b.tasks),
        "pathway_guides": len(b.guides),
        "plan_template_days": len(b.plan.days),
        "sources": len(b.sources.accepted),
        "question_sets": len(b.sets),
        "content_packs": len(b.packs),
        "blueprint_targets": len(b.outline.blueprint) if b.outline else 0,
        "pack_flashcards": len(b.cards),
        "section_media": sum(len(b.bodies.sections[s.id].figure_ids) for _c, _i, s in b.sections),
        "app_config": len(file_values(b.config_dir)),
        "video_topics": len(b.video_topics),
        "video_media_assets": len(b.videos),
        "video_catalog": len(b.videos),
    }


# ── driver ──


@contextmanager
def _advisory_lock(conn: psycopg.Connection[Any]) -> Iterator[None]:
    row = conn.execute("select pg_try_advisory_lock(%s)", (LOCK_KEY,)).fetchone()
    if not row or not row[0]:
        raise SeedLocked("another seed import is running against this database")
    try:
        yield
    finally:
        conn.execute("select pg_advisory_unlock(%s)", (LOCK_KEY,))


def run_seed(opts: SeedOptions, *, bundle: SeedBundle | None = None) -> SeedReport:
    """Run (or dry-run) the import and verify it. Raises SeedFailed (with the report) if verification fails."""
    started = time.monotonic()
    unknown = [g for g in opts.only if g not in GROUPS]
    if unknown:
        raise ValueError(f"unknown groups {unknown}; choose from {', '.join(GROUPS)}")
    check_targets(opts)
    b = bundle or load_bundle(opts.data_dir, opts.config_dir)
    figures = prepare_figures(b)
    report = SeedReport(
        dry_run=opts.dry_run,
        groups=list(opts.only),
        license_gate={
            "accepted": [s.id for s in b.sources.accepted],
            "rejected": [{"id": i, "reason": r} for i, r in b.sources.rejected],
        },
        svg={
            "fixed": sum(len(f.fixed) for f in figures),
            "removed": [f"{f.id}: {r}" for f in figures for r in f.removed],
        },
        expected=expected_counts(b),
    )
    storage_pending: set[str] = set()
    with psycopg.connect(opts.database_url, autocommit=True, connect_timeout=10, application_name="naadi-seed") as conn:
        with _advisory_lock(conn):
            outer = conn.transaction(force_rollback=True) if opts.dry_run else nullcontext()
            with outer:
                for group in GROUPS:
                    if group not in opts.only:
                        continue
                    t0 = time.monotonic()
                    with conn.transaction(), conn.cursor() as cur:
                        cur.execute(f"set local statement_timeout = {STATEMENT_TIMEOUT_MS}")
                        # Seed writes never count as admin edits (migration 0036 trigger reads this).
                        cur.execute("select set_config('naadi.seed', 'on', true)")
                        if group == "sources":
                            report.tables.update(_sources(cur, b))
                        elif group == "taxonomy":
                            report.tables.update(_taxonomy(cur, b))
                        elif group == "figures":
                            bucket_name = file_values(b.config_dir)["storage"]["content_public"]
                            if opts.storage is None:
                                raise SeedFailed("figures need a Storage target (SUPABASE_URL + service key)", report)
                            bucket = opts.storage.client().from_(bucket_name)
                            res = sync_objects(
                                bucket, FIGURE_FOLDER, [(f.name, f.data) for f in figures], dry_run=opts.dry_run
                            )
                            report.storage = StorageCounts(uploaded=len(res.uploaded), unchanged=len(res.unchanged))
                            if opts.dry_run:
                                storage_pending = {f.id for f in figures if f.name in set(res.uploaded)}
                            report.tables["media_assets"] = upsert(
                                cur,
                                "media_assets",
                                ["id"],
                                _media_rows(b, figures, bucket_name),
                                keep_existing=("attribution",),
                                guard=None if opts.force else ADMIN_EDIT_GUARD,
                            )
                        elif group == "chapters":
                            report.tables.update(_chapters(cur, b, force=opts.force))
                        elif group == "videos":
                            report.tables.update(_videos(cur, b, force=opts.force))
                        elif group == "questions":
                            report.tables.update(_questions(cur, b, force=opts.force))
                        elif group == "cards":
                            report.tables.update(_cards(cur, b))
                        elif group == "pathway":
                            report.tables.update(_pathway(cur, b))
                        elif group == "plan":
                            report.tables.update(_plan(cur, b))
                        elif group == "app_config":
                            report.tables.update(_app_config(cur, b, force=opts.force))
                    log.info("seed_group", group=group, dry_run=opts.dry_run, seconds=round(time.monotonic() - t0, 2))
                with conn.cursor() as cur:
                    actual, checks = verify(cur, b, storage_pending=storage_pending)
        if opts.storage is not None and not opts.dry_run and "figures" in opts.only:
            bucket = opts.storage.client().from_(file_values(b.config_dir)["storage"]["content_public"])
            from .storage import list_folder

            stored = list_folder(bucket, FIGURE_FOLDER)
            mismatched = sorted(f.id for f in figures if stored.get(f.name) != md5_hex(f.data))
            checks.append(
                Check(
                    name="Storage objects match the sanitised figures (MD5)",
                    ok=not mismatched,
                    detail=", ".join(mismatched[:10]) or f"{len(figures)} match",
                )
            )
    report.actual = actual
    counts = [
        Check(name=f"count {key}", ok=actual.get(key) == want, detail=f"{actual.get(key)} / {want}")
        for key, want in report.expected.items()
        if _group_of(key) in opts.only
    ]
    report.checks = counts + checks
    report.seconds = round(time.monotonic() - started, 2)
    # Every check, the counts included. License-gate rejections are not failures: those rows are skipped
    # and listed in report.license_gate (HANDOFF §7.1: nothing without a license is seeded).
    report.ok = all(c.ok for c in report.checks)
    if not report.ok:
        raise SeedFailed(
            "seed verification failed: " + "; ".join(f"{c.name} ({c.detail})" for c in report.checks if not c.ok),
            report,
        )
    return report


_COUNT_GROUP = {
    "chapters": "chapters",
    "chapter_sections": "chapters",
    "section_media": "chapters",
    "questions": "questions",
    "question_map_entries": "questions",
    "question_sets": "questions",
    "content_packs": "taxonomy",
    "blueprint_targets": "taxonomy",
    "pack_flashcards": "cards",
    "media_assets": "figures",
    "taxonomy_nodes": "taxonomy",
    "pathway_stages": "pathway",
    "pathway_tasks": "pathway",
    "pathway_guides": "pathway",
    "plan_template_days": "plan",
    "sources": "sources",
    "app_config": "app_config",
    "video_topics": "videos",
    "video_media_assets": "videos",
    "video_catalog": "videos",
}


def _group_of(count_key: str) -> str:
    return _COUNT_GROUP[count_key]
