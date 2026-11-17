"""`tutor`: the page-aware assistant (segment 14; HANDOFF §6.2–§6.5, docs/AGENTS.md, docs/segments/14-protocol.md).

    START → guard ─┬─ refuse → END                         (exam mode or assistant off: structured refusal)
                   └─ tutor ⇄ tools → compose → END

- guard: identity from the token only; exam mode from `active_exam` (server), never from the client; the page's
  question state re-checked against attempts; attachments validated; intent rules (router role only when they are
  ambiguous, counted in the turn); the few most relevant long-term memories retrieved (semantic search).
- tutor: `get_gateway().chat_model("tutor", ctx)` with the visible tools bound, purpose `chat`; the prompt is sized
  to the model's window (`assistant.context_budget`; older turns folded into the rolling summary by langmem through
  the `long_context` role when needed). The 4-call cap is the gateway's TurnCounter, keyed by the turn (so a resume
  after a confirm continues the same budget); the last call is made without tools so the turn ends with an answer.
  Budget spent or no model → degrade: course retrieval only, a `sources` block and a `notice` with the reset time.
- tools: at most one write per pass (confirm interrupt), every result through the leak checks.
- compose: citations, the §6.5 disclaimer once per thread, the `assistant_threads` row, and langmem's background
  memory manager (suggestions only, at most once per thread per `memory_reflect_minutes`, off the turn budget).
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
import uuid
from collections.abc import Sequence
from typing import Annotated, Any, Literal, NotRequired, TypedDict

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_store, get_stream_writer
from langgraph.errors import GraphInterrupt
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langmem.short_term import RunningSummary, asummarize_messages

from naadi_api.agent.identity import RunIdentity, run_identity
from naadi_api.agent.runtime import agent_db, get_runtime
from naadi_api.agent.tools import tools_for
from naadi_api.agent.tools.executor import plan_pass, run_tool_call
from naadi_api.agent.tools.registry import ToolContext, ToolResult, get_tool, web_url_key
from naadi_api.assistant import memory as mem
from naadi_api.assistant import threads
from naadi_api.assistant.context_budget import (
    SUMMARY_TOKENS,
    Budget,
    count_tokens,
    fit,
    history_for_model,
    schema_tokens,
)
from naadi_api.assistant.guard import ROUTER_PROMPT, Intent, decide, parse_router
from naadi_api.assistant.leak import LeakBlocked, check_payload
from naadi_api.assistant.page import (
    InvalidAttachment,
    page_from_state,
    page_ref_row,
    page_to_state,
    parse_attachments,
    parse_page,
    verify_page,
)
from naadi_api.assistant.prompts import ASK_RULES, INTENT_RULES, SYSTEM, memory_lines, page_lines
from naadi_api.auth import CurrentUser
from naadi_api.config_api.schemas import AssistantConfig, FeatureFlags
from naadi_api.config_api.service import AppConfigService
from naadi_api.embeddings.engine import get_engine
from naadi_api.embeddings.query import search_local
from naadi_api.llm import CallContext, LLMUnavailable, get_gateway
from naadi_api.llm_budget import BudgetExhausted, TurnLimitReached
from naadi_api.logs import get_logger
from naadi_api.qbank import routes as qbank_db
from naadi_api.qbank.exam_state import active_exam
from naadi_api.settings import get_settings

log = get_logger("naadi.agent.tutor")

NOSTREAM = ["nostream"]  # LangGraph does not stream these runs' tokens to the client (router, summary)
ASK_KINDS = ("hint", "explain", "ask", "summarise", "flashcards", "quiz")
_CITE = re.compile(r"\[(\d{1,3})\]")
_reflections: set[asyncio.Task[Any]] = set()


def _append(left: list[Any] | None, right: list[Any] | None) -> list[Any]:
    out = list(left or [])
    seen = {x.get("id") for x in out if isinstance(x, dict)}
    for x in right or []:
        if isinstance(x, dict) and x.get("id") in seen:
            continue
        out.append(x)
    return out


def _canvas(left: list[Any] | None, right: list[Any] | None) -> list[Any]:
    """Items keyed by `key`: a later item with the same key replaces the earlier one."""
    out = list(left or [])
    for x in right or []:
        out = [o for o in out if not (isinstance(o, dict) and o.get("key") == x.get("key"))]
        out.append(x)
    return out


class TutorInput(TypedDict):
    """What a client may send. Everything else in the state is the server's (LangGraph filters input to this)."""

    messages: Annotated[list[AnyMessage], add_messages]
    page_context: NotRequired[dict[str, Any] | None]
    attachments: NotRequired[list[dict[str, Any]]]
    web: NotRequired[bool]
    ask: NotRequired[dict[str, Any] | None]


