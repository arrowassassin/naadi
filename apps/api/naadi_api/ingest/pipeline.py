"""The universal ingest pipeline: framework-free steps the `ingest` graph runs one by one (segment 15).

    receive   -> sniff from bytes, caps, container guards, SHA-256 exact duplicate, ZIP bundles fan out
    gate      -> local extraction in the sandbox + the sensitivity rules (no model has been called yet)
    [vault?]  -> interrupt: "Store in your Vault?" (vault: object moved, vault_documents row, run ends)
    extract   -> figures uploaded (owned WebP), scanned pages/photos through the vision role (pages with an
                 identifier never), identifiers redacted (second rules pass), extract saved (server-only)
    understand-> one structured call (or local rules)
    propose   -> near-duplicate check, matching sections, every registered placement's candidates
    [confirm] -> interrupt: the confirm card (grouped per batch in the Library)
    place     -> the chosen handler writes inside one transaction; what it wrote is recorded for Undo
    index     -> local embeddings for what was placed
    report    -> ready (or failed with a reason code; retry restarts from receive)

Every step reads the item fresh from the database, so a step can be re-run after a crash. Text, file names and
document fragments are never logged: log lines carry the item id, kinds, counts and reason codes.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from ..config_api.schemas import IngestRules, StorageBuckets
from ..db import Database
from ..embeddings.engine import Engine
from ..llm import LLMUnavailable
from ..llm_budget import BudgetExhausted
from ..logs import get_logger
from . import errors, index, sensitivity
from . import store as st
from .errors import IngestRejected
from .extract import extract_file, extract_text, render_pages
from .extract.model import ExtractedDoc, Section
from .extract.structure import clean_text, sections_from_pages
from .kinds import KIND_MIME, VAULT_MAX_BYTES, VAULT_MIMES
from .placements import Candidate, IngestView, PlacementContext, PlacementRecord, handler_for, propose_all
from .secure import MB, check_size, inspect_container, list_bundle, read_member
from .sniff import sniff
from .storage import Storage, StorageError, StorageTooLarge, asset_path, upload_path, vault_path
from .understand import Understanding, understand
from .vision import UnreadableReply, read_image

log = get_logger("naadi.ingest")

USER_SOURCE_ID = "user-provided"
CONTAINER_KINDS = frozenset({"docx", "pptx", "xlsx", "epub", "anki"})
VISION_IMAGE_WORDS = 40  # photos with fewer OCR words go to the vision role (handwriting, diagrams)


@dataclass
class Deps:
    db: Database
    storage: Storage
    gateway: Any
    buckets: StorageBuckets
    rules: Callable[[], Awaitable[IngestRules]]
    flags_for: Callable[[uuid.UUID], Awaitable[dict[str, bool]]]
    search: Any = None
    engine: Callable[[], Engine | None] = index.engine_or_none
    start_child: Callable[[uuid.UUID, uuid.UUID], Awaitable[None]] | None = None


@dataclass
class Outcome:
    """What a step decided; the graph routes on `next`."""

    next: str
    detail: dict[str, Any] = field(default_factory=dict)


def _ext_for(mime: str) -> str:
    return {
        "application/pdf": "pdf",
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/webp": "webp",
        "image/heic": "heic",
        "image/heif": "heif",
    }.get(mime, "bin")


async def _fail(deps: Deps, item_id: uuid.UUID, user_id: uuid.UUID, code: str, **detail: Any) -> Outcome:
    async with st.service_tx(deps.db) as conn:
        row = await st.get_item(conn, item_id, user_id)
        await st.set_status(conn, item_id, user_id, "failed", error=code, detail={"failed": detail} if detail else None)
        if row and row["upload_id"]:
            await st.set_upload(conn, row["upload_id"], user_id, status="failed")
    log.info("ingest.failed", item_id=str(item_id), reason=code)
    return Outcome("end", {"error": code})


async def guarded(
    deps: Deps, item_id: uuid.UUID, user_id: uuid.UUID, step: Callable[[], Awaitable[Outcome]]
) -> Outcome:
    """Run one step; a rejection or an unexpected error marks the item failed with a reason code."""
    try:
        return await step()
    except IngestRejected as exc:
        return await _fail(deps, item_id, user_id, exc.code, **exc.detail)
    except StorageTooLarge:
        return await _fail(deps, item_id, user_id, errors.FILE_TOO_LARGE)
    except StorageError:
        return await _fail(deps, item_id, user_id, errors.UPLOAD_MISSING)
    except (psycopg.OperationalError, TimeoutError):
        raise  # the run fails and the item stays at its stage; Retry restarts it
    except Exception as exc:  # noqa: BLE001 - never leak details; the reason code is what the student sees
        log.error("ingest.step_error", item_id=str(item_id), error=type(exc).__name__)
        return await _fail(deps, item_id, user_id, errors.INTERNAL)


# ── 1. receive & secure ──


async def _original(deps: Deps, row: Mapping[str, Any], rules: IngestRules) -> bytes:
    path = row["storage_path"]
    if not path:
        raise IngestRejected(errors.UPLOAD_MISSING)
    return await deps.storage.download(deps.buckets.user_uploads, path, rules.max_file_mb * MB)


async def receive(deps: Deps, item_id: uuid.UUID, user_id: uuid.UUID) -> Outcome:
    rules = await deps.rules()
    async with st.service_tx(deps.db) as conn:
        row = await st.get_item(conn, item_id, user_id)
        if row is None:
            return Outcome("end", {"error": "gone"})
        await st.prune_extracts(conn, user_id, rules.undo_days)
        await st.set_status(conn, item_id, user_id, "received")
    if row["input_kind"] in ("link", "paste"):
        return Outcome("gate")
    data = await _original(deps, row, rules)
    check_size(len(data), rules)
    sniffed = sniff(data, row["original_name"])
    if sniffed.kind in CONTAINER_KINDS:
        inspect_container(data, max_ratio=rules.zip_max_ratio)
    sha = hashlib.sha256(data).hexdigest()
    async with st.service_tx(deps.db) as conn:
        await st.set_status(
            conn,
            item_id,
            user_id,
            "received",
            input_kind=sniffed.kind,
            sha256=sha,
            bytes=len(data),
            detected_kind=sniffed.kind,
        )
        await st.set_upload(
            conn,
            row["upload_id"],
            user_id,
            kind=sniffed.kind,
            sha256=sha,
            mime=sniffed.mime,
            bytes=len(data),
            status="processing",
        )
        dup = await st.exact_duplicate(conn, user_id, sha, item_id)
        if dup is not None:
            await st.set_status(conn, item_id, user_id, "ready", duplicate_of=dup, detail={"duplicate": "exact"})
            await st.set_upload(conn, row["upload_id"], user_id, status="ready")
    if dup is not None:
        # The second copy is not kept: the earlier item already holds the file.
        await deps.storage.remove(deps.buckets.user_uploads, [row["storage_path"]])
        async with st.service_tx(deps.db) as conn:
            await st.set_upload(conn, row["upload_id"], user_id, storage_path=None)
        log.info("ingest.duplicate", item_id=str(item_id), kind="exact")
        return Outcome("end", {"duplicate_of": str(dup)})
    if sniffed.kind == "zip":
        return await _bundle(deps, row, data, rules)
    return Outcome("gate")


async def _bundle(deps: Deps, row: Mapping[str, Any], data: bytes, rules: IngestRules) -> Outcome:
    """A ZIP: every member becomes its own item in the same batch (depth-limited), each with its own run."""
    item_id, user_id = row["id"], row["user_id"]
    depth = int((row["stage_detail"] or {}).get("depth", 0)) + 1
    if depth > rules.zip_max_depth:
        raise IngestRejected(errors.ZIP_TOO_DEEP, limit=rules.zip_max_depth)
    members = list_bundle(data, rules)
    batch = row["batch_id"] or item_id
    children: list[uuid.UUID] = []
    for m in members:
        blob = read_member(data, m, rules.max_file_mb * MB)
        try:
            kind = sniff(blob, m.name)
        except IngestRejected:
            continue  # an unsupported or dangerous member is skipped; the bundle reports how many were
        child, upload = uuid.uuid4(), uuid.uuid4()
        path = upload_path(user_id, child)
        mime = KIND_MIME.get(kind.kind, kind.mime)
        await deps.storage.upload(deps.buckets.user_uploads, path, blob, mime)
        async with st.service_tx(deps.db) as conn:
            await conn.execute(
                "insert into public.uploads (id, user_id, kind, storage_path, original_name, bytes, mime, status)"
                " values (%s, %s, %s, %s, %s, %s, %s, 'uploaded')",
                (upload, user_id, kind.kind, path, m.name.rsplit("/", 1)[-1][:255], len(blob), mime),
            )
            await conn.execute(
                "insert into public.ingest_items (id, user_id, upload_id, input_kind, batch_id, status, stage_detail)"
                " values (%s, %s, %s, %s, %s, 'received', %s)",
                (child, user_id, upload, kind.kind, batch, Jsonb({"depth": depth, "parent": str(item_id)})),
            )
        children.append(child)
    async with st.service_tx(deps.db) as conn:
        await st.set_status(
            conn,
            item_id,
            user_id,
            "ready",
            detail={"bundle": {"files": len(children), "skipped": len(members) - len(children)}},
        )
        await conn.execute("update public.ingest_items set batch_id = %s where id = %s", (batch, item_id))
        await st.set_upload(conn, row["upload_id"], user_id, status="ready")
    if deps.start_child is not None:
        for child in children:
            await deps.start_child(child, user_id)
    log.info("ingest.bundle", item_id=str(item_id), files=len(children), skipped=len(members) - len(children))
    return Outcome("end", {"children": [str(c) for c in children]})


# ── 2. local extraction + sensitivity gate ──


async def _local(deps: Deps, row: Mapping[str, Any], rules: IngestRules) -> tuple[ExtractedDoc, dict[str, bytes]]:
    kind = row["input_kind"]
    if kind == "paste":
        async with st.service_tx(deps.db) as conn:
            doc = await st.load_extract(conn, row["id"], row["user_id"])
        if doc is None:
            raise IngestRejected(errors.NO_TEXT)
        return doc, {}
    if kind == "link":
        return await _fetch_link(deps, row), {}
    data = await _original(deps, row, rules)
    return await extract_file(data, kind, row["original_name"], rules)


async def _fetch_link(deps: Deps, row: Mapping[str, Any]) -> ExtractedDoc:
    from ..search import TavilyGuardExhausted, youtube_id

    url = str(row["kb_url"] or "")
    if youtube_id(url):
        if deps.search is None:
            raise IngestRejected(errors.URL_FETCH_FAILED)
        v = await deps.search.youtube(url)
        if v is None:
            raise IngestRejected(errors.URL_FETCH_FAILED)
        return ExtractedDoc(kind="link", title=str(v.title or "")[:300], url=url)
    if deps.search is None:
        raise IngestRejected(errors.URL_FETCH_FAILED)
    try:
        res = await deps.search.extract(url, user_id=row["user_id"])
    except TavilyGuardExhausted as exc:
        raise IngestRejected(errors.WEB_BUDGET) from exc
    if res is None or not res.text.strip():
        raise IngestRejected(errors.URL_FETCH_FAILED)
    doc = extract_text(res.text, "link", res.title or "")
    return doc.model_copy(update={"url": url})


def _page_hits(doc: ExtractedDoc) -> tuple[sensitivity.Sensitivity, set[int]]:
    total = sensitivity.Sensitivity()
    pages: set[int] = set()
    if doc.pages:
        for p in doc.pages:
            s = sensitivity.scan(p.text)
            if s.personal:
                pages.add(p.number)
            total.merge(s)
    total.merge(sensitivity.scan(doc.full_text()))
    for qa in doc.qa_pairs[:200]:
        total.merge(sensitivity.scan(f"{qa.front}\n{qa.back}"))
    return total, pages


async def gate(deps: Deps, item_id: uuid.UUID, user_id: uuid.UUID) -> Outcome:
    rules = await deps.rules()
    async with st.service_tx(deps.db) as conn:
        row = await st.get_item(conn, item_id, user_id)
        if row is None:
            return Outcome("end", {"error": "gone"})
        await st.set_status(conn, item_id, user_id, "extracting")
    doc, _files = await _local(deps, row, rules)
    hit, _pages = _page_hits(doc)
    if hit.personal:
        async with st.service_tx(deps.db) as conn:
            await st.set_status(
                conn,
                item_id,
                user_id,
                "needs_confirmation",
                sensitivity="personal",
                detail={"gate": {"signals": hit.signals, "doc_type": hit.doc_type}, "stage": "vault"},
            )
        log.info("ingest.gate", item_id=str(item_id), personal=True, signals=len(hit.signals))
        return Outcome("ask_vault", {"signals": hit.signals, "doc_type": hit.doc_type})
    log.info("ingest.gate", item_id=str(item_id), personal=False)
    # Nothing personal: carry on with what was just extracted (no second pass over the file).
    return await _finish_extract(deps, row, doc, _files, rules)


async def vault(deps: Deps, item_id: uuid.UUID, user_id: uuid.UUID, decision: str) -> Outcome:
    """The student's answer to the Vault prompt: `vault`, `continue` (not personal) or `discard`."""
    async with st.service_tx(deps.db) as conn:
        row = await st.get_item(conn, item_id, user_id)
    if row is None:
        return Outcome("end", {"error": "gone"})
    if decision == "continue":
        async with st.service_tx(deps.db) as conn:
            await st.set_status(
                conn,
                item_id,
                user_id,
                "extracting",
                sensitivity="normal",
                detail={"stage": "extract", "gate_override": True},
            )
        return Outcome("extract", {"override": True})
    path = row["storage_path"]
    if decision == "discard":
        if path:
            await deps.storage.remove(deps.buckets.user_uploads, [path])
        async with st.service_tx(deps.db) as conn:
            await st.set_status(conn, item_id, user_id, "failed", error=errors.DISCARDED)
            if row["upload_id"]:
                await st.set_upload(conn, row["upload_id"], user_id, status="failed", storage_path=None)
        return Outcome("end", {"discarded": True})
    # vault
    if row["input_kind"] in ("link", "paste") or not path:
        return await _fail(deps, item_id, user_id, errors.VAULT_TYPE)
    mime = row["mime"] or ""
    if mime not in VAULT_MIMES:
        return await _fail(deps, item_id, user_id, errors.VAULT_TYPE)
    if (row["bytes"] or 0) > VAULT_MAX_BYTES:
        return await _fail(deps, item_id, user_id, errors.VAULT_TOO_LARGE)
    data = await deps.storage.download(deps.buckets.user_uploads, path, VAULT_MAX_BYTES)
    target = vault_path(user_id, item_id, _ext_for(mime))
    await deps.storage.upload(deps.buckets.vault, target, data, mime)
    del data
    await deps.storage.remove(deps.buckets.user_uploads, [path])
    detail = (row["stage_detail"] or {}).get("gate") or {}
    async with st.service_tx(deps.db) as conn:
        await conn.execute(
            "insert into public.vault_documents (user_id, doc_type, title, storage_path) values (%s, %s, %s, %s)",
            (
                user_id,
                detail.get("doc_type"),
                (row["original_name"] or "")[:200] or None,
                f"{deps.buckets.vault}/{target}",
            ),
        )
        await st.set_status(conn, item_id, user_id, "vaulted", detail={"stage": "vaulted"})
        await st.set_upload(conn, row["upload_id"], user_id, status="vaulted", storage_path=None)
        await conn.execute("delete from public.ingest_extracts where item_id = %s", (item_id,))
    log.info("ingest.vaulted", item_id=str(item_id))
    return Outcome("end", {"vaulted": True})


