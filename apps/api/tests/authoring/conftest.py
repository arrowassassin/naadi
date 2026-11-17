"""Fixtures for the authoring db tests: a real Postgres, the real HTTP path (token -> JWKS -> admin check), a
scripted model gateway, and a small taxonomy of the test's own."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, BaseMessage
from psycopg.types.json import Jsonb
from pydantic import SecretStr

from naadi_api.auth import TokenVerifier
from naadi_api.db import Database
from naadi_api.llm import CallContext
from naadi_api.main import create_app
from tests.auth.accounts import signed_in
from tests.auth.jwt_helpers import SUPABASE_URL, JwksServer, SigningKey
from tests.conftest import fake_service, make_settings
from tests.db.fixtures import insert_row
from tests.db.rls import create_auth_user, require_local

ADMINS = {"a": "admin-a@example.com", "b": "admin-b@example.com", "c": "admin-c@example.com"}
SOLVER_MODEL = "particle-ai/particle-test"


class FakeGateway:
    """Stands in for the LLM gateway on `app.state.authoring_gateway`: scripted blind-solver replies."""

    def __init__(self) -> None:
        self.replies: list[str] = []
        self.model = SOLVER_MODEL
        self.flagged = False
        self.calls = 0
        self.delay = 0.0  # seconds each call takes (to hold a run open)

    def queue_draft(self, text: str) -> None:
        self.replies.append(text)

    def queue_agreeing(self, key: str = "A") -> None:
        self.replies += [
            json.dumps({"choice": key, "confidence": 0.9, "defensibility": {key: 0.9}}),
            json.dumps({"answer": "x"}),
            json.dumps({"matches": True}),
        ]

    async def ainvoke(self, role: str, messages: list[BaseMessage], ctx: CallContext, **kw: Any) -> AIMessage:
        import asyncio

        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        text = self.replies.pop(0)
        model = "nvidia/nemotron-test" if role == "author" else self.model
        return AIMessage(content=text, response_metadata={"naadi_model": model, "review_not_independent": self.flagged})

    async def config(self) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(roles={"author": SimpleNamespace(chain=[SimpleNamespace(model="nvidia/nemotron-test")])})


@pytest.fixture(scope="module")
def url() -> str:
    from tests.db.rls import database_url, ensure_migrated

    u = database_url()
    require_local(u)
    ensure_migrated(u)
    return u


@pytest.fixture
def pg(url: str) -> Iterator[psycopg.Connection[Any]]:
    with psycopg.connect(url, autocommit=True) as conn:
        yield conn


@pytest.fixture
def key() -> SigningKey:
    return SigningKey()


@dataclass
class World:
    tag: str
    users: dict[str, uuid.UUID]
    student: uuid.UUID
    source: str
    system: str
    discipline: str
    competency: str
    topic: str
    gateway: FakeGateway

    @property
    def taxonomy_ids(self) -> list[str]:
        return [self.system, self.discipline, self.competency, self.topic]


@pytest.fixture
def world(pg: psycopg.Connection[Any]) -> Iterator[World]:
    tag = uuid.uuid4().hex[:8]
    w = World(
        tag=tag,
        users={k: create_auth_user(pg) for k in ADMINS},
        student=create_auth_user(pg),
        source=f"t{tag}-src",
        system=f"t{tag}.sys.x",
        discipline=f"t{tag}.disc.x",
        competency=f"t{tag}.comp.x",
        topic=f"topic.t{tag}.x",
        gateway=FakeGateway(),
    )
    insert_row(
        pg,
        "sources",
        {"id": w.source, "name": "t", "license": "proprietary-naadi", "allow_ai_ingest": True},
    )
    # Authored items are Naadi originals: the shared source row every run points at (the seed creates it too).
    # Other suites insert a bare row with that id (default flags: not AI-ingestible) and leave it, so the row is
    # set to the seed's flags here, and whatever was there before is put back afterwards.
    before = pg.execute(
        "select allow_copy, allow_modify, allow_commercial, allow_ai_ingest from public.sources"
        " where id = 'naadi-original-v1'"
    ).fetchone()
    pg.execute(
        "insert into public.sources (id, name, license, allow_copy, allow_modify, allow_commercial, allow_ai_ingest)"
        " values ('naadi-original-v1', 'Naadi original content (v1)', 'proprietary-naadi', true, true, true, true)"
        " on conflict (id) do update set allow_copy = true, allow_modify = true, allow_commercial = true,"
        " allow_ai_ingest = true"
    )
    for nid, kind, parent in (
        (w.system, "system", None),
        (w.discipline, "discipline", None),
        (w.competency, "competency", None),
        (w.topic, "topic", w.system),
    ):
        insert_row(
            pg,
            "taxonomy_nodes",
            {"id": nid, "kind": kind, "parent_id": parent, "label": f"Zqxv {tag}", "exam": ["step1"]},
        )
    yield w
    pg.execute("delete from public.questions where id like %s", (f"t{tag}-%",))
    pg.execute("delete from public.authoring_runs where cell->>'topic' = %s", (w.topic,))
    pg.execute("delete from public.taxonomy_nodes where id like %s or id like %s", (f"t{tag}.%", f"topic.t{tag}.%"))
    pg.execute("delete from public.sources where id = %s", (w.source,))
    if before is not None:
        pg.execute(
            "update public.sources set allow_copy = %s, allow_modify = %s, allow_commercial = %s,"
            " allow_ai_ingest = %s where id = 'naadi-original-v1'",
            before,
        )
    pg.execute("delete from auth.users where id = any(%s)", ([*w.users.values(), w.student],))


@pytest.fixture
def client(url: str, key: SigningKey, static_dir: Path, world: World) -> Iterator[TestClient]:
    s = make_settings(
        static_dir=static_dir,
        supabase_url=SUPABASE_URL,
        database_url=SecretStr(url),
        admin_emails=",".join(ADMINS.values()),
        app_env="prod",
        supabase_service_role_key=None,
        supabase_jwt_secret=None,
        openrouter_api_key=None,
        nvidia_api_key=None,
        tavily_api_key=None,
    )
    app = create_app(
        s,
        health=fake_service(),
        db=Database(s.database_url),
        verifier=TokenVerifier(SUPABASE_URL, transport=JwksServer(key).transport()),
    )
    app.state.authoring_gateway = world.gateway
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def head(key: SigningKey, w: World, who: str) -> dict[str, str]:
    """`who`: 'a', 'b', 'c' (admins) or 'student'."""
    if who == "student":
        return signed_in(key, w.student, email="student@example.com", header="Authorization")
    return signed_in(key, w.users[who], email=ADMINS[who], header="Authorization")


GOOD_STEM = (
    "<p>A 34-year-old woman comes to the clinic because of burning with urination for 2 days. She has no fever or "
    "flank pain. Her temperature is 37.0°C (98.6°F), pulse is 72/min, and blood pressure is 118/74 mm Hg. "
    "Examination of the abdomen shows mild suprapubic tenderness. Urinalysis shows positive leukocyte esterase and "
    "nitrite. Urine culture grows a gram-negative rod that ferments lactose and produces indole.</p>"
    "<p>Which of the following is the most likely causal organism?</p>"
)
OPTIONS = [
    {"label": "A", "html": "Escherichia coli"},
    {"label": "B", "html": "Klebsiella pneumoniae"},
    {"label": "C", "html": "Proteus mirabilis"},
    {"label": "D", "html": "Pseudomonas aeruginosa"},
    {"label": "E", "html": "Staphylococcus saprophyticus"},
]


def solver_json(agrees: bool = True, independent: bool = True) -> dict[str, Any]:
    return {
        "agrees": agrees,
        "choice": "A" if agrees else "B",
        "confidence": 0.9,
        "defensibility": {"A": 0.9},
        "coverMatches": True,
        "model": SOLVER_MODEL,
        "independent": independent,
        "findings": [],
        "calls": 3,
        "error": None,
    }


Make = Callable[..., str]


@pytest.fixture
def make_item(pg: psycopg.Connection[Any], world: World) -> Make:
    """Insert a question of this world (privileged); returns its id."""

    def make(
        suffix: str = "1",
        *,
        status: str = "in_review",
        ai: bool = True,
        solver: dict[str, Any] | None = None,
        review: dict[str, Any] | None = None,
        stem: str = GOOD_STEM,
        objective: str | None = "Identify the usual cause of uncomplicated cystitis.",
        stats: dict[str, Any] | None = None,
    ) -> str:
        qid = f"t{world.tag}-{suffix}"
        rev: dict[str, Any] = {"status": "x", "humanExpertReviewed": False, "round": 1, "humanReviewers": []}
        if ai:
            rev["authorModel"] = {
                "model": "nvidia/nemotron-test",
                "promptHash": "h",
                "excerptIds": ["s#0"],
                "runId": "r",
            }
            rev["solver"] = solver if solver is not None else solver_json()
        rev.update(review or {})
        insert_row(
            pg,
            "questions",
            {
                "id": qid,
                "exam": "step1",
                "stem_html": stem,
                "options": Jsonb(OPTIONS),
                "answer": "A",
                "answer_html": "Answer: A. Escherichia coli",
                "explanation_html": "<p>Escherichia coli is the commonest cause of uncomplicated cystitis.</p>",
                "why_not_html": (
                    "Why the others are wrong: B — indole negative. C — no lactose. D — no lactose. E — coccus."
                ),
                "educational_objective": objective,
                "difficulty": "medium",
                "taxonomy_ids": world.taxonomy_ids,
                "source_id": world.source,
                "status": status,
                "review": Jsonb(rev),
                "stats": Jsonb(stats or {}),
            },
        )
        return qid

    return make


RATINGS: dict[str, Any] = {
    "accuracy": "correct",
    "single_best_answer": True,
    "cognitive_level": "application",
    "flaws": [],
    "difficulty": "medium",
    "taxonomy_ok": True,
}


def review_body(decision: str = "approve", **kw: Any) -> dict[str, Any]:
    ratings = {**RATINGS, **kw.pop("ratings", {})}
    return {"decision": decision, "ratings": ratings, **kw}


def run_as(
    pg: psycopg.Connection[Any], role: str, statement: str, params: Any = None, sub: uuid.UUID | None = None
) -> Any:
    """Run one statement as a database role (with the JWT subject when given), inside a transaction of its own."""
    with pg.transaction():
        pg.execute(f"set local role {role}")
        if sub is not None:
            pg.execute(
                "select set_config('request.jwt.claims', %s, true)", (json.dumps({"sub": str(sub), "role": role}),)
            )
        cur = pg.execute(statement, params)
        return cur.fetchall() if cur.description else None