class TutorState(TutorInput):
    # The run whose guard derived this turn's server state (not in TutorInput: no client input can set it). A node
    # running in any other run (a resume, or a client that skipped the guard) re-derives that state itself.
    guard_run: NotRequired[str | None]
    blocks: NotRequired[Annotated[list[dict[str, Any]], _append]]
    citations: NotRequired[Annotated[list[dict[str, Any]], _append]]
    canvas: NotRequired[Annotated[list[dict[str, Any]], _canvas]]
    notice: NotRequired[dict[str, Any] | None]
    refusal: NotRequired[dict[str, Any] | None]
    disclaimer_shown: NotRequired[bool]
    summary: NotRequired[dict[str, Any] | None]
    # server bookkeeping
    msg_count: NotRequired[int]
    turn_id: NotRequired[str]
    turn_calls: NotRequired[int]
    turn_start: NotRequired[int]
    intent: NotRequired[str]
    exam_mode: NotRequired[bool]
    page: NotRequired[dict[str, Any] | None]
    memories: NotRequired[list[list[str]]]
    kb_best: NotRequired[float | None]
    web_allowed: NotRequired[bool]
    degraded: NotRequired[bool]


# ── shared helpers ──


async def _config() -> tuple[AssistantConfig, dict[str, Any]]:
    rt = get_runtime()
    svc = AppConfigService(agent_db(), rt.settings.config_dir)
    cfg = AssistantConfig.model_validate(await svc.get("assistant"))
    rules = await svc.get("exam_rules")
    return cfg, rules


async def _flags_for(who: RunIdentity) -> dict[str, bool]:
    rt = get_runtime()
    svc = AppConfigService(agent_db(), rt.settings.config_dir)
    flags = FeatureFlags.model_validate(await svc.get("feature_flags")).root
    return {k: f.on_for(is_admin=who.is_admin, app_env=rt.settings.app_env) for k, f in flags.items()}


async def _exam_active(user_id: uuid.UUID, rules: dict[str, Any]) -> bool:
    async def check(conn: Any) -> bool:
        return await active_exam(conn, user_id, rules=rules) is not None

    return await qbank_db.as_service(agent_db(), check)


def _emit(event: dict[str, Any]) -> None:
    with contextlib.suppress(RuntimeError):  # outside a streaming run (unit tests calling a node directly)
        get_stream_writer()(event)


def _block(block_type: str, data: dict[str, Any], message_id: str | None) -> dict[str, Any]:
    return {"id": "b_" + uuid.uuid4().hex[:12], "message_id": message_id, "type": block_type, "data": data}


def _text(m: BaseMessage) -> str:
    c = m.content
    if isinstance(c, str):
        return c
    return " ".join(str(p.get("text", "")) for p in c if isinstance(p, dict) and p.get("type") == "text")


def _call_ctx(state: TutorState, who: RunIdentity, cfg: AssistantConfig, extra_tokens: int = 0) -> CallContext:
    gw = get_gateway()
    turn = gw.turns.for_run(state.get("turn_id") or who.run_id or uuid.uuid4().hex)
    turn.calls = max(turn.calls, int(state.get("turn_calls") or 0))
    reserve = cfg.answer_reserve_tokens + extra_tokens
    return CallContext(
        purpose="chat",
        user_id=who.user_id,
        run_id=who.run_id,
        turn=turn,
        fit=lambda msgs, window: fit(msgs, window, margin=cfg.context_margin, answer_reserve=reserve),
    )


def _store() -> Any:
    try:
        return get_store()
    except Exception:  # noqa: BLE001 - no store configured (unit tests); memory is then unavailable
        return None


# ── guard ──