# ── 3. extract (after the gate) ──


async def _vision_budget(deps: Deps, user_id: uuid.UUID, rules: IngestRules) -> int:
    async with st.service_tx(deps.db) as conn:
        used = await st.vision_calls_today(conn, user_id)
    return max(0, rules.vision_per_user_daily - used)


async def extract(deps: Deps, item_id: uuid.UUID, user_id: uuid.UUID) -> Outcome:
    rules = await deps.rules()
    async with st.service_tx(deps.db) as conn:
        row = await st.get_item(conn, item_id, user_id)
        if row is None:
            return Outcome("end", {"error": "gone"})
        await st.set_status(conn, item_id, user_id, "extracting", progress=30)
    doc, files = await _local(deps, row, rules)
    return await _finish_extract(deps, row, doc, files, rules)


async def _finish_extract(
    deps: Deps, row: Mapping[str, Any], doc: ExtractedDoc, files: dict[str, bytes], rules: IngestRules
) -> Outcome:
    item_id, user_id = row["id"], row["user_id"]
    hit, hit_pages = _page_hits(doc)
    redactions = hit.redactions  # non-empty only when the student said the file is not personal
    kind = row["input_kind"]
    # Owned assets (figures, the converted photo, a thumbnail) go to the student's folder.
    for fig in doc.figures:
        blob = files.get(fig.key)
        if blob is None:
            continue
        path = asset_path(user_id, item_id, fig.key)
        await deps.storage.upload(deps.buckets.user_uploads, path, blob, "image/webp")
        fig.storage_path = f"{deps.buckets.user_uploads}/{path}"
    if doc.media.thumbnail and doc.media.thumbnail in files:
        await deps.storage.upload(
            deps.buckets.user_uploads, asset_path(user_id, item_id, "thumb"), files[doc.media.thumbnail], "image/webp"
        )
    big_original = (row["bytes"] or 0) > rules.keep_image_original_kb * 1024
    if kind == "image" and row["storage_path"] and "image" in files and big_original:
        # The WebP replaces the original unless the original is small (Storage budget).
        await deps.storage.remove(deps.buckets.user_uploads, [row["storage_path"]])
        async with st.service_tx(deps.db) as conn:
            await st.set_upload(conn, row["upload_id"], user_id, storage_path=asset_path(user_id, item_id, "image"))
    if kind == "audio" and not rules.audio_keep and row["storage_path"]:
        await deps.storage.remove(deps.buckets.user_uploads, [row["storage_path"]])
        async with st.service_tx(deps.db) as conn:
            await st.set_upload(conn, row["upload_id"], user_id, storage_path=None)
    if kind == "video" and row["storage_path"]:
        # Own video files: metadata + thumbnail only (Storage is 1 GB).
        await deps.storage.remove(deps.buckets.user_uploads, [row["storage_path"]])
        async with st.service_tx(deps.db) as conn:
            await st.set_upload(conn, row["upload_id"], user_id, storage_path=None)
    doc = await _vision(deps, row, doc, files, hit_pages, rules)
    if redactions:
        doc = _redact(doc, redactions)
    if not doc.full_text().strip() and not doc.qa_pairs and kind not in ("video", "image", "link"):
        raise IngestRejected(errors.NO_TEXT)
    async with st.service_tx(deps.db) as conn:
        await st.save_extract(conn, item_id, user_id, doc)
        await st.set_status(
            conn,
            item_id,
            user_id,
            "extracting",
            progress=55,
            pages=doc.page_count or None,
            detail={"stage": "understand", "counts": _counts(doc)},
        )
    return Outcome("understand")


