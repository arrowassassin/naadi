"""S15 tutor tools: `add_to_knowledge` (a write behind the confirm) and the `pdf_page` kind of `get_page`.

`add_to_knowledge` hands a link or a file the student already uploaded to the ingest pipeline (segment 15): the
same quota, the same sensitivity gate and Vault prompt, the same placement confirm in the Library. The tool only
starts the run; nothing is placed until the student confirms on the Library page.

`get_page` on `pdf_page` ("{upload_id}:{page}") reads one page of the student's own PDF, only once the file has
passed the sensitivity gate (never a vaulted file, never a file still waiting for its gate) and only when the page
itself carries no personal-document signal. Both exist only while the `library` feature flag is on.
"""

from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, model_validator

from ...config_api.schemas import IngestRules
from ...config_api.service import AppConfigService
from ...ingest import store as st
from ...ingest.routes import IngestError, check_startable, create_link, read_page_text, start_run
from ...ingest.sensitivity import scan
from ...settings import get_settings
from .executor import ToolRefused
from .registry import (
    Proposal,
    ToolContext,
    ToolResult,
    WriteTool,
    register_page_resolver,
    register_tool,
    web_url_key,
)
from .untrusted import frame

FLAG = "library"
# Item states past the sensitivity gate whose text the pipeline extracted (received/vaulted/failed are not).
GATED = frozenset({"needs_confirmation", "placing", "ready"})
LABEL_MAX = 120


async def _rules(ctx: ToolContext) -> IngestRules:
    svc = AppConfigService(ctx.db, get_settings().config_dir)
    rules = IngestRules.model_validate(await svc.get("ingest"))
    if not rules.enabled:
        raise ToolRefused("ingest_disabled")
    return rules


class AddToKnowledgeArgs(BaseModel):
    url: str | None = Field(
        default=None,
        min_length=8,
        max_length=2048,
        pattern=r"^https://",
        description="An https link to add (article, online PDF or YouTube video)",
    )
    upload_id: uuid.UUID | None = Field(
        default=None, description="The id of a file the student uploaded that has not been processed yet"
    )

    @model_validator(mode="after")
    def _one(self) -> AddToKnowledgeArgs:
        if (self.url is None) == (self.upload_id is None):
            raise ValueError("give exactly one of url or upload_id")
        return self


async def _owned_upload(ctx: ToolContext, upload_id: uuid.UUID) -> dict[str, Any]:
    """The student's upload and its item, read as the student (RLS); a vaulted file is never offered."""
    async with ctx.user_tx() as conn:
        cur = await conn.execute(
            "select u.id, u.original_name, u.kind, u.status, i.id as item_id, i.status as item_status, i.run_id"
            " from public.uploads u left join public.ingest_items i on i.upload_id = u.id"
            " where u.id = %s order by i.created_at limit 1",
            (upload_id,),
        )
        row = await cur.fetchone()
    if row is None or row["item_id"] is None or row["status"] == "vaulted":
        raise ToolRefused("upload_not_found")
    if row["item_status"] != "received" or row["run_id"]:
        raise ToolRefused("already_added")
    return dict(row)


async def _add_prepare(ctx: ToolContext, args: AddToKnowledgeArgs) -> Proposal:
    """Add a link or a file the student uploaded to their knowledge base. Naadi reads it in the background and the
    student chooses where it goes on the Library page. Give exactly one of `url` or `upload_id`; the student
    confirms first."""
    if args.url is not None and web_url_key(args.url) not in ctx.web_urls:
        # Only a link the student typed this turn or one web_search returned: a URL written inside a document,
        # a page or a search snippet must not be able to make the tutor propose adding it (review15 #3).
        raise ToolRefused("url_not_from_student")
    await _rules(ctx)
    if args.url is not None:
        host = urlsplit(args.url).hostname or ""
        if not host:
            raise ToolRefused("invalid_url")
        link: dict[str, Any] = {"kind": "knowledge", "source": "link", "label": args.url[:LABEL_MAX], "host": host}
        return Proposal(preview=link, editable=False, data={"url": args.url})
    assert args.upload_id is not None
    row = await _owned_upload(ctx, args.upload_id)
    name = (row["original_name"] or row["kind"] or "file")[:LABEL_MAX]
    preview: dict[str, Any] = {"kind": "knowledge", "source": "file", "label": name, "host": None}
    return Proposal(preview=preview, editable=False, data={"item_id": str(row["item_id"])})