async def guard(state: TutorState, config: RunnableConfig) -> dict[str, Any]:
    who = run_identity(config)
    if who.user_id is None:
        return {"refusal": {"reason": "not_a_student"}, "notice": None}
    cfg, rules = await _config()
    flags = await _flags_for(who)
    msgs = list(state.get("messages") or [])
    known = int(state.get("msg_count") or 0)
    fresh = msgs[known:]
    # Only the student's own new message counts; anything else a client slipped into `messages` is removed.
    removals = [RemoveMessage(id=m.id) for m in fresh if not isinstance(m, HumanMessage) and m.id]
    humans = [m for m in fresh if isinstance(m, HumanMessage)]
    removals += [RemoveMessage(id=m.id) for m in humans[:-1] if m.id]
    base: dict[str, Any] = {"refusal": None, "notice": None, "attachments": [], "degraded": False, "kb_best": None}
    if not flags.get("assistant"):
        return {**base, "messages": removals, "refusal": {"reason": "disabled"}, "msg_count": known}
    if await _exam_active(who.user_id, rules):
        return {
            **base,
            "messages": removals,
            "refusal": {"reason": "exam_mode"},
            "exam_mode": True,
            "msg_count": known,
        }
    if not humans:
        return {**base, "messages": removals, "refusal": {"reason": "empty"}, "msg_count": known}
    human = humans[-1]
    text = _text(human).strip()[: cfg.max_message_chars]
    ask = state.get("ask") if isinstance(state.get("ask"), dict) else None
    ask_kind = ask.get("kind") if ask and ask.get("kind") in ASK_KINDS else None
    page = await verify_page(agent_db(), who.user_id, parse_page(state.get("page_context")), ask_kind)
    try:
        att = parse_attachments(state.get("attachments"), cfg.max_attachments)
    except InvalidAttachment as exc:
        return {**base, "messages": removals, "refusal": {"reason": f"attachment:{exc}"}, "msg_count": known}
    if att.selections or att.image_texts:
        quoted = "\n\n".join(f"Selected text:\n> {s}" for s in att.selections + att.image_texts)
        text = f"{text}\n\n{quoted}" if text else quoted
    if not text and not att.images:
        return {**base, "messages": removals, "refusal": {"reason": "empty"}, "msg_count": known}
    try:
        check_payload(text, where="message")
    except LeakBlocked:
        # Vault material never reaches a model (llm.py would refuse the call anyway): refuse the turn cleanly.
        return {**base, "messages": removals, "refusal": {"reason": "vault"}, "msg_count": known}
    clean_human = HumanMessage(
        content=text or "(image)",
        id=human.id,
        additional_kwargs={"naadi_attachments": [{"kind": "image"}] * len(att.images)} if att.images else {},
    )
    turn_id = uuid.uuid4().hex
    intent, ambiguous = decide(text)
    turn_calls = 0
    if ambiguous:
        intent, turn_calls = await _route(state, who, cfg, text, turn_id)
    memories: list[list[str]] = []
    store = _store()
    if store is not None and cfg.memory_retrieve_k:
        try:
            found = await mem.retrieve(store, who.user_id, text, cfg.memory_retrieve_k)
            memories = [[m.key, m.fact] for m in found]
        except Exception as exc:  # noqa: BLE001 - memory is an aid; the turn goes on without it
            log.warning("tutor.memory_unavailable", error=type(exc).__name__)
    count_after = len(msgs) - len(removals)
    if who.thread_id:
        # Listed from the first turn, so a chat left waiting on a confirm still shows in the thread list.
        try:
            first = next((m for m in msgs if isinstance(m, HumanMessage)), human)
            first_text = text if first.id == human.id else _text(first)
            await threads.record_turn(
                agent_db(),
                user_id=who.user_id,
                aegra_thread_id=uuid.UUID(who.thread_id),
                title=threads.title_from(first_text),
                page_ref=page_ref_row(page),
                reflect_minutes=None,
            )
        except Exception as exc:  # noqa: BLE001 - the turn goes on; compose writes the row again
            log.warning("tutor.thread_row_failed", error=type(exc).__name__)
    return {
        **base,
        "messages": [*removals, clean_human],
        "attachments": [{"kind": "image", "data_url": u} for u in att.images],
        "page": page_to_state(page),
        "exam_mode": False,
        "intent": intent or "study",
        "turn_id": turn_id,
        "turn_calls": turn_calls,
        "turn_start": count_after - 1,
        "msg_count": count_after,
        "memories": memories,
        "web_allowed": bool(state.get("web")),
        "guard_run": who.run_id,
    }


async def _route(
    state: TutorState, who: RunIdentity, cfg: AssistantConfig, text: str, turn_id: str
) -> tuple[Intent, int]:
    ctx = _call_ctx({**state, "turn_id": turn_id, "turn_calls": 0}, who, cfg)
    model = get_gateway().chat_model("router", ctx).with_config(tags=NOSTREAM)
    try:
        reply = await model.ainvoke([SystemMessage(ROUTER_PROMPT), HumanMessage(text[:1000])])
        return parse_router(_text(reply)), ctx.turn.calls
    except (BudgetExhausted, LLMUnavailable, TurnLimitReached):
        return "dosing", ctx.turn.calls  # the stricter rule when the tie cannot be broken