def _counts(doc: ExtractedDoc) -> dict[str, Any]:
    return {
        "pages": doc.page_count,
        "words": doc.word_count,
        "sections": len(doc.sections),
        "figures": len(doc.figures),
        "qa_pairs": len(doc.qa_pairs),
        "mcqs": len(doc.mcqs),
        "scanned": len(doc.scanned_pages()),
        "duration_s": doc.media.duration_s,
    }


async def _vision(
    deps: Deps,
    row: Mapping[str, Any],
    doc: ExtractedDoc,
    files: dict[str, bytes],
    hit_pages: set[int],
    rules: IngestRules,
) -> ExtractedDoc:
    kind = row["input_kind"]
    user_id, item_id = row["user_id"], row["id"]
    budget = min(rules.max_vision_pages, await _vision_budget(deps, user_id, rules))
    if budget <= 0:
        return doc
    if kind == "image" and "image" in files and 1 not in hit_pages:
        words = len(doc.full_text().split())
        if words < VISION_IMAGE_WORDS or doc.handwriting_likely:
            try:
                vt = await read_image(
                    deps.gateway, files["image"], mime="image/webp", kind="image", user_id=user_id, item_id=item_id
                )
            except (BudgetExhausted, LLMUnavailable, UnreadableReply) as exc:
                log.info("ingest.vision_skipped", item_id=str(item_id), reason=type(exc).__name__)
                return doc
            text = clean_text(vt.text)
            figures = list(doc.figures)
            if figures and vt.caption:
                figures[0] = figures[0].model_copy(update={"caption": vt.caption})
            title = row["original_name"] or ""
            return doc.model_copy(
                update={
                    "pages": [doc.pages[0].model_copy(update={"text": text, "source": "vision"})]
                    if text
                    else doc.pages,
                    "sections": [Section(heading=title[:300], text=text)] if text else doc.sections,
                    "figures": figures,
                    "handwriting_likely": vt.handwritten or doc.handwriting_likely,
                }
            )
        return doc
    if kind != "pdf":
        return doc
    # Scanned pages that local OCR read poorly, never a page where the gate found an identifier.
    want = [p.number for p in doc.pages if p.scanned and p.number not in hit_pages and len(p.text.split()) < 120]
    want = want[:budget]
    if not want:
        return doc
    data = await _original(deps, row, rules)
    renders = await render_pages(data, want)
    del data
    pages = {p.number: p for p in doc.pages}
    changed = False
    for n in want:
        png = renders.get(n)
        if png is None:
            continue
        try:
            vt = await read_image(deps.gateway, png, mime="image/png", kind="page", user_id=user_id, item_id=item_id)
        except (BudgetExhausted, LLMUnavailable) as exc:
            log.info("ingest.vision_stopped", item_id=str(item_id), reason=type(exc).__name__)
            break
        except UnreadableReply:
            continue
        text = clean_text(vt.text)
        if text and (vt.handwritten or len(text) > len(pages[n].text)):
            pages[n] = pages[n].model_copy(update={"text": text, "source": "vision"})
            changed = True
        async with st.service_tx(deps.db) as conn:
            await st.set_progress(conn, item_id, user_id, 30 + int(25 * (want.index(n) + 1) / len(want)))
    if not changed:
        return doc
    ordered = [pages[k] for k in sorted(pages)]
    outline = [(1, s.heading, s.page_start) for s in doc.sections if s.page_start] if len(doc.sections) > 1 else []
    sections = sections_from_pages(ordered, doc.title or (row["original_name"] or ""), outline)
    return doc.model_copy(update={"pages": ordered, "sections": sections})


