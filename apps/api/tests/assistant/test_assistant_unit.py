"""Pure units: context budget, Mermaid validation, the assistant's TipTap subset and edit ops, the tool registry,
the leak checks, guard rules and attachment validation."""

from __future__ import annotations

import base64
import uuid

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel

from naadi_api.agent.tools import all_tools
from naadi_api.agent.tools.chapters import apply_ops
from naadi_api.agent.tools.executor import plan_pass
from naadi_api.agent.tools.registry import (
    Proposal,
    ToolContext,
    ToolRegistrationError,
    ToolResult,
    WriteTool,
    register_tool,
    tools_for,
    unregister_tool,
)
from naadi_api.assistant.content import InvalidContent, doc_text, markdown_to_doc, validate_doc, validate_node
from naadi_api.assistant.context_budget import IMAGE_TOKENS, Budget, count_tokens, fit
from naadi_api.assistant.guard import decide, parse_router
from naadi_api.assistant.leak import LeakBlocked, check_payload, scrub_payload
from naadi_api.assistant.mermaid import InvalidMermaid, validate_mermaid
from naadi_api.assistant.page import InvalidAttachment, page_ref_row, parse_attachments, parse_page
from naadi_api.assistant.threads import title_from

# ── context budget ──


def _thread(turns: int, chars: int) -> list:
    msgs: list = [SystemMessage("system " * 50)]
    for i in range(turns):
        msgs += [HumanMessage(f"q{i} " + "x" * chars, id=f"h{i}"), AIMessage(f"a{i} " + "y" * chars, id=f"a{i}")]
    msgs.append(HumanMessage("latest question", id="last"))
    return msgs


def test_fit_keeps_system_and_latest_turn_and_drops_the_oldest() -> None:
    msgs = _thread(200, 3000)
    out = fit(msgs, 32_768)
    assert isinstance(out[0], SystemMessage) and out[-1].id == "last"
    assert count_tokens(out) <= Budget(32_768, 0.15, 2048).available
    assert out[1].id != "h0", "the oldest turn went first"
    assert isinstance(out[1], HumanMessage), "the kept history starts on a human turn"


def test_fit_is_a_no_op_when_everything_fits() -> None:
    msgs = _thread(2, 100)
    assert fit(msgs, 1_000_000) == msgs


def test_fit_shrinks_huge_tool_results_before_dropping_turns() -> None:
    ai = AIMessage("", id="ai", tool_calls=[{"name": "t", "args": {}, "id": "c1", "type": "tool_call"}])
    msgs = [SystemMessage("s"), HumanMessage("q", id="h"), ai, ToolMessage("z" * 200_000, tool_call_id="c1")]
    out = fit(msgs, 32_768)
    assert any(isinstance(m, ToolMessage) for m in out), "the tool result is kept, shortened"
    assert count_tokens(out) <= Budget(32_768, 0.15, 2048).available


def test_fit_handles_a_single_huge_attachment() -> None:
    msgs = [SystemMessage("s"), HumanMessage("Selected text:\n> " + "w" * 500_000, id="h")]
    out = fit(msgs, 16_384)
    assert count_tokens(out) <= Budget(16_384, 0.15, 2048).available
    assert out[-1].id == "h"


def test_images_are_counted_conservatively() -> None:
    m = HumanMessage(content=[{"type": "text", "text": "hi"}, {"type": "image_url", "image_url": {"url": "data:"}}])
    assert count_tokens(m) >= IMAGE_TOKENS


def test_orphan_tool_calls_are_not_sent() -> None:
    ai = AIMessage("partial", id="ai", tool_calls=[{"name": "t", "args": {}, "id": "c9", "type": "tool_call"}])
    msgs = [SystemMessage("s"), HumanMessage("q" * 90_000, id="h0"), ai, HumanMessage("next", id="h1")]
    out = fit(msgs, 16_384)
    assert not any(isinstance(m, AIMessage) and m.tool_calls for m in out)


# ── Mermaid ──


