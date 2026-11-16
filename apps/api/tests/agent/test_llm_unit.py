"""Unit tests for the LLM gateway's guards (acceptance 1). Provider behaviour is scripted; no network."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import ValidationError

from naadi_api.llm import CallContext, LLMGateway, LLMUnavailable
from naadi_api.llm_budget import BudgetExhausted, TokenBucket, TurnLimitReached, provider_daily_cap, purpose_cap
from naadi_api.llm_config import (
    AgentConfig,
    RuntimeConfig,
    load_yaml_sections,
    sections_from_models_yaml,
    validate_section,
)
from naadi_api.llm_privacy import PrivacyContext, PrivacyUnavailable, VaultContentRejected, VaultDocument
from naadi_api.settings import Settings

from .fakes import Clock, FakeClients, MemLedger, ScriptedModel

CONFIG_DIR = Path(__file__).resolve().parents[4] / "config"


def sections(**patch: Any) -> dict[str, dict[str, Any]]:
    s = copy.deepcopy(load_yaml_sections(CONFIG_DIR))
    for dotted, value in patch.items():
        key, *path = dotted.split("__")
        node: Any = s[key]
        for p in path[:-1]:
            node = node[p]
        node[path[-1]] = value
    return s


class StaticRuntime(RuntimeConfig):
    def __init__(self, values: dict[str, dict[str, Any]]) -> None:
        super().__init__(None, CONFIG_DIR)
        self._values = values

    async def get(self) -> AgentConfig:
        return AgentConfig(
            models=validate_section_model("models", self._values["models"]),
            budget=validate_section_model("budget", self._values["budget"]),
            search=validate_section_model("search", self._values["search"]),
            generation=validate_section_model("generation", self._values["generation"]),
            source={k: "yaml" for k in self._values},
        )


def validate_section_model(key: str, value: Any) -> Any:
    from naadi_api.llm_config import SECTION_MODELS

    return SECTION_MODELS[key].model_validate(value)


def chain(*entries: tuple[str, str]) -> list[dict[str, str]]:
    return [{"provider": p, "model": m} for p, m in entries]


def gateway(
    values: dict[str, dict[str, Any]], clients: FakeClients, ledger: MemLedger | None = None, **settings: Any
) -> tuple[LLMGateway, MemLedger, Clock]:
    clock = Clock()
    led = ledger or MemLedger()
    gw = LLMGateway(
        Settings(_env_file=None, **settings),
        StaticRuntime(values),
        led,
        clients=clients,
        clock=clock,
        sleep=clock.sleep,
    )
    return gw, led, clock


def ctx(purpose: str = "chat", **kw: Any) -> CallContext:
    return CallContext(purpose=purpose, **kw)  # type: ignore[arg-type]


# ── free-only validation ──


def test_yaml_sections_validate_and_keep_the_stored_shape() -> None:
    s = load_yaml_sections(CONFIG_DIR)
    assert set(s) == {"models", "budget", "search", "generation"}
    assert "embeddings" in s["models"]["roles"]  # verbatim YAML shape (seg05 contract)
    assert validate_section("models", s["models"]) == s["models"]


@pytest.mark.parametrize("model", ["openai/gpt-5", "anthropic/claude-x", ":free", "vendor/model:freeish"])
def test_paid_openrouter_ids_are_rejected(model: str) -> None:
    s = sections()
    s["models"]["roles"]["tutor"]["chain"].append({"provider": "openrouter", "model": model})
    with pytest.raises(ValidationError, match="not free"):
        validate_section("models", s["models"])


def test_free_router_and_free_suffix_are_accepted() -> None:
    s = sections()
    s["models"]["roles"]["tutor"]["chain"] = chain(("openrouter", "openrouter/free"), ("openrouter", "x/y:free"))
    validate_section("models", s["models"])


def test_paid_embedding_fallback_rejected() -> None:
    s = sections()
    s["models"]["roles"]["embeddings"]["cloud_fallback"] = "openai/text-embedding-3-large"
    with pytest.raises(ValidationError):
        validate_section("models", s["models"])


def test_chain_provider_must_exist_and_budget_bounds() -> None:
    s = sections()
    del s["models"]["providers"]["nvidia"]
    with pytest.raises(ValidationError, match="not under providers"):
        validate_section("models", s["models"])
    b = dict(s["budget"], max_llm_calls_per_turn=50)
    with pytest.raises(ValidationError):
        validate_section("budget", b)
    with pytest.raises(ValidationError):
        validate_section("budget", dict(s["budget"], only_free_ids_in_chains=False))


def test_sections_from_yaml_matches_loader() -> None:
    import yaml

    raw = yaml.safe_load((CONFIG_DIR / "models.yaml").read_text())
    assert sections_from_models_yaml(raw) == load_yaml_sections(CONFIG_DIR)


# ── fallback order ──


async def test_fallback_walks_the_chain_in_order_on_429_5xx_and_410() -> None:
    s = sections()
    s["models"]["roles"]["tutor"]["chain"] = chain(
        ("nvidia", "a/one"), ("openrouter", "b/two:free"), ("openrouter", "c/three:free"), ("nvidia", "d/four")
    )
    clients = FakeClients(
        {
            ("nvidia", "a/one"): ScriptedModel(fail_status=429, seen=[], seen_kwargs=[]),
            ("openrouter", "b/two:free"): ScriptedModel(fail_status=503, seen=[], seen_kwargs=[]),
            ("openrouter", "c/three:free"): ScriptedModel(fail_status=410, seen=[], seen_kwargs=[]),
        }
    )
    gw, led, _ = gateway(s, clients)
    out = await gw.ainvoke("tutor", [HumanMessage("hi")], ctx())
    assert out.content == "nvidia:d/four"
    assert out.response_metadata["naadi_model"] == "d/four"
    assert clients.calls == [
        ("nvidia", "a/one"),
        ("openrouter", "b/two:free"),
        ("openrouter", "c/three:free"),
        ("nvidia", "d/four"),
    ]
    statuses = [(r["row"].model, r["ok"], r.get("status")) for r in led.rows.values()]
    assert statuses == [
        ("a/one", False, 429),
        ("b/two:free", False, 503),
        ("c/three:free", False, 410),
        ("d/four", True, 200),
    ]
    # The retired model is skipped on the next call without spending a request.
    clients.calls.clear()
    await gw.ainvoke("tutor", [HumanMessage("again")], ctx())
    assert ("openrouter", "c/three:free") not in clients.calls


async def test_all_failing_raises_unavailable_not_budget() -> None:
    s = sections()
    s["models"]["roles"]["router"]["chain"] = chain(("nvidia", "a/one"))
    gw, _, _ = gateway(s, FakeClients({("nvidia", "a/one"): ScriptedModel(fail_status=500, seen=[], seen_kwargs=[])}))
    with pytest.raises(LLMUnavailable) as e:
        await gw.ainvoke("router", [HumanMessage("x")], ctx())
    assert e.value.attempts[0]["status"] == 500


async def test_unconfigured_provider_is_skipped() -> None:
    s = sections()
    s["models"]["roles"]["router"]["chain"] = chain(("nvidia", "a/one"), ("openrouter", "b/two:free"))
    clients = FakeClients({}, keys={"nvidia": False, "openrouter": True})
    gw, _, _ = gateway(s, clients)
    out = await gw.ainvoke("router", [HumanMessage("x")], ctx())
    assert out.response_metadata["naadi_provider"] == "openrouter"


async def test_streaming_yields_tokens_and_falls_back_before_the_first_token() -> None:
    s = sections()
    s["models"]["roles"]["router"]["chain"] = chain(("openrouter", "a/x:free"), ("openrouter", "b/y:free"))
    clients = FakeClients(
        {
            ("openrouter", "a/x:free"): ScriptedModel(fail_status=429, seen=[], seen_kwargs=[]),
            ("openrouter", "b/y:free"): ScriptedModel(reply="one two three", seen=[], seen_kwargs=[]),
        }
    )
    gw, _, _ = gateway(s, clients)
    model = gw.chat_model("router", ctx())
    parts = [c.content async for c in model.astream([HumanMessage("x")])]
    assert "".join(str(p) for p in parts).split() == ["one", "two", "three"]


# ── rpm bucket ──


async def test_token_bucket_refills_continuously() -> None:
    clock = Clock()
    b = TokenBucket(18, clock=clock)
    assert all([await b.try_take() for _ in range(18)])
    assert not await b.try_take()
    assert b.wait_s() == pytest.approx(60 / 18)
    clock.t += 60 / 18
    assert await b.try_take()


async def test_rate_exhausted_provider_is_skipped_then_budget_exhausted() -> None:
    s = sections()
    s["models"]["providers"]["openrouter"]["rpm"] = 2
    s["models"]["providers"]["nvidia"]["rpm"] = 1
    s["models"]["roles"]["router"]["chain"] = chain(("nvidia", "a/n"), ("openrouter", "b/o:free"))
    s["budget"]["rate_wait_s"] = 0
    clients = FakeClients({})
    gw, _, _ = gateway(s, clients)
    first = await gw.ainvoke("router", [HumanMessage("1")], ctx())
    second = await gw.ainvoke("router", [HumanMessage("2")], ctx())
    third = await gw.ainvoke("router", [HumanMessage("3")], ctx())
    assert [m.response_metadata["naadi_provider"] for m in (first, second, third)] == [
        "nvidia",
        "openrouter",
        "openrouter",
    ]
    with pytest.raises(BudgetExhausted) as e:
        await gw.ainvoke("router", [HumanMessage("4")], ctx())
    assert e.value.reason == "rate_limited"


async def test_rate_limited_call_waits_within_rate_wait_s() -> None:
    s = sections()
    s["models"]["providers"]["nvidia"]["rpm"] = 60  # one token per second
    s["models"]["roles"]["router"]["chain"] = chain(("nvidia", "a/n"))
    s["budget"]["rate_wait_s"] = 3
    gw, _, clock = gateway(s, FakeClients({}))
    for _ in range(60):
        await gw.ainvoke("router", [HumanMessage("x")], ctx("job"))
    t0 = clock.t
    await gw.ainvoke("router", [HumanMessage("x")], ctx("job"))
    assert 0.9 < clock.t - t0 < 1.2


# ── daily cap + 70/30 reserve ──


async def test_daily_cap_split_between_chat_and_jobs() -> None:
    s = sections()
    s["models"]["providers"]["openrouter"]["daily_cap"] = None
    s["budget"]["daily_request_cap"] = 1000
    s["models"]["roles"]["router"]["chain"] = chain(("openrouter", "b/o:free"))
    s["models"]["providers"]["openrouter"]["rpm"] = 10_000
    gw, led, _ = gateway(s, FakeClients({}), openrouter_daily_cap=10)
    cfg = await gw.config()
    assert provider_daily_cap(cfg, "openrouter", 10) == 10
    assert purpose_cap(cfg, "openrouter", "chat", 10) == 7
    assert purpose_cap(cfg, "openrouter", "job", 10) == 3
    for _ in range(3):
        await gw.ainvoke("router", [HumanMessage("j")], ctx("job"))
    with pytest.raises(BudgetExhausted) as e:
        await gw.ainvoke("router", [HumanMessage("j")], ctx("job"))
    assert e.value.reason == "daily_cap"
    assert e.value.reset_at.hour == 0 and e.value.reset_at.minute == 0
    # Jobs being out does not touch the tutor's reserve.
    for _ in range(7):
        await gw.ainvoke("router", [HumanMessage("c")], ctx("chat"))
    with pytest.raises(BudgetExhausted):
        await gw.ainvoke("router", [HumanMessage("c")], ctx("chat"))
    assert len(led.rows) == 10


async def test_cap_reached_on_one_provider_falls_to_the_next() -> None:
    s = sections()
    s["models"]["providers"]["nvidia"]["daily_cap"] = 0
    s["models"]["roles"]["router"]["chain"] = chain(("nvidia", "a/n"), ("openrouter", "b/o:free"))
    gw, _, _ = gateway(s, FakeClients({}))
    out = await gw.ainvoke("router", [HumanMessage("x")], ctx())
    assert out.response_metadata["naadi_provider"] == "openrouter"


async def test_cap_forced_to_zero_exhausts_everything() -> None:
    s = sections()
    s["budget"]["daily_request_cap"] = 0
    s["models"]["providers"]["nvidia"]["daily_cap"] = 0
    gw, _, _ = gateway(s, FakeClients({}))
    with pytest.raises(BudgetExhausted) as e:
        await gw.ainvoke("tutor", [HumanMessage("x")], ctx())
    assert e.value.reason == "daily_cap"


# ── 4 calls per turn ──


async def test_turn_stops_after_max_llm_calls() -> None:
    s = sections()
    s["models"]["roles"]["router"]["chain"] = chain(("nvidia", "a/n"))
    gw, _, _ = gateway(s, FakeClients({}))
    c = gw.context_for_run("chat", "run-1")
    for _ in range(4):
        await gw.ainvoke("router", [HumanMessage("x")], c)
    with pytest.raises(TurnLimitReached):
        await gw.ainvoke("router", [HumanMessage("x")], c)
    # Same run id → same counter; a new run starts fresh.
    with pytest.raises(TurnLimitReached):
        await gw.ainvoke("router", [HumanMessage("x")], gw.context_for_run("chat", "run-1"))
    await gw.ainvoke("router", [HumanMessage("x")], gw.context_for_run("chat", "run-2"))


# ── PII stripping ──


async def test_pii_is_stripped_before_the_provider_sees_the_prompt() -> None:
    s = sections()
    s["models"]["roles"]["router"]["chain"] = chain(("openrouter", "b/o:free"))
    model = ScriptedModel(seen=[], seen_kwargs=[])
    gw, _, _ = gateway(s, FakeClients({("openrouter", "b/o:free"): model}))
    privacy = PrivacyContext.from_profile(
        display_name="Ananya Iyer",
        email="ananya.iyer@example.org",
        phone="+91 99887 76655",
        settings={"college": "Seth GS Medical College"},
    )
    text = (
        "I'm Ananya Iyer, ananya.iyer@example.org, +91 99887 76655, passport Z9876543, user "
        "3f2b8c1e-1d2a-4b9e-9c1a-0e5f6a7b8c9d at Seth GS Medical College. Other mail: x@y.com; ph 555 123 4567 890. "
        "ABG: pH 7.21, HCO3 14, PaCO2 28, BP 120/80, PMID 31234567."
    )
    await gw.ainvoke("router", [HumanMessage(text)], ctx(privacy=privacy))
    sent = str(model.seen[0][0].content)
    for leaked in ("Ananya", "Iyer", "ananya.iyer", "99887", "Z9876543", "3f2b8c1e", "Seth GS", "x@y.com", "555 123"):
        assert leaked not in sent, leaked
    for kept in ("pH 7.21", "HCO3 14", "PaCO2 28", "120/80", "PMID 31234567"):
        assert kept in sent, kept
    assert "[name]" in sent and "[email]" in sent and "[phone]" in sent and "[id]" in sent and "[profile]" in sent


async def test_pii_stripped_from_tool_call_args_too() -> None:
    s = sections()
    s["models"]["roles"]["router"]["chain"] = chain(("openrouter", "b/o:free"))
    model = ScriptedModel(seen=[], seen_kwargs=[])
    gw, _, _ = gateway(s, FakeClients({("openrouter", "b/o:free"): model}))
    privacy = PrivacyContext.from_profile(display_name="Ravi Kumar", email=None)
    ai = AIMessage(content="", tool_calls=[{"name": "save_note", "args": {"body": "Ravi Kumar's note"}, "id": "t1"}])
    await gw.ainvoke("router", [HumanMessage("x"), ai], ctx(privacy=privacy))
    sent_ai = model.seen[0][1]
    assert isinstance(sent_ai, AIMessage)
    assert sent_ai.tool_calls[0]["args"]["body"] == "[name]'s note"


# ── vault can never reach the LLM ──


@pytest.mark.parametrize(
    "message",
    [
        HumanMessage(content=[{"type": "text", "text": "summarise", "naadi_source": "vault"}]),
        HumanMessage(content="x", additional_kwargs={"naadi_source": "vault"}),
        HumanMessage(
            content=[
                {
                    "type": "image_url",
                    "image_url": {"url": "https://p.supabase.co/storage/v1/object/sign/vault/u/passport.jpg?token=t"},
                }
            ]
        ),
        HumanMessage(content=[{"type": "text", "text": "doc", "doc": VaultDocument(b"MRZ<<", "u/passport.pdf")}]),  # type: ignore[list-item]
    ],
)
async def test_vault_material_is_rejected_before_any_provider_call(message: HumanMessage) -> None:
    s = sections()
    clients = FakeClients({})
    gw, led, _ = gateway(s, clients)
    with pytest.raises(VaultContentRejected):
        await gw.ainvoke("tutor", [message], ctx())
    assert clients.calls == [] and led.rows == {}


def test_vault_document_hides_its_bytes() -> None:
    d = VaultDocument(b"P<IND<<SECRET", "u/p.pdf")
    assert "SECRET" not in repr(d) and "SECRET" not in str(d)
    assert not isinstance(d, str | bytes)


# ── reviewer independence + PDFs ──


async def test_reviewer_from_author_family_is_marked_not_independent() -> None:
    s = sections()
    s["models"]["roles"]["reviewer"]["chain"] = chain(("openrouter", "nvidia/nemotron-x:free"))
    gw, _, _ = gateway(s, FakeClients({}))
    out = await gw.ainvoke("reviewer", [HumanMessage("solve")], ctx("job", author_model="nvidia/other-model"))
    assert out.response_metadata["review_not_independent"] is True
    out = await gw.ainvoke("reviewer", [HumanMessage("solve")], ctx("job", author_model="baidu/ernie"))
    assert out.response_metadata["review_not_independent"] is False


async def test_pdf_goes_only_to_openrouter_with_the_free_parser_engine() -> None:
    s = sections()
    s["models"]["roles"]["long_context"]["chain"] = chain(("nvidia", "a/n"), ("openrouter", "b/o:free"))
    model = ScriptedModel(seen=[], seen_kwargs=[])
    clients = FakeClients({("openrouter", "b/o:free"): model})
    gw, _, _ = gateway(s, clients)
    msg = HumanMessage(
        content=[
            {"type": "text", "text": "summarise"},
            {"type": "file", "file": {"filename": "a.pdf", "file_data": "data:application/pdf;base64,AAAA"}},
        ]
    )
    await gw.ainvoke("long_context", [msg], ctx("job"))
    assert clients.calls == [("openrouter", "b/o:free")]
    assert model.seen_kwargs[0]["plugins"] == [{"id": "file-parser", "pdf": {"engine": "cloudflare-ai"}}]


async def test_runtime_config_falls_back_to_yaml_without_db() -> None:
    rc = RuntimeConfig(None, CONFIG_DIR)
    cfg = await rc.get()
    assert cfg.source == {"models": "yaml", "budget": "yaml", "search": "yaml", "generation": "yaml"}
    assert cfg.models.roles["tutor"].chain


async def test_zero_cap_is_budget_exhausted_even_without_keys() -> None:
    s = sections()
    s["models"]["roles"]["router"]["chain"] = chain(("openrouter", "b/o:free"), ("nvidia", "a/n"))
    gw, _, _ = gateway(s, FakeClients({}, keys={"nvidia": False, "openrouter": False}), openrouter_daily_cap=0)
    with pytest.raises(BudgetExhausted) as e:
        await gw.ainvoke("router", [HumanMessage("x")], ctx())
    assert e.value.reason == "daily_cap"


async def test_no_keys_and_no_zero_cap_is_unavailable() -> None:
    s = sections()
    gw, _, _ = gateway(s, FakeClients({}, keys={"nvidia": False, "openrouter": False}))
    with pytest.raises(LLMUnavailable):
        await gw.ainvoke("router", [HumanMessage("x")], ctx())


# ── privacy loaded by the gateway itself (review item 2) ──


class StaticPrivacy:
    def __init__(self, ctx: PrivacyContext | None) -> None:
        self.ctx = ctx
        self.calls = 0

    async def get(self, user_id: Any) -> PrivacyContext:
        self.calls += 1
        if self.ctx is None:
            raise PrivacyUnavailable("unknown user")
        return self.ctx


async def test_gateway_loads_the_students_privacy_itself() -> None:
    import uuid as _uuid

    s = sections()
    s["models"]["roles"]["router"]["chain"] = chain(("openrouter", "b/o:free"))
    model = ScriptedModel()
    gw, _, _ = gateway(s, FakeClients({("openrouter", "b/o:free"): model}))
    directory = StaticPrivacy(PrivacyContext.from_profile(display_name="Kavya Menon", email="kavya@example.net"))
    gw._privacy = directory  # type: ignore[assignment]
    # The caller passes NO privacy context: only the user id.
    await gw.ainvoke("router", [HumanMessage("Kavya Menon here, kavya@example.net")], ctx(user_id=_uuid.uuid4()))
    sent = str(model.seen[0][0].content)
    assert "Kavya" not in sent and "Menon" not in sent and "kavya@" not in sent
    assert directory.calls == 1


async def test_user_call_is_refused_when_privacy_cannot_be_loaded() -> None:
    import uuid as _uuid

    s = sections()
    clients = FakeClients({})
    gw, led, _ = gateway(s, clients)
    with pytest.raises(PrivacyUnavailable):  # no directory configured
        await gw.ainvoke("router", [HumanMessage("x")], ctx(user_id=_uuid.uuid4()))
    gw._privacy = StaticPrivacy(None)  # type: ignore[assignment]
    with pytest.raises(PrivacyUnavailable):  # directory cannot build it
        await gw.ainvoke("router", [HumanMessage("x")], ctx(user_id=_uuid.uuid4()))
    assert clients.calls == [] and led.rows == {}


# ── per-turn cap counts provider attempts (review item 7) ──


async def test_turn_cap_counts_fallback_attempts() -> None:
    s = sections()
    s["models"]["roles"]["router"]["chain"] = chain(
        ("openrouter", "a/1:free"), ("openrouter", "a/2:free"), ("openrouter", "a/3:free"), ("nvidia", "a/4")
    )
    failing = {("openrouter", f"a/{i}:free"): ScriptedModel(fail_status=503) for i in (1, 2, 3)}
    gw, led, _ = gateway(s, FakeClients(failing))
    c = gw.context_for_run("chat", "run-attempts")
    out = await gw.ainvoke("router", [HumanMessage("x")], c)  # 3 failures + 1 success = 4 provider requests
    assert out.response_metadata["naadi_provider"] == "nvidia"
    assert len(led.rows) == 4
    with pytest.raises(TurnLimitReached):
        await gw.ainvoke("router", [HumanMessage("again")], c)
    assert len(led.rows) == 4  # the refused call made no request


async def test_turn_cap_stops_a_chain_mid_walk() -> None:
    s = sections()
    s["budget"]["max_llm_calls_per_turn"] = 2
    s["models"]["roles"]["router"]["chain"] = chain(
        ("openrouter", "a/1:free"), ("openrouter", "a/2:free"), ("nvidia", "a/3")
    )
    failing = {("openrouter", f"a/{i}:free"): ScriptedModel(fail_status=429) for i in (1, 2)}
    gw, led, _ = gateway(s, FakeClients(failing))
    with pytest.raises(TurnLimitReached):
        await gw.ainvoke("router", [HumanMessage("x")], gw.context_for_run("chat", "run-mid"))
    assert len(led.rows) == 2