def route_guard(state: TutorState) -> Literal["refuse", "tutor"]:
    return "refuse" if state.get("refusal") else "tutor"


async def refuse(state: TutorState) -> dict[str, Any]:
    reason = (state.get("refusal") or {}).get("reason", "disabled")
    notice = {"kind": "exam_mode"} if reason == "exam_mode" else {"kind": "unavailable"}
    block = _block("notice", notice, None)
    _emit({"type": "block", "block": block})
    return {"notice": notice, "blocks": [block]}


# ── server state, re-derived in every run ──


async def _reveal_state(user_id: uuid.UUID, question_id: str) -> tuple[bool, str | None]:
    """Whether the student has a graded, revealed attempt on the question, and their choice (from the database)."""
    from naadi_api.assistant.questions import revealed_choice, revealed_questions

    db = agent_db()
    revealed = question_id in await revealed_questions(db, user_id, [question_id])
    return revealed, (await revealed_choice(db, user_id, question_id) if revealed else None)


def _turn_text(state: TutorState) -> str:
    msgs = list(state.get("messages") or [])
    start = int(state.get("turn_start") or 0)
    human = next((m for m in reversed(msgs[start:] or msgs) if isinstance(m, HumanMessage)), None)
    return _text(human).strip() if human is not None else ""


_URL = re.compile(r"https://[^\s<>()\[\]\"']+")


def _turn_web_urls(state: TutorState) -> set[str]:
    """web_extract's allow-list for this turn: the web results already returned this turn and the URLs in the
    student's own message."""
    msgs = list(state.get("messages") or [])
    turn_ids = {m.id for m in msgs[int(state.get("turn_start") or 0) :] if isinstance(m, AIMessage)}
    urls = {
        web_url_key(c["url"])
        for c in state.get("citations") or []
        if c.get("layer") == "web" and isinstance(c.get("url"), str) and c.get("message_id") in turn_ids
    }
    urls |= {web_url_key(u.rstrip(".,;:!?")) for u in _URL.findall(_turn_text(state))}
    return urls


def _disclaimer_shown(state: TutorState) -> bool:
    """From the thread's recorded blocks, never from a flag a client could have written."""
    return any(
        b.get("type") == "notice" and (b.get("data") or {}).get("kind") == "disclaimer"
        for b in state.get("blocks") or []
        if isinstance(b, dict)
    )


async def _recheck(
    state: TutorState, who: RunIdentity, cfg: AssistantConfig, rules: dict[str, Any], flags: dict[str, bool]
) -> dict[str, Any]:
    """Every node after the guard calls this. The feature flag and the exam are checked on every call; when the
    guard did not run in this run (a resumed confirm, or a run that reached this node without the guard), the
    guard-derived fields are derived again from the database and the turn's own text: the page's revealed state,
    the intent (rules; a tie takes the stricter rule without a model call) and the memories. Returns the state
    updates, with `refusal` set when the turn must stop."""
    assert who.user_id is not None
    if not flags.get("assistant"):
        return {"refusal": {"reason": "disabled"}}
    if await _exam_active(who.user_id, rules):
        return {"refusal": {"reason": "exam_mode"}, "exam_mode": True}
    if state.get("guard_run") and state.get("guard_run") == who.run_id:
        return {}
    page = page_from_state(state.get("page"))
    if page.question_id:
        page.question_revealed, page.chosen = await _reveal_state(who.user_id, page.question_id)
    else:
        page.question_revealed, page.chosen = False, None
    text = _turn_text(state)
    intent, ambiguous = decide(text)
    memories: list[list[str]] = []
    store = _store()
    if store is not None and cfg.memory_retrieve_k and text:
        try:
            found = await mem.retrieve(store, who.user_id, text, cfg.memory_retrieve_k)
            memories = [[m.key, m.fact] for m in found]
        except Exception as exc:  # noqa: BLE001 - memory is an aid
            log.warning("tutor.memory_unavailable", error=type(exc).__name__)
    return {
        "page": page_to_state(page),
        "intent": "dosing" if ambiguous else (intent or "study"),
        "memories": memories,
        "exam_mode": False,
        "guard_run": who.run_id,
    }


def _stop(reason: str) -> dict[str, Any]:
    notice = {"kind": "exam_mode"} if reason == "exam_mode" else {"kind": "unavailable"}
    block = _block("notice", notice, None)
    _emit({"type": "block", "block": block})
    return {"refusal": {"reason": reason}, "notice": notice, "blocks": [block]}


