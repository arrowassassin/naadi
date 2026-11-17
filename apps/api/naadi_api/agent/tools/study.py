"""Write tools on study material: `make_flashcards`, `build_block`, `add_to_plan` (each behind a confirm).

They reuse the owning segments' rules: cards are validated with S10's `CardCreate` and inserted as the student
(origin 'user'; a question link needs a graded attempt), blocks are chosen by S08's pool filter (tutor mode only:
the assistant never starts a timed session), plan items are the student's own rows with origin 'assistant'.
"""

from __future__ import annotations

import datetime as dt
import secrets
import uuid
from typing import Any, Literal

import psycopg
from pydantic import BaseModel, Field, ValidationError

from ...cards import docs as card_docs
from ...cards import routes as cards
from ...cards.models import CardCreate
from ...qbank import routes as qbank
from ...qbank.models import BlockConfig
from ...qbank.selection import choose, filter_pool
from .executor import ToolRefused
from .registry import Proposal, ToolContext, ToolResult, WriteTool, register_tool

MAX_CARDS = 20


class CardDraft(BaseModel):
    front: str = Field(min_length=1, max_length=1000)
    back: str = Field(min_length=1, max_length=2000)


class FlashcardsArgs(BaseModel):
    cards: list[CardDraft] = Field(min_length=1, max_length=MAX_CARDS)
    deck: str | None = Field(default=None, max_length=60)


async def _cards_prepare(ctx: ToolContext, args: FlashcardsArgs) -> Proposal:
    """Make flashcards (front/back pairs, up to 20) for the student from the page or the conversation. The student
    sees a preview and confirms, edits or cancels before any card is saved."""
    try:
        valid = [CardCreate(front_text=c.front, back_text=c.back, deck=args.deck) for c in args.cards]
    except ValidationError as exc:
        raise ToolRefused("invalid_card") from exc
    question_id = ctx.page.question_id if ctx.page.question_revealed else None
    section_id = ctx.page.id if ctx.page.type == "section" else None
    preview = {"kind": "cards", "cards": [{"front": c.front_text, "back": c.back_text} for c in valid]}
    return Proposal(preview=preview, data={"cards": valid, "question_id": question_id, "section_id": section_id})


async def _cards_apply(ctx: ToolContext, args: FlashcardsArgs, p: Proposal) -> ToolResult:
    question_id = p.data["question_id"]
    if question_id is not None:

        async def gate(conn: psycopg.AsyncConnection[Any]) -> bool:
            return await cards._has_graded_attempt(conn, ctx.user_id, question_id)

        if not await cards.as_service(ctx.db, gate):
            question_id = None
    from psycopg.types.json import Jsonb

    made: list[dict[str, str]] = []
    async with ctx.user_tx() as conn:
        for c in p.data["cards"]:
            await conn.execute(
                "insert into public.flashcards (id, owner_user_id, deck, front_html, back_html, front_doc, back_doc,"
                " question_id, section_id, origin)"
                " values (%s, (select auth.uid()), %s, %s, %s, %s, %s, %s, %s, 'user')",
                (
                    uuid.uuid4(),
                    c.deck or cards.DEFAULT_DECK,
                    card_docs.text_to_html(c.front_text),
                    card_docs.text_to_html(c.back_text),
                    Jsonb(card_docs.text_to_doc(c.front_text)),
                    Jsonb(card_docs.text_to_doc(c.back_text)),
                    question_id,
                    p.data["section_id"],
                ),
            )
            made.append({"front": c.front_text, "back": c.back_text})
    return ToolResult(
        content=f"Saved {len(made)} flashcards.", blocks=[{"type": "card_preview", "data": {"cards": made}}]
    )


class BlockArgs(BaseModel):
    count: int = Field(default=20, ge=1, le=40)
    systems: list[str] = Field(default_factory=list, max_length=10, description="System names, e.g. 'Renal'")
    disciplines: list[str] = Field(default_factory=list, max_length=10, description="e.g. 'Physiology'")
    status: Literal["unused", "incorrect", "flagged", "correct", "all"] = "unused"


async def _taxonomy_ids(conn: psycopg.AsyncConnection[Any], exam: str, kind: str, names: list[str]) -> list[str]:
    if not names:
        return []
    cur = await conn.execute(
        "select id from public.taxonomy_nodes where kind = %s and %s = any(exam)"
        " and (lower(label) = any(%s) or lower(id) = any(%s))",
        (kind, exam, [n.lower().strip() for n in names], [n.lower().strip() for n in names]),
    )
    return sorted({r["id"] for r in await cur.fetchall()})