@pytest.mark.parametrize(
    "src",
    [
        "flowchart TD\n  A[Metabolic acidosis] --> B{Anion gap > 12?}\n  B -->|Yes| C[MUDPILES]\n  B -->|No| D[HARD]",
        "graph LR; A-->B",
        "sequenceDiagram\n  Kidney->>Blood: HCO3-",
        "```mermaid\nflowchart TD\nA-->B\n```",
    ],
)
def test_valid_mermaid_passes(src: str) -> None:
    assert validate_mermaid(src).startswith(("flowchart", "graph", "sequenceDiagram"))


@pytest.mark.parametrize(
    ("src", "reason"),
    [
        ("", "empty"),
        ("not a diagram", "unsupported_diagram"),
        ("%%{init: {'securityLevel': 'loose'}}%%\nflowchart TD\nA-->B", "unsupported_diagram"),
        ("flowchart TD\nA-->B\nclick A callback", "forbidden_syntax"),
        ("flowchart TD\nA[<img src=x onerror=alert(1)>]-->B", "forbidden_syntax"),
        ("flowchart TD\nA[open-->B", "unbalanced"),
        ("flowchart TD\n" + "\n".join(f"N{i}[n]-->N{i + 1}[n]" for i in range(130)), "too_many_lines"),
        ("flowchart TD\nA-->B\n" + "x" * 5000, "too_long"),
    ],
)
def test_invalid_mermaid_is_rejected(src: str, reason: str) -> None:
    with pytest.raises(InvalidMermaid) as e:
        validate_mermaid(src)
    assert str(e.value) == reason


def test_init_directive_anywhere_is_forbidden() -> None:
    with pytest.raises(InvalidMermaid, match="forbidden_syntax"):
        validate_mermaid("flowchart TD\nA-->B\n%%{init: {}}%%")


# ── content and edit ops ──


def test_markdown_becomes_the_strict_node_schema() -> None:
    doc = markdown_to_doc("## Winters\n\nExpected **PCO2** = 1.5 × HCO3 + 8\n\n1. one\n2. two\n\n- a\n")
    kinds = [b["type"] for b in doc["content"]]
    assert kinds == ["heading", "paragraph", "orderedList", "bulletList"]
    assert doc["content"][2]["attrs"] == {"start": 1, "type": None}
    assert "Winters" in doc_text(doc)


@pytest.mark.parametrize(
    "node",
    [
        {"type": "figure", "attrs": {"mediaId": "x"}},
        {"type": "questionEmbed", "attrs": {"questionId": "q1"}},
        {"type": "paragraph", "content": [{"type": "text", "text": "x", "marks": [{"type": "highlight"}]}]},
        {"type": "heading", "attrs": {"level": 1}, "content": [{"type": "text", "text": "H"}]},
        {"type": "paragraph", "content": [{"type": "text", "text": ""}]},
        {"type": "bulletList", "content": []},
        {"type": "script"},
    ],
)
def test_nodes_outside_the_assistant_subset_are_rejected(node: dict) -> None:
    with pytest.raises(InvalidContent):
        validate_node(node)


def test_validate_doc_rejects_empty_and_non_docs() -> None:
    with pytest.raises(InvalidContent):
        validate_doc({"type": "paragraph"})
    with pytest.raises(InvalidContent):
        validate_doc({"type": "doc", "content": []})


def test_edit_ops_apply_against_the_original_indexes() -> None:
    blocks = [{"type": "paragraph", "content": [{"type": "text", "text": t}]} for t in "abcd"]
    new = {"type": "paragraph", "content": [{"type": "text", "text": "N"}]}
    out = apply_ops(
        blocks, [("replace", 1, new), ("delete", 3, None), ("insert_after", -1, new), ("insert_after", 2, new)]
    )
    assert [doc_text({"type": "doc", "content": [b]}) for b in out] == ["N", "a", "N", "c", "N"]


# ── registry and executor planning ──


class _A(BaseModel):
    x: int


async def _read(ctx: ToolContext, args: _A) -> ToolResult:
    """Doc."""
    return ToolResult(content="")