def _redact(doc: ExtractedDoc, values: set[str]) -> ExtractedDoc:
    r = sensitivity.redact
    return doc.model_copy(
        update={
            "title": r(doc.title, values),
            "pages": [p.model_copy(update={"text": r(p.text, values)}) for p in doc.pages],
            "sections": [
                s.model_copy(update={"text": r(s.text, values), "heading": r(s.heading, values)}) for s in doc.sections
            ],
            "qa_pairs": [
                q.model_copy(update={"front": r(q.front, values), "back": r(q.back, values)}) for q in doc.qa_pairs
            ],
            "mcqs": [
                m.model_copy(
                    update={
                        "stem": r(m.stem, values),
                        "explanation": r(m.explanation, values),
                        "options": [r(o, values) for o in m.options],
                    }
                )
                for m in doc.mcqs
            ],
            "tables": [
                t.model_copy(update={"rows": [[r(c, values) for c in row] for row in t.rows]}) for t in doc.tables
            ],
        }
    )


# ── 4. understand ──


async def understand_step(deps: Deps, item_id: uuid.UUID, user_id: uuid.UUID) -> Outcome:
    async with st.service_tx(deps.db) as conn:
        row = await st.get_item(conn, item_id, user_id)
        doc = await st.load_extract(conn, item_id, user_id) if row else None
        tax = await st.taxonomy(conn)
    if row is None or doc is None:
        return Outcome("end", {"error": "gone"})
    u = await understand(
        deps.gateway,
        doc,
        input_kind=row["input_kind"],
        name_hint=row["original_name"] or row["kb_title"] or "",
        taxonomy=tax,
        user_id=user_id,
        item_id=item_id,
    )
    async with st.service_tx(deps.db) as conn:
        await st.set_status(
            conn,
            item_id,
            user_id,
            "extracting",
            progress=65,
            detail={"understanding": u.model_dump(mode="json"), "stage": "propose"},
        )
    return Outcome("propose")