async def _block_prepare(ctx: ToolContext, args: BlockArgs) -> Proposal:
    """Build a tutor-mode Qbank block (e.g. 20 unused renal physiology questions). The student confirms first."""
    async with ctx.user_tx() as conn:
        cur = await conn.execute("select exam_target from public.profiles where user_id = (select auth.uid())")
        row = await cur.fetchone()
        exam = str(row["exam_target"]) if row else "step1"
        systems = await _taxonomy_ids(conn, exam, "system", args.systems)
        disciplines = await _taxonomy_ids(conn, exam, "discipline", args.disciplines)
        if (args.systems and not systems) or (args.disciplines and not disciplines):
            raise ToolRefused("unknown_topic")
        questions, history = await qbank._load_pool(conn, exam)
    status: Any = args.status
    pool = filter_pool(questions, history, systems=systems, disciplines=disciplines, status=status, difficulty=None)
    if not pool:
        raise ToolRefused("no_questions_match")
    count = min(args.count, len(pool))
    preview = {
        "kind": "block",
        "count": count,
        "mode": "tutor",
        "systems": args.systems,
        "disciplines": args.disciplines,
        "status": args.status,
    }
    return Proposal(
        preview=preview,
        editable=False,
        data={"exam": exam, "systems": systems, "disciplines": disciplines, "count": count, "status": status},
    )


async def _block_apply(ctx: ToolContext, args: BlockArgs, p: Proposal) -> ToolResult:
    d = p.data
    seed = secrets.randbits(48)
    async with ctx.user_tx() as conn:
        questions, history = await qbank._load_pool(conn, d["exam"])
    pool = filter_pool(
        questions, history, systems=d["systems"], disciplines=d["disciplines"], status=d["status"], difficulty=None
    )
    if not pool:
        return ToolResult(content="[error: no_questions_match]", status="error")
    ids = choose((q.id for q in pool), min(d["count"], len(pool)), seed)
    cfg = BlockConfig(
        exam=d["exam"],
        mode="tutor",
        status=d["status"],
        difficulty=None,
        systems=d["systems"],
        disciplines=d["disciplines"],
        seed=seed,
        weak_spot=False,
    )
    try:
        detail = await qbank._insert_block(ctx.db, ctx.user, cfg, ids)
    except Exception as exc:  # noqa: BLE001 - HTTP-shaped errors from the qbank module become a tool error
        return ToolResult(content=f"[error: block_failed:{type(exc).__name__}]", status="error")
    action = {
        "items": [
            {
                "action": "open_block",
                "label_key": "agent.action.open_block",
                "payload": {"block_id": str(detail.id), "n": 1},
            }
        ]
    }
    return ToolResult(
        content=f"Built a tutor block of {len(ids)} questions.", blocks=[{"type": "actions", "data": action}]
    )


class PlanArgs(BaseModel):
    day: dt.date = Field(description="ISO date, today or later")
    title: str = Field(min_length=2, max_length=120)
    kind: Literal["read", "questions", "cards", "video", "admin"] = "read"
    target_minutes: int | None = Field(default=None, ge=5, le=600)


async def _plan_prepare(ctx: ToolContext, args: PlanArgs) -> Proposal:
    """Add one item to the student's study plan on a given day. The student confirms or edits it first."""
    if args.day < dt.datetime.now(dt.UTC).date() - dt.timedelta(days=1):
        raise ToolRefused("day_in_past")
    preview = {
        "kind": "plan_item",
        "day": args.day.isoformat(),
        "title": args.title,
        "item_kind": args.kind,
        "target_minutes": args.target_minutes,
    }
    return Proposal(preview=preview)


async def _plan_apply(ctx: ToolContext, args: PlanArgs, p: Proposal) -> ToolResult:
    ref_type, ref_id = None, None
    if ctx.page.type == "section" and ctx.page.id:
        ref_type, ref_id = "section", ctx.page.id

    # plan_items are API-written since S16 (0027): the confirmed item goes in as the service role for this run's
    # authenticated user only, after the day's existing items (the same shape S16's coach accept writes).
    async def write(conn: psycopg.AsyncConnection[Any]) -> None:
        cur = await conn.execute(
            "select coalesce(max(sort), -1)::int as m from public.plan_items where user_id = %s and day = %s",
            (ctx.user_id, args.day),
        )
        row = await cur.fetchone()
        await conn.execute(
            "insert into public.plan_items (user_id, day, kind, title, ref_type, ref_id, target_minutes, origin, sort)"
            " values (%s, %s, %s, %s, %s, %s, %s, 'assistant', %s)",
            (ctx.user_id, args.day, args.kind, args.title, ref_type, ref_id, args.target_minutes, _sort_after(row)),
        )

    await qbank.as_service(ctx.db, write)
    return ToolResult(content=f"Added to the plan on {args.day.isoformat()}.")


def _sort_after(row: Any) -> int:
    if row is None:
        return 0
    m = row["m"] if isinstance(row, dict) else row[0]
    return int(m) + 1


register_tool("make_flashcards", WriteTool(_cards_prepare, _cards_apply), writes=True)
register_tool("build_block", WriteTool(_block_prepare, _block_apply), writes=True)
register_tool("add_to_plan", WriteTool(_plan_prepare, _plan_apply), writes=True)