# ── tutor ──


def _system_messages(state: TutorState, page_hint: str) -> list[SystemMessage]:
    intent = state.get("intent") or "study"
    rules = INTENT_RULES.get(intent, "")  # type: ignore[call-overload]
    page = page_from_state(state.get("page"))
    ask_rule = ASK_RULES.get(page.ask_kind or "", "")
    memories = memory_lines([(k, f) for k, f in state.get("memories") or []])
    context = "\n".join(x for x in (page_hint, rules, ask_rule, memories) if x)
    return [SystemMessage(content=SYSTEM), SystemMessage(content=context)]


def _turn_human_with_images(history: list[BaseMessage], state: TutorState) -> list[BaseMessage]:
    images = [a.get("data_url") for a in state.get("attachments") or [] if isinstance(a, dict) and a.get("data_url")]
    if not images:
        return history
    out = list(history)
    for i in range(len(out) - 1, -1, -1):
        if isinstance(out[i], HumanMessage):
            parts: list[str | dict[str, Any]] = [{"type": "text", "text": _text(out[i])}]
            parts += [{"type": "image_url", "image_url": {"url": u}} for u in images]
            out[i] = HumanMessage(content=parts, id=out[i].id)
            break
    return out


def _rs_from(d: Any) -> RunningSummary | None:
    if not isinstance(d, dict) or not d.get("summary"):
        return None
    return RunningSummary(
        summary=str(d["summary"]),
        summarized_message_ids=set(d.get("ids") or []),
        last_summarized_message_id=d.get("last"),
    )


def _rs_to(rs: RunningSummary | None) -> dict[str, Any] | None:
    if rs is None:
        return None
    return {"summary": rs.summary, "ids": sorted(rs.summarized_message_ids), "last": rs.last_summarized_message_id}


async def budget_context(
    state: TutorState, who: RunIdentity, cfg: AssistantConfig, ctx: CallContext, schemas: Sequence[dict[str, Any]]
) -> tuple[list[BaseMessage], dict[str, Any] | None]:
    """The model's view of the thread, sized to the primary model's window (see context_budget)."""
    gw = get_gateway()
    models = (await gw.config()).models
    window = models.chain("tutor")[0].context_window
    page = page_from_state(state.get("page"))
    video_t = (page.video or {}).get("t") if page.video else None
    hint = page_lines(
        page_type=page.type,
        title=page.title,
        ident=page.id,
        question_id=page.question_id,
        question_revealed=page.question_revealed,
        video_t=video_t,
        has_selection="Selected text:" in "".join(_text(m) for m in (state.get("messages") or [])[-1:]),
    )
    head = _system_messages(state, hint)
    budget = Budget(window, cfg.context_margin, cfg.answer_reserve_tokens, schema_tokens(schemas))
    room = max(1024, budget.available - count_tokens(head))
    history = history_for_model(state.get("messages") or [])
    running = _rs_from(state.get("summary"))
    max_calls = (await gw.config()).budget.max_llm_calls_per_turn
    # A summary costs one call: only when at least two remain (one is kept for the answer).
    can_summarize = max_calls - ctx.turn.calls >= 2
    trigger = int(room * cfg.summary_trigger) if can_summarize else 10**12
    summarizer = gw.chat_model("long_context", ctx).with_config(tags=NOSTREAM)
    new_summary = state.get("summary")
    try:
        res = await asummarize_messages(
            history,  # type: ignore[arg-type]
            running_summary=running,
            model=summarizer,
            max_tokens=room,
            max_tokens_before_summary=trigger,
            max_summary_tokens=SUMMARY_TOKENS,
            token_counter=count_tokens,  # type: ignore[arg-type]
        )
        view: list[BaseMessage] = list(res.messages)
        new_summary = _rs_to(res.running_summary) or new_summary
    except (BudgetExhausted, LLMUnavailable, TurnLimitReached) as exc:
        log.info("tutor.summary_skipped", reason=type(exc).__name__)
        view = history
    view = _turn_human_with_images(view, state)
    return fit(
        [*head, *view], window, margin=cfg.context_margin, answer_reserve=budget.answer_reserve + budget.tools
    ), (new_summary)


