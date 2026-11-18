"""agent_runtime health carries structured facts (reason codes, graph ids, failing checks) for the client to
word with ui_copy; the English `detail` is for logs and ops only (REVIEW_CHECKLIST: no server sentences)."""

from __future__ import annotations

from naadi_api.health.checks import agent_runtime_check
from naadi_api.health.models import AgentRuntimeFacts, ComponentHealth


async def test_not_hosted_process_reports_a_reason_code() -> None:
    out = await agent_runtime_check(None)()
    assert out.status == "down"
    assert out.agent == AgentRuntimeFacts(reason="not_hosted")


def test_component_serialises_the_facts_and_omits_nothing_the_client_needs() -> None:
    facts = AgentRuntimeFacts(reason="checks_failed", graphs=["ping"], failing=["broker"])
    dumped = facts.model_dump(mode="json")
    assert dumped == {"reason": "checks_failed", "graphs": ["ping"], "failing": ["broker"], "last_cron_ping": None}
    assert "agent" in ComponentHealth.model_fields