# ── 5. propose ──


async def _view(deps: Deps, row: Mapping[str, Any], doc: ExtractedDoc, matches: list[dict[str, Any]]) -> IngestView:
    detail = row["stage_detail"] or {}
    u = Understanding.model_validate(detail.get("understanding") or {})
    path = row["storage_path"]
    # The kept original (PDFs, Office files, Anki decks, small images); None for links, pastes and deleted media.
    original = f"{deps.buckets.user_uploads}/{path}" if path and row["input_kind"] not in ("link", "paste") else None
    return IngestView(
        item_id=row["id"],
        user_id=row["user_id"],
        input_kind=row["input_kind"],
        understanding=u,
        doc=doc,
        original_name=row["original_name"] or row["kb_title"] or "",
        flags=await deps.flags_for(row["user_id"]),
        original_path=original,
        matches=tuple(matches),
    )


def _option_id(kind: str, i: int) -> str:
    return f"{kind}:{i}"


async def propose(deps: Deps, item_id: uuid.UUID, user_id: uuid.UUID) -> Outcome:
    rules = await deps.rules()
    async with st.service_tx(deps.db) as conn:
        row = await st.get_item(conn, item_id, user_id)
        doc = await st.load_extract(conn, item_id, user_id) if row else None
    if row is None or doc is None:
        return Outcome("end", {"error": "gone"})
    engine = deps.engine()
    matches: list[dict[str, Any]] = []
    if engine is not None:
        async with st.service_tx(deps.db) as conn:
            if row["input_kind"] not in ("image", "video", "anki") and doc.word_count >= 200:
                dup_chapter = await index.near_duplicate(
                    conn, engine, doc, user_id, cosine=rules.near_dup_cosine, share=rules.near_dup_share
                )
                if dup_chapter is not None:
                    other = await index.item_for_chapter(conn, user_id, dup_chapter)
                    await st.set_status(
                        conn,
                        item_id,
                        user_id,
                        "ready",
                        duplicate_of=other,
                        detail={"duplicate": "near", "chapter_id": dup_chapter},
                    )
                    if row["upload_id"]:
                        await st.set_upload(
                            conn, row["upload_id"], user_id, status="ready", processed_chapter_id=dup_chapter
                        )
                    log.info("ingest.duplicate", item_id=str(item_id), kind="near")
                    return Outcome("end", {"duplicate": "near"})
            probe = doc.full_text() or " ".join(f.caption for f in doc.figures)
            if probe.strip():
                matches = await index.match_sections(conn, engine, probe)
    view = await _view(deps, row, doc, matches)
    ranked = propose_all(view)
    if not ranked:
        return await _fail(deps, item_id, user_id, errors.NO_PLACEMENT)
    options = []
    for i, (reg, cand) in enumerate(ranked[:6]):
        options.append(
            {
                "option_id": _option_id(reg.kind, i),
                "kind": reg.kind,
                "score": round(cand.score, 3),
                "recommended": i == 0,
                "preview": cand.preview,
                "params": cand.params,
            }
        )
    proposal = {"options": options, "understanding": view.understanding.model_dump(mode="json")}
    async with st.service_tx(deps.db) as conn:
        await st.set_status(
            conn, item_id, user_id, "needs_confirmation", proposed_placement=proposal, detail={"stage": "confirm"}
        )
    log.info("ingest.proposed", item_id=str(item_id), options=len(options), top=options[0]["kind"])
    return Outcome("ask_confirm", {"options": [{k: v for k, v in o.items() if k != "params"} for o in options]})


