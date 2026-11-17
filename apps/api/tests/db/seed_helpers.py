"""Shared setup for the seed importer's db tests: options against the local stack, and a full clean-up.

The seed writes real rows and real Storage objects into the shared test database, so every test module that
seeds removes everything it wrote afterwards (other suites, e.g. the migration round trip, need empty
buckets and no seed rows).
"""

from __future__ import annotations

from pathlib import Path

import psycopg

from naadi_api.seed.data import SeedBundle, load_bundle
from naadi_api.seed.importer import FIGURE_FOLDER, SeedOptions
from naadi_api.seed.storage import StorageTarget, list_folder

from .storage import local_service_key, require_local_storage

REPO = Path(__file__).resolve().parents[4]
DATA, CONFIG = REPO / "data", REPO / "config"
BUCKET = "content-public"


def bundle() -> SeedBundle:
    return load_bundle(DATA, CONFIG)


def storage() -> StorageTarget:
    return StorageTarget(key=local_service_key(), storage_url=require_local_storage())


def options(url: str, **over: object) -> SeedOptions:
    base: dict[str, object] = {"database_url": url, "data_dir": DATA, "config_dir": CONFIG}
    base.update(over)
    if "storage" not in over:
        base["storage"] = storage()
    return SeedOptions(**base)  # type: ignore[arg-type]


def remove_seed(url: str, b: SeedBundle) -> None:
    """Delete every row and Storage object the seed creates (idempotent)."""
    bucket = storage().client().from_(BUCKET)
    names = list(list_folder(bucket, FIGURE_FOLDER))
    if names:
        bucket.remove([f"{FIGURE_FOLDER}/{n}" for n in names])
    sections = [s.id for _c, _i, s in b.sections]
    with psycopg.connect(url, autocommit=True) as c, c.transaction():
        c.execute("delete from public.seed_runs")
        c.execute("delete from public.section_media where section_id = any(%s)", (sections,))
        c.execute("delete from public.chapter_section_revisions where section_id = any(%s)", (sections,))
        c.execute("delete from public.questions where id = any(%s)", ([q.id for q in b.questions],))
        c.execute("delete from public.chapter_sections where id = any(%s)", (sections,))
        c.execute("delete from public.chapters where id = any(%s)", ([ch.id for ch in b.chapters],))
        c.execute("delete from public.media_assets where id = any(%s)", ([f.id for f in b.figures],))
        c.execute("delete from public.plan_templates where id = %s", (b.plan.id,))
        c.execute("delete from public.pathway_guides where key = any(%s)", ([g.key for g in b.guides],))
        c.execute("delete from public.pathway_tasks where id = any(%s)", ([t.id for t in b.tasks],))
        c.execute(
            "update public.profiles set pathway_stage = null where pathway_stage = any(%s)",
            ([s.key for s in b.stages],),
        )
        c.execute("delete from public.pathway_stages where key = any(%s)", ([s.key for s in b.stages],))
        c.execute("delete from public.taxonomy_nodes where id = any(%s)", ([t.id for t in b.taxonomy],))
        keys = c.execute("select key from public.app_config").fetchall()
        from naadi_api.config_api.schemas import KEY_SPECS

        seeded = [k for (k,) in keys if k in KEY_SPECS]
        c.execute("delete from public.app_config where key = any(%s)", (seeded,))
        c.execute("delete from public.app_config_history where key = any(%s)", (list(KEY_SPECS),))
        # `user-provided` is created by migration 0028 (private chapters reference it); the seed only keeps it equal.
        c.execute(
            "delete from public.sources where id = any(%s)",
            ([s.id for s in b.sources.accepted if s.id != "user-provided"],),
        )