async def tutor(state: TutorState, config: RunnableConfig) -> dict[str, Any]:
    who = run_identity(config)
    if who.user_id is None or state.get("refusal"):
        return {}
    cfg, rules = await _config()
    flags = await _flags_for(who)
    fix = await _recheck(state, who, cfg, rules, flags)
    if fix.get("refusal"):
        return {**fix, **_stop(fix["refusal"]["reason"])}
    if fix:
        state = {**state, **fix}  # type: ignore[typeddict-item]
    return {**fix, **await _answer(state, who, cfg, flags, config)}


async def _answer(
    state: TutorState, who: RunIdentity, cfg: AssistantConfig, flags: dict[str, bool], config: RunnableConfig
) -> dict[str, Any]:
    specs = tools_for(flags)
    schemas = [s.openai_schema() for s in specs]
    ctx = _call_ctx(state, who, cfg, extra_tokens=schema_tokens(schemas))
    gw = get_gateway()
    max_calls = (await gw.config()).budget.max_llm_calls_per_turn
    try:
        messages, summary = await budget_context(state, who, cfg, ctx, schemas)
        model = gw.chat_model("tutor", ctx)
        # The last call of the turn has no tools, so the turn always ends with an answer.
        runnable = model.bind_tools(schemas) if max_calls - ctx.turn.calls > 1 and schemas else model
        reply = await runnable.ainvoke(messages, config)
    except TurnLimitReached:
        notice = {"kind": "turn_cap"}
        block = _block("notice", notice, None)
        _emit({"type": "block", "block": block})
        return {"notice": notice, "blocks": [block], "turn_calls": ctx.turn.calls}
    except BudgetExhausted as exc:
        return await _degrade(
            state,
            who,
            {
                "kind": exc.reason if exc.reason == "rate_limited" else "budget_exhausted",
                "reset_at": exc.reset_at.isoformat(),
            },
            ctx,
        )
    except LLMUnavailable:
        return await _degrade(state, who, {"kind": "unavailable"}, ctx)
    if not isinstance(reply, AIMessage):
        reply = AIMessage(content=_text(reply))
    if reply.id is None:
        reply.id = "ai_" + uuid.uuid4().hex
    allowed = {s.name for s in specs}
    # Drop calls to tools this user may not see; the executor would refuse them anyway.
    if reply.tool_calls:
        kept = [tc for tc in reply.tool_calls if tc.get("name") in allowed][:6]
        reply = AIMessage(
            content=reply.content, id=reply.id, tool_calls=kept, response_metadata=reply.response_metadata
        )
    return {"messages": [reply], "summary": summary, "turn_calls": ctx.turn.calls}


async def _degrade(state: TutorState, who: RunIdentity, notice: dict[str, Any], ctx: CallContext) -> dict[str, Any]:
    """No model this turn: answer from course retrieval only (sources) and say when the quota resets."""
    msgs = state.get("messages") or []
    question = next((_text(m) for m in reversed(msgs) if isinstance(m, HumanMessage)), "")
    reply = AIMessage(content="", id="ai_" + uuid.uuid4().hex, additional_kwargs={"naadi_degraded": True})
    out_blocks = []
    citations: list[dict[str, Any]] = []
    try:
        engine = get_engine(get_settings().config_dir)
        assert who.user_id is not None
        from naadi_api.agent.tools.knowledge import citation_of_hit

        user = CurrentUser(
            user_id=who.user_id, email=None, email_verified=True, is_admin=who.is_admin, account_checked=True
        )
        res = await search_local(agent_db(), engine, user, question[:300] or "study", limit=8)
        qids = [h.ref_id for h in res.hits if h.ref_type == "question"]
        from naadi_api.assistant.questions import revealed_questions

        revealed = await revealed_questions(agent_db(), who.user_id, qids) if qids else set()
        hits = [h for h in res.hits if h.ref_type != "question" or h.ref_id in revealed][:6]
        citations = [{**citation_of_hit(i, h), "message_id": reply.id} for i, h in enumerate(hits, start=1)]
    except Exception as exc:  # noqa: BLE001 - degraded answers must never fail the turn
        log.warning("tutor.degrade_search_failed", error=type(exc).__name__)
    nb = _block("notice", notice, reply.id)
    out_blocks.append(nb)
    _emit({"type": "block", "block": nb})
    if citations:
        sb = _block("sources", {"items": citations}, reply.id)
        out_blocks.append(sb)
        _emit({"type": "block", "block": sb})
    return {
        "messages": [reply],
        "notice": notice,
        "blocks": out_blocks,
        "citations": citations,
        "degraded": True,
        "turn_calls": ctx.turn.calls,
    }