# ── 6. place / undo ──


def _choice(proposal: Mapping[str, Any] | None, option_id: str) -> dict[str, Any] | None:
    for o in (proposal or {}).get("options") or []:
        if o.get("option_id") == option_id:
            return dict(o)
    return None


async def place(deps: Deps, item_id: uuid.UUID, user_id: uuid.UUID, option_id: str) -> Outcome:
    async with st.service_tx(deps.db) as conn:
        row = await st.get_item(conn, item_id, user_id)
        doc = await st.load_extract(conn, item_id, user_id) if row else None
    if row is None:
        return Outcome("end", {"error": "gone"})
    if doc is None:
        return Outcome("extract", {"replace_after": option_id})  # extract expired after Undo: rebuild it
    option = _choice(row["proposed_placement"], option_id)
    if option is None:
        raise IngestRejected(errors.NO_PLACEMENT)
    flags = await deps.flags_for(user_id)
    handler = handler_for(str(option["kind"]), flags)
    if handler is None:
        raise IngestRejected(errors.PLACEMENT_UNAVAILABLE)
    view = await _view(deps, row, doc, [])
    cand = Candidate(
        kind=str(option["kind"]),
        score=float(option["score"]),
        preview=dict(option["preview"]),
        params=dict(option.get("params") or {}),
    )
    async with st.service_tx(deps.db, timeout_ms=120_000) as conn:
        await st.set_status(
            conn, item_id, user_id, "placing", confirmed_placement={"option_id": option_id, "kind": cand.kind}
        )
        ctx = PlacementContext(conn=conn, user_id=user_id, item_id=item_id, source_id=USER_SOURCE_ID, view=view)
        try:
            records = await handler.apply(ctx, cand)
        except (LookupError, ValueError, psycopg.errors.IntegrityError) as exc:
            raise IngestRejected(errors.PLACEMENT_FAILED) from exc
        await st.add_placements(conn, item_id, user_id, [r.to_json() for r in records])
        chapter = next((r.id for r in records if r.table == "chapters"), None)
        if row["upload_id"]:
            await st.set_upload(conn, row["upload_id"], user_id, status="ready", processed_chapter_id=chapter)
    log.info("ingest.placed", item_id=str(item_id), kind=cand.kind, rows=len(records))
    return Outcome("index", {"records": [r.to_json() for r in records]})


