"""Publishing gates (HANDOFF §7.1, `sources_policy` in app_config, config/sources.yaml header).

Every asset references a `sources` row. Before an admin publishes it, the asset's source must allow the use:

- `host`  : we store and serve a copy (figure upload, PDF, raster): the licence must exist and allow copying.
- `embed` : a YouTube video through the official IFrame player: the source must be a YouTube embed source, the
            video embeddable and its metadata fresh (`youtube_metadata_max_age_days`).
- `page`  : the asset appears in a chapter page: everything above for its kind, then NC/ND on a paid page and
            share-alike inside a proprietary composite (a Naadi chapter) are refused.
- `rag`   : the asset's text would go into prompts, RAG or embeddings: the source must allow AI ingest.

A refusal raises `GateRefused` with a stable reason code (the web shows `admin.gate.<reason>`). The decision is a
pure function of the source row, the asset and the policy (`decide`), so the whole matrix is unit-tested.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from psycopg import AsyncConnection

from ..config_api.schemas import SourcesPolicy

Use = Literal["host", "embed", "page", "rag"]
Reason = Literal[
    "no_source",
    "no_license",
    "copy_not_allowed",
    "noncommercial_on_paid",
    "share_alike_in_proprietary",
    "no_ai_ingest",
    "not_embeddable",
    "metadata_stale",
    "media_not_approved",
    "media_missing",
    "question_missing",
]

# Licence strings that name no licence at all, or defer to a per-file licence that a shared row cannot state.
NO_LICENSE = frozenset({"", "unknown", "none", "per-item", "per-file"})
SHARE_ALIKE = re.compile(r"(^|[-_ ])SA([-_ ]|$)", re.IGNORECASE)
YOUTUBE_LICENSE = "youtube-embed-only"


class GateRefused(Exception):
    """A publishing gate refused. HTTP 409 `gate_refused` with details `[{loc: ["gate", reason], msg: ref}]`."""

    def __init__(self, reason: Reason | str, ref_id: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.ref_id = ref_id


@dataclass(frozen=True)
class SourceTerms:
    id: str
    license: str
    allow_copy: bool
    allow_modify: bool
    allow_commercial: bool
    allow_ai_ingest: bool

    @classmethod
    def of(cls, row: Mapping[str, Any]) -> SourceTerms:
        return cls(
            id=str(row["id"]),
            license=str(row.get("license") or ""),
            allow_copy=bool(row.get("allow_copy")),
            allow_modify=bool(row.get("allow_modify")),
            allow_commercial=bool(row.get("allow_commercial")),
            allow_ai_ingest=bool(row.get("allow_ai_ingest")),
        )

    @property
    def licensed(self) -> bool:
        return self.license.strip().lower() not in NO_LICENSE

    @property
    def share_alike(self) -> bool:
        return bool(SHARE_ALIKE.search(self.license))

    @property
    def youtube(self) -> bool:
        return self.license.strip().lower() == YOUTUBE_LICENSE


@dataclass(frozen=True)
class AssetFacts:
    """What the gate needs to know about the asset (a `media_assets` row or a file about to become one)."""

    kind: str  # media_assets.kind
    embeddable: bool | None = None
    metadata_fetched_at: dt.datetime | None = None


@dataclass(frozen=True)
class PageFacts:
    """Where the asset is published. v1 has no paid pages (HANDOFF §5.4: video pages stay free)."""

    paid: bool = False
    proprietary: bool = True  # a Naadi chapter is a proprietary composite


HOSTED_KINDS = frozenset({"svg", "image", "pdf", "audio", "video_own"})


def decide(
    source: SourceTerms | None,
    asset: AssetFacts,
    use: Use,
    policy: SourcesPolicy,
    *,
    page: PageFacts | None = None,
    now: dt.datetime | None = None,
) -> Reason | None:
    """The first gate that refuses, or None. Pure: no I/O."""
    if source is None:
        return "no_source"
    if not source.licensed and policy.block_without_license:
        return "no_license"
    if use == "rag":
        return None if source.allow_ai_ingest or not policy.block_non_ai_ingest_from_rag else "no_ai_ingest"
    if asset.kind == "video_youtube":
        if not source.youtube:
            return "no_license"
        if asset.embeddable is not True:
            return "not_embeddable"
        fetched = asset.metadata_fetched_at
        max_age = dt.timedelta(days=policy.youtube_metadata_max_age_days)
        if fetched is None or (now or dt.datetime.now(dt.UTC)) - fetched > max_age:
            return "metadata_stale"
    elif asset.kind in HOSTED_KINDS and not source.allow_copy:
        return "copy_not_allowed"
    if use == "page":
        p = page or PageFacts()
        if p.paid and policy.block_noncommercial_on_paid and not (source.allow_commercial and source.allow_modify):
            return "noncommercial_on_paid"
        if p.proprietary and policy.block_share_alike_in_proprietary and source.share_alike:
            return "share_alike_in_proprietary"
    return None


async def load_policy(conn: AsyncConnection[Any]) -> SourcesPolicy:
    """`sources_policy` as stored (the app_config row), else the shipped defaults. Always the gate's own model,
    whose `block_without_license` and `block_non_ai_ingest_from_rag` are pinned to true."""
    cur = await conn.execute("select value from public.app_config where key = 'sources_policy'")
    row = await cur.fetchone()
    value = (row["value"] if isinstance(row, Mapping) else row[0]) if row else None
    try:
        return SourcesPolicy.model_validate(value) if isinstance(value, dict) else SourcesPolicy()
    except ValueError:
        return SourcesPolicy()


async def source_terms(conn: AsyncConnection[Any], source_id: str | None) -> SourceTerms | None:
    if not source_id:
        return None
    cur = await conn.execute(
        "select id, license, allow_copy, allow_modify, allow_commercial, allow_ai_ingest from public.sources"
        " where id = %s",
        (source_id,),
    )
    row = await cur.fetchone()
    return SourceTerms.of(dict(row)) if row else None


async def check_asset(
    conn: AsyncConnection[Any],
    source_id: str | None,
    *,
    use: Use,
    asset: AssetFacts,
    ref_id: str | None = None,
    page: PageFacts | None = None,
) -> None:
    """Raise GateRefused when `source_id` does not allow `use` of `asset`."""
    reason = decide(await source_terms(conn, source_id), asset, use, await load_policy(conn), page=page)
    if reason is not None:
        raise GateRefused(reason, ref_id)


async def check_media_row(
    conn: AsyncConnection[Any], media_id: str, *, use: Use, page: PageFacts | None = None, require_approved: bool
) -> None:
    """Gate an existing `media_assets` row (by id) for `use`; optionally it must already be approved."""
    cur = await conn.execute(
        "select id, kind, source_id, review_status, embeddable, metadata_fetched_at from public.media_assets"
        " where id = %s",
        (media_id,),
    )
    row = await cur.fetchone()
    if row is None:
        raise GateRefused("media_missing", media_id)
    m = dict(row)
    if require_approved and m["review_status"] != "approved":
        raise GateRefused("media_not_approved", media_id)
    await check_asset(
        conn,
        m["source_id"],
        use=use,
        asset=AssetFacts(m["kind"], m["embeddable"], m["metadata_fetched_at"]),
        ref_id=media_id,
        page=page,
    )
