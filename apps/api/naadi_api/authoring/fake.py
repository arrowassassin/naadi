"""A scripted stand-in for the model gateway, for tests and the non-production e2e (`AUTHORING_FAKE=1`, ignored when
APP_ENV=prod): it writes a well-formed item grounded on the excerpts it is shown and answers the blind solver
correctly from a model of another family, so the whole pipeline can be exercised without a model or a network.
It never runs in production and spends no quota."""

from __future__ import annotations

import itertools
import json
import re
from types import SimpleNamespace
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage

from ..llm import CallContext

AUTHOR_MODEL = "nvidia/fake-author"
SOLVER_MODEL = "particle-ai/fake-reviewer"
_EXCERPT = re.compile(r"^\[([^\]]+)\] (.+)$", re.M)
_SENT = re.compile(r"(?<=[.?!])\s+")


class FakeAuthoringGateway:
    def __init__(self) -> None:
        self._n = itertools.count(1)
        self.calls: list[str] = []

    async def config(self) -> Any:
        return SimpleNamespace(roles={"author": SimpleNamespace(chain=[SimpleNamespace(model=AUTHOR_MODEL)])})

    async def ainvoke(self, role: str, messages: list[BaseMessage], ctx: CallContext, **kw: Any) -> AIMessage:
        text = "\n".join(str(m.content) for m in messages)
        self.calls.append(role)
        if role == "author":
            return AIMessage(content=json.dumps(self._draft(text)), response_metadata=self._meta(AUTHOR_MODEL))
        return AIMessage(content=json.dumps(self._solve(text)), response_metadata=self._meta(SOLVER_MODEL))

    @staticmethod
    def _meta(model: str) -> dict[str, Any]:
        return {"naadi_model": model, "review_not_independent": False}

    def _draft(self, prompt: str) -> dict[str, Any]:
        n = next(self._n)
        found = _EXCERPT.findall(prompt)
        excerpt_id, excerpt = found[0] if found else ("none#0", "No excerpt was supplied for this item.")
        sentences = [s.strip() for s in _SENT.split(excerpt) if len(s.split()) >= 5] or [excerpt]
        topic = re.search(r"Taxonomy cell: .*> (.+)", prompt)
        label = topic.group(1).strip() if topic else "the topic"
        facts = " ".join(sentences[:2])
        return {
            "cannot_write": None,
            "vignette": (
                f"A {20 + n % 40}-year-old woman comes to the clinic because of {2 + n % 5} days of symptoms that her "
                f"physician relates to {label}. Her temperature is 37.0°C (98.6°F), pulse is 72/min, and blood "
                "pressure is 118/74 mm Hg. Examination shows no acute distress and no focal findings of note. "
                f"The physician reviews this background: {facts}"
            ),
            "lead_in": "Which of the following is the best explanation of the background material?",
            "options": [
                {"label": "A", "html": "The statement given in the background material"},
                {"label": "B", "html": "A statement about an unrelated organ system"},
                {"label": "C", "html": "A statement about a different disease entity"},
                {"label": "D", "html": "A statement about a medication class"},
                {"label": "E", "html": "A statement about a laboratory artefact"},
            ],
            "answer": "A",
            "explanation": facts,
            "why_not": {
                "B": "It concerns another organ system.",
                "C": "It concerns another disease.",
                "D": "It concerns a drug class.",
                "E": "It concerns an artefact.",
            },
            "educational_objective": f"Recall the key concept of {label}.",
            "difficulty": "medium",
            "citations": [{"excerpt_id": excerpt_id, "claim": " ".join(sentences[0].split()[:12])}],
            "nutrition": False,
            "demographic_relevance": None,
        }

    @staticmethod
    def _solve(prompt: str) -> dict[str, Any]:
        if "Does the answer given mean the same" in prompt:
            return {"matches": True}
        if "You are not shown any options" in prompt:
            return {"answer": "The statement given in the background material"}
        return {"choice": "A", "confidence": 0.9, "defensibility": {"A": 0.9, "B": 0.1, "C": 0.1, "D": 0.05, "E": 0.05}}