def route_tutor(state: TutorState) -> Literal["tools", "compose"]:
    msgs = state.get("messages") or []
    last = msgs[-1] if msgs else None
    if isinstance(last, AIMessage) and last.tool_calls and not state.get("degraded") and not state.get("refusal"):
        return "tools"
    return "compose"


# ── tools ──


async def tools(state: TutorState, config: RunnableConfig) -> dict[str, Any]:
    who = run_identity(config)
    assert who.user_id is not None
    cfg, rules = await _config()
    flags = await _flags_for(who)
    msgs = state.get("messages") or []
    ai = next((m for m in reversed(msgs) if isinstance(m, AIMessage) and m.tool_calls), None)
    if ai is None:
        return {}
    done = {m.tool_call_id for m in msgs if isinstance(m, ToolMessage)}
    batch = plan_pass(ai.tool_calls, done)
    # Flag and exam on every pass, also after a confirm; the page's revealed state again in a resumed run.
    fix = await _recheck(state, who, cfg, rules, flags)
    exam = (fix.get("refusal") or {}).get("reason") == "exam_mode"
    if fix.get("refusal") and not exam:
        return {**fix, **_stop(fix["refusal"]["reason"])}
    fix = {k: v for k, v in fix.items() if k != "refusal"}
    if fix:
        state = {**state, **fix}  # type: ignore[typeddict-item]
    ctx = ToolContext(
        user_id=who.user_id,
        is_admin=who.is_admin,
        run_id=who.run_id,
        thread_id=who.thread_id,
        db=agent_db(),
        exam_active=exam,
        page=page_from_state(state.get("page")),
        gateway=get_gateway(),
        call=_call_ctx(state, who, cfg),
        flags=flags,
        web_allowed=bool(state.get("web_allowed")),
        kb_low_score=cfg.kb_low_score,
        tool_result_max_chars=cfg.tool_result_max_chars,
        store=_store(),
        memory_max_items=cfg.memory_max_items,
        kb_best=state.get("kb_best"),
        web_urls=_turn_web_urls(state),
    )
    allowed = {s.name for s in tools_for(flags)}
    out_msgs: list[ToolMessage] = []
    blocks: list[dict[str, Any]] = []
    citations: list[dict[str, Any]] = []
    canvas: list[dict[str, Any]] = []
    for call in batch:
        name = str(call.get("name"))
        spec = get_tool(name)
        _emit({"type": "tool", "tool": name, "status": "running"})
        started = time.monotonic()
        try:
            result = await run_tool_call(ctx, name, call.get("args") or {}, allowed)
        except GraphInterrupt:
            raise  # the confirm: LangGraph pauses the run here and re-runs this node on resume
        except Exception as exc:  # noqa: BLE001 - one tool failing must not end the turn
            log.warning("tutor.tool_failed", tool=name, error=type(exc).__name__)
            result = ToolResult(content="[error: tool_failed]", status="error")
        status = {"ok": "done", "declined": "declined"}.get(result.status, "error")
        _emit({"type": "tool", "tool": name, "status": status})
        log.info(
            "tutor.tool",
            tool=name,
            status=result.status,
            writes=bool(spec and spec.writes),
            ms=int((time.monotonic() - started) * 1000),
            run_id=who.run_id,
        )
        for b in result.blocks:
            blk = _block(str(b.get("type")), dict(b.get("data") or {}), ai.id)
            blocks.append(blk)
            _emit({"type": "block", "block": blk})
        for c in result.citations:
            citations.append({**c, "message_id": ai.id})
            if c.get("layer") == "web" and isinstance(c.get("url"), str):
                ctx.web_urls.add(web_url_key(c["url"]))
        canvas.extend(result.canvas)
        out_msgs.append(ToolMessage(content=result.content, tool_call_id=str(call.get("id")), name=name))
    return {
        **fix,
        "messages": out_msgs,
        "blocks": blocks,
        "citations": [{**c, "id": f"{c['id']}@{ai.id}"} for c in citations],
        "canvas": canvas,
        "kb_best": ctx.kb_best,
    }


def route_tools(state: TutorState) -> Literal["tools", "tutor"]:
    if state.get("refusal"):
        return "tutor"  # tutor stops on a refusal; compose ends the turn
    msgs = state.get("messages") or []
    ai = next((m for m in reversed(msgs) if isinstance(m, AIMessage) and m.tool_calls), None)
    if ai is None:
        return "tutor"
    done = {m.tool_call_id for m in msgs if isinstance(m, ToolMessage)}
    return "tools" if any(tc.get("id") not in done for tc in ai.tool_calls) else "tutor"