async def index_step(deps: Deps, item_id: uuid.UUID, user_id: uuid.UUID) -> Outcome:
    async with st.service_tx(deps.db) as conn:
        row = await st.get_item(conn, item_id, user_id)
    if row is None:
        return Outcome("end")
    try:
        await index.reindex(deps.db, deps.engine(), user_id, [dict(r) for r in row["placements"] or []])
    except Exception as exc:  # noqa: BLE001 - the nightly reconcile picks it up; the placement stands
        log.warning("ingest.index_deferred", item_id=str(item_id), error=type(exc).__name__)
    async with st.service_tx(deps.db) as conn:
        await st.set_status(conn, item_id, user_id, "ready", detail={"stage": "done"})
    return Outcome("end")


async def undo(deps: Deps, item_id: uuid.UUID, user_id: uuid.UUID) -> list[dict[str, str]]:
    """Delete exactly what the placement wrote; the item returns to `needs_confirmation` with its proposal."""
    async with st.service_tx(deps.db) as conn:
        row = await st.get_item(conn, item_id, user_id)
        if row is None:
            raise LookupError("item")
        records = [PlacementRecord.from_json(r) for r in row["placements"] or []]
        if not records:
            return []
        doc = await st.load_extract(conn, item_id, user_id) or ExtractedDoc(kind=row["input_kind"] or "other")
        view = await _view(deps, row, doc, [])
        ctx = PlacementContext(conn=conn, user_id=user_id, item_id=item_id, source_id=USER_SOURCE_ID, view=view)
        kind = str((row["confirmed_placement"] or {}).get("kind") or records[0].kind)
        # Undo works even when the placement's flag has since been turned off.
        handler = handler_for(kind, _all_on())
        if handler is None:
            raise IngestRejected(errors.PLACEMENT_UNAVAILABLE)
        await handler.undo(ctx, records)
        await st.add_placements(conn, item_id, user_id, [])
        await st.set_status(
            conn,
            item_id,
            user_id,
            "needs_confirmation",
            confirmed_placement=None,
            detail={"stage": "confirm", "undone": True},
        )
        if row["upload_id"]:
            await st.set_upload(conn, row["upload_id"], user_id, status="processing", processed_chapter_id=None)
    out = [r.to_json() for r in records]
    await index.reindex(deps.db, deps.engine(), user_id, out)
    log.info("ingest.undone", item_id=str(item_id), rows=len(records))
    return out