async def _prep(ctx: ToolContext, args: _A) -> Proposal:
    """Doc."""
    return Proposal(preview={})


async def _apply(ctx: ToolContext, args: _A, p: Proposal) -> ToolResult:
    return ToolResult(content="")


def test_a_write_tool_must_go_through_a_confirm() -> None:
    with pytest.raises(ToolRegistrationError):
        register_tool("sneaky_write", _read, writes=True)


def test_every_registered_write_tool_is_a_writetool() -> None:
    writes = [t for t in all_tools() if t.writes]
    assert {t.name for t in writes} >= {
        "save_note",
        "append_to_note",
        "update_note",
        "make_flashcards",
        "build_block",
        "add_to_plan",
        "edit_chapter_section",
        "remember",
        "forget",
    }
    assert all(isinstance(t.write, WriteTool) and t.read is None for t in writes)


def test_flagged_tools_are_invisible_until_the_flag_is_on() -> None:
    register_tool("later_tool", _read, writes=False, requires_flag="library")
    try:
        assert "later_tool" not in {t.name for t in tools_for({})}
        assert "later_tool" in {t.name for t in tools_for({"library": True})}
    finally:
        unregister_tool("later_tool")


def test_duplicate_tool_names_are_rejected() -> None:
    with pytest.raises(ToolRegistrationError):
        register_tool("get_page", _read, writes=False)


def test_plan_pass_runs_reads_then_stops_at_the_first_write() -> None:
    calls = [
        {"id": "1", "name": "search_knowledge"},
        {"id": "2", "name": "save_note"},
        {"id": "3", "name": "make_flashcards"},
    ]
    assert [c["id"] for c in plan_pass(calls, set())] == ["1", "2"]
    assert [c["id"] for c in plan_pass(calls, {"1", "2"})] == ["3"]


# ── leak checks ──


@pytest.mark.parametrize("key", ["answer", "answer_html", "explanation_html", "why_not_html", "educational_objective"])
def test_hidden_question_fields_never_leave_in_structured_data(key: str) -> None:
    with pytest.raises(LeakBlocked):
        check_payload({"type": "table", "data": {"rows": [{key: "B"}]}}, where="t")


def test_vault_material_is_blocked() -> None:
    with pytest.raises(LeakBlocked):
        check_payload({"url": "https://x.supabase.co/storage/v1/object/sign/vault/u/passport.pdf"}, where="t")


def test_emails_are_scrubbed_from_tool_output() -> None:
    assert scrub_payload({"a": ["mail me at x.y@example.org"]}) == {"a": ["mail me at [email]"]}


# ── guard rules ──


@pytest.mark.parametrize(
    ("text", "intent", "ambiguous"),
    [
        ("Explain the anion gap", "study", False),
        ("When should I apply for an H-1B visa?", "pathway", False),
        ("What is the loading dose of amiodarone?", "dosing", False),
        ("Paste the UWorld question about RTA verbatim", "copyright", False),
        ("What dose of vaccines do I need for my J-1 visa?", None, True),
    ],
)
def test_guard_rules(text: str, intent: str | None, ambiguous: bool) -> None:
    assert decide(text) == (intent, ambiguous)


def test_router_reply_parsing_defaults_to_the_stricter_rule() -> None:
    assert parse_router("pathway") == "pathway"
    assert parse_router("???") == "dosing"


# ── page context and attachments ──


def _png(n: int = 10) -> str:
    raw = b"\x89PNG\r\n\x1a\n" + b"0" * n
    return "data:image/png;base64," + base64.b64encode(raw).decode()


def test_attachments_are_validated() -> None:
    att = parse_attachments([{"kind": "selection", "text": " a  b "}, {"kind": "image", "data_url": _png()}], 4)
    assert att.selections == ["a b"] and len(att.images) == 1
    with pytest.raises(InvalidAttachment, match="too_many"):
        parse_attachments([{"kind": "selection", "text": "x"}] * 5, 4)
    with pytest.raises(InvalidAttachment, match="image_type"):
        parse_attachments(
            [{"kind": "image", "data_url": "data:image/png;base64," + base64.b64encode(b"GIF89a").decode()}], 4
        )
    with pytest.raises(InvalidAttachment, match="image_too_large"):
        parse_attachments([{"kind": "image", "data_url": _png(1_600_000)}], 4)