# ── compose ──


async def compose(state: TutorState, config: RunnableConfig) -> dict[str, Any]:
    who = run_identity(config)
    assert who.user_id is not None
    cfg, _ = await _config()
    msgs = state.get("messages") or []
    start = int(state.get("turn_start") or 0)
    turn = msgs[start:]
    final = next((m for m in reversed(turn) if isinstance(m, AIMessage) and not m.tool_calls), None)
    if state.get("refusal"):
        final = None  # a turn stopped by a re-check: no answer to attach citations or the disclaimer to
    out: dict[str, Any] = {"attachments": [], "msg_count": len(msgs)}
    blocks: list[dict[str, Any]] = []
    # Citations the answer actually used ([n]), else every one this turn produced; each sent as an event.
    turn_ids = {m.id for m in turn if isinstance(m, AIMessage)}
    turn_cites = [c for c in state.get("citations") or [] if c.get("message_id") in turn_ids]
    if final is not None and turn_cites and not state.get("degraded"):
        used = {int(n) for n in _CITE.findall(_text(final))}
        chosen = [c for c in turn_cites if c.get("n") in used] or turn_cites
        final_cites = [{**c, "id": f"{c['id']}#final", "message_id": final.id} for c in chosen]
        for c in final_cites:
            _emit({"type": "citation", "citation": c})
        out["citations"] = final_cites
    if final is not None and cfg.disclaimer_every_thread and not _disclaimer_shown(state):
        b = _block("notice", {"kind": "disclaimer"}, final.id)
        blocks.append(b)
        _emit({"type": "block", "block": b})
        out["disclaimer_shown"] = True
    if blocks:
        out["blocks"] = blocks
    first_human = next((m for m in msgs if isinstance(m, HumanMessage)), None)
    title = threads.title_from(_text(first_human)) if first_human is not None else None
    if who.thread_id:
        try:
            due = await threads.record_turn(
                agent_db(),
                user_id=who.user_id,
                aegra_thread_id=uuid.UUID(who.thread_id),
                title=title,
                page_ref=page_ref_row(page_from_state(state.get("page"))),
                reflect_minutes=cfg.memory_reflect_minutes,
            )
        except Exception as exc:  # noqa: BLE001 - the answer stands even if the list row could not be written
            log.warning("tutor.thread_row_failed", error=type(exc).__name__)
            due = False
        if due and final is not None:
            _schedule_reflection(who, msgs[-24:])
    if state.get("turn_id"):
        get_gateway().turns.end(str(state["turn_id"]))
    return out


def _schedule_reflection(who: RunIdentity, messages: Sequence[BaseMessage]) -> None:
    """langmem's background memory manager, after the turn and outside its budget (suggestions only)."""
    store = _store()
    if store is None or who.user_id is None:
        return
    convo = [m for m in messages if isinstance(m, HumanMessage) or (isinstance(m, AIMessage) and not m.tool_calls)]
    convo = [m.__class__(content=_text(m), id=m.id) for m in convo if _text(m)]
    if len(convo) < 2:
        return

    async def run() -> None:
        try:
            n = await mem.reflect(store, who.user_id, convo, gateway=get_gateway(), run_id=who.run_id)  # type: ignore[arg-type]
            log.info("tutor.reflected", suggestions=n)
        except Exception as exc:  # noqa: BLE001 - background work: logged, never surfaced
            log.info("tutor.reflection_skipped", error=type(exc).__name__)

    task = asyncio.get_running_loop().create_task(run())
    _reflections.add(task)
    task.add_done_callback(_reflections.discard)


def build() -> StateGraph[TutorState, None, TutorInput, TutorState]:
    g: StateGraph[TutorState, None, TutorInput, TutorState] = StateGraph(TutorState, input_schema=TutorInput)
    g.add_node("guard", guard)
    g.add_node("refuse", refuse)
    g.add_node("tutor", tutor)
    g.add_node("tools", tools)
    g.add_node("compose", compose)
    g.add_edge(START, "guard")
    g.add_conditional_edges("guard", route_guard, {"refuse": "refuse", "tutor": "tutor"})
    g.add_edge("refuse", END)
    g.add_conditional_edges("tutor", route_tutor, {"tools": "tools", "compose": "compose"})
    g.add_conditional_edges("tools", route_tools, {"tools": "tools", "tutor": "tutor"})
    g.add_edge("compose", END)
    return g


graph = build()