def _all_on() -> dict[str, bool]:
    from .placements import registered

    return {r.requires_flag: True for r in registered() if r.requires_flag}


async def run_all(
    deps: Deps, item_id: uuid.UUID, user_id: uuid.UUID, *, vault_answer: str = "continue", option: str | None = None
) -> Outcome:
    """The whole pipeline without interrupts (tests and the integration benchmark): answers are given up front;
    `option=None` takes the recommended placement."""
    out = await guarded(deps, item_id, user_id, lambda: receive(deps, item_id, user_id))
    if out.next == "gate":
        out = await guarded(deps, item_id, user_id, lambda: gate(deps, item_id, user_id))
    if out.next == "ask_vault":
        out = await guarded(deps, item_id, user_id, lambda: vault(deps, item_id, user_id, vault_answer))
    if out.next == "extract":
        out = await guarded(deps, item_id, user_id, lambda: extract(deps, item_id, user_id))
    if out.next == "understand":
        out = await guarded(deps, item_id, user_id, lambda: understand_step(deps, item_id, user_id))
    if out.next == "propose":
        out = await guarded(deps, item_id, user_id, lambda: propose(deps, item_id, user_id))
    if out.next == "ask_confirm":
        chosen = option or out.detail["options"][0]["option_id"]
        out = await guarded(deps, item_id, user_id, lambda: place(deps, item_id, user_id, chosen))
    if out.next == "index":
        out = await guarded(deps, item_id, user_id, lambda: index_step(deps, item_id, user_id))
    return out


__all__ = [
    "Deps",
    "Outcome",
    "extract",
    "gate",
    "guarded",
    "index_step",
    "place",
    "propose",
    "receive",
    "run_all",
    "understand_step",
    "undo",
    "vault",
]


async def batch_of(deps: Deps, item_id: uuid.UUID, user_id: uuid.UUID) -> str | None:
    async with st.service_tx(deps.db) as conn:
        row = await st.one(
            conn, "select batch_id from public.ingest_items where id = %s and user_id = %s", (item_id, user_id)
        )
    return str(row["batch_id"]) if row and row["batch_id"] else None