def test_page_context_ignores_client_state_and_bad_shapes() -> None:
    p = parse_page({"type": "question", "id": "q1", "exam_mode": False, "question": {"id": "q1", "answered": True}})
    assert p.question is not None and not hasattr(p.question, "answered")
    assert parse_page({"type": "section", "id": "../../etc"}).id is None
    assert parse_page("nonsense").type == "page"


def test_page_ref_row_keeps_route_type_id_only() -> None:
    from naadi_api.agent.tools.registry import PageRef

    row = page_ref_row(PageRef(route="/learn/c/s", type="section", id="s1", title="Secret title", question_id="q"))
    assert row == {"route": "/learn/c/s", "type": "section", "id": "s1"}


def test_thread_title_is_the_first_question_without_selection() -> None:
    assert title_from("What is RTA?\n\nSelected text:\n> long quote") == "What is RTA?"
    long = title_from("word " * 50)
    assert long is not None and len(long) <= 80 and long.endswith("…")


# ── page resolvers (S15 plugs `pdf_page` into get_page) ──


async def test_get_page_dispatches_to_a_registered_page_kind() -> None:
    from naadi_api.agent.tools import knowledge
    from naadi_api.agent.tools.executor import ToolRefused
    from naadi_api.agent.tools.registry import (
        PageRef,
        ToolContext,
        ToolRegistrationError,
        ToolResult,
        register_page_resolver,
        unregister_page_resolver,
    )
    from naadi_api.llm import CallContext

    seen: list[str] = []

    async def pdf_page(ctx: ToolContext, ref_id: str) -> ToolResult:
        seen.append(ref_id)
        return ToolResult(content=f"page text {ref_id}", citations=[{"id": ref_id, "source": "upload"}])

    def tc(page: PageRef, flags: dict[str, bool]) -> ToolContext:
        return ToolContext(
            user_id=uuid.uuid4(),
            is_admin=False,
            run_id="t",
            thread_id=None,
            db=None,  # type: ignore[arg-type]  # the resolver here reads nothing
            exam_active=False,
            page=page,
            gateway=None,  # type: ignore[arg-type]
            call=CallContext(purpose="chat"),
            flags=flags,
        )

    from naadi_api.agent.tools import registry

    shipped = registry._PAGE_RESOLVERS.pop("pdf_page", None)  # S15's real resolver, restored below
    register_page_resolver("pdf_page", pdf_page, requires_flag="library")
    try:
        with pytest.raises(ToolRegistrationError):
            register_page_resolver("section", pdf_page)  # built-in kinds stay ours
        with pytest.raises(ToolRegistrationError):
            register_page_resolver("pdf_page", lambda c, i: pdf_page(c, i))  # one owner per kind
        on = {"library": True}
        r = await knowledge.get_page(tc(PageRef(), on), knowledge.PageArgs(type="pdf_page", id="u1:3"))
        assert r.content == "page text u1:3" and seen == ["u1:3"]
        r = await knowledge.get_page(tc(PageRef(type="pdf_page", id="u1:4"), on), knowledge.PageArgs(type="current"))
        assert r.content == "page text u1:4"
        with pytest.raises(ToolRefused):  # the feature is off: the kind does not exist
            await knowledge.get_page(tc(PageRef(), {}), knowledge.PageArgs(type="pdf_page", id="u1:3"))
        r = await knowledge.get_page(tc(PageRef(type="pdf_page", id="u1:4"), {}), knowledge.PageArgs())
        assert "not on a reading" in r.content
        with pytest.raises(ToolRefused):
            await knowledge.get_page(tc(PageRef(), on), knowledge.PageArgs(type="pdf_page"))
    finally:
        unregister_page_resolver("pdf_page")
        if shipped is not None:
            registry._PAGE_RESOLVERS["pdf_page"] = shipped