def _started(item_id: uuid.UUID) -> ToolResult:
    return ToolResult(
        content=(
            "Started: Naadi is reading it in the background. Tell the student they choose where it goes on the"
            " Library page (Learn → Library) once it is read; nothing is placed before that."
        ),
        blocks=[
            {
                "type": "actions",
                "data": {
                    "items": [
                        {
                            "action": "open_library",
                            "label_key": "agent.action.open_library",
                            "payload": {"item_id": str(item_id)},
                        }
                    ]
                },
            }
        ],
    )


async def _add_apply(ctx: ToolContext, args: AddToKnowledgeArgs, proposal: Proposal) -> ToolResult:
    rules = await _rules(ctx)
    settings = get_settings()
    try:
        if "url" in proposal.data:
            item_id = await create_link(ctx.db, settings, rules, ctx.user_id, proposal.data["url"], None)
        else:
            item_id = uuid.UUID(proposal.data["item_id"])
            async with st.service_tx(ctx.db) as conn:
                row = await st.get_item(conn, item_id, ctx.user_id)
            if row is None:
                raise ToolRefused("upload_not_found")
            await check_startable(settings, rules, row)
        await start_run(
            ctx.db, settings, ctx.user_id, ctx.is_admin, item_id, {"item_id": str(item_id), "mode": "start"}
        )
    except IngestError as exc:
        # The quota, size and state codes the Library shows; the model reads only the code.
        if exc.status >= 500:
            return ToolResult(content=f"[error: {exc.code}]", status="error")
        return ToolResult(content=f"[refused: {exc.code}]", status="refused")
    return _started(item_id)


register_tool("add_to_knowledge", WriteTool(_add_prepare, _add_apply), writes=True, requires_flag=FLAG)


# ── get_page: pdf_page ──


def _parse_page_id(ref_id: str) -> tuple[uuid.UUID, int]:
    upload_s, sep, page_s = ref_id.partition(":")
    try:
        upload_id, page = uuid.UUID(upload_s), int(page_s)
    except ValueError as exc:
        raise ToolRefused("not_found") from exc
    if not sep or not 1 <= page <= 10_000:
        raise ToolRefused("not_found")
    return upload_id, page


async def pdf_page(ctx: ToolContext, ref_id: str) -> ToolResult:
    """One page of the student's own PDF, with its citation `{upload_id, page}`."""
    upload_id, page = _parse_page_id(ref_id)
    async with ctx.user_tx() as conn:  # RLS: only the student's own upload is visible
        cur = await conn.execute(
            "select u.original_name, u.kind, u.status, i.status as item_status"
            " from public.uploads u left join public.ingest_items i on i.upload_id = u.id"
            " where u.id = %s order by i.created_at limit 1",
            (upload_id,),
        )
        row = await cur.fetchone()
    if row is None or row["kind"] != "pdf" or row["status"] == "vaulted":
        raise ToolRefused("not_found")
    if row["item_status"] not in GATED:
        # Not through the sensitivity gate yet (or it went to the Vault): its text never reaches a model.
        raise ToolRefused("not_processed")
    out = await read_page_text(ctx.db, get_settings(), ctx.user_id, upload_id, page)
    if out is None:
        raise ToolRefused("not_found")
    if scan(out.text).personal:
        raise ToolRefused("sensitive_page")
    limit = max(200, ctx.tool_result_max_chars - 200)
    text = out.text if len(out.text) <= limit else out.text[: limit - 1].rstrip() + "…"
    name = (row["original_name"] or "Your PDF")[:LABEL_MAX]
    cite = {
        "id": f"kb:pdf_page:{upload_id}:{page}",
        "title": f"{name}, page {page}",
        "source": "Your file",
        "layer": "notes",
        "url": None,
        "target": {"type": "pdf_page", "upload_id": str(upload_id), "page": str(page)},
        "license": None,
    }
    return ToolResult(content=f"PDF page {page} of {out.pages}:\n" + frame(text, source="pdf_page"), citations=[cite])


register_page_resolver("pdf_page", pdf_page, requires_flag=FLAG)

__all__ = ["AddToKnowledgeArgs", "pdf_page"]
