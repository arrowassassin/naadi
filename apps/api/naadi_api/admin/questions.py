"""Admin question editor (segment 19): CRUD with the full item form, status workflow, lint, revisions.

These routes are the ONLY place outside grading where a question's key and explanations leave the database, and
they are admin-only (`ConsoleAdmin`). No student route reads from here. Every edit snapshots the whole row into
`question_revisions` (backend-only) and writes an `admin_audit` row; `snapshot_question` is public for S21's
authoring pipeline. Optimistic concurrency uses `version` (sha256 of the row), since `questions` has no
`updated_at`.

HTML fields are rendered by the web's SafeHtml (DOMPurify) exactly as seed items; the server additionally refuses
active content (script, event handlers, javascript: URLs) and bounds every field.
"""

from __future__ import annotations

import datetime as dt
import importlib
import re
import secrets
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Path, Query, Request
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from ..auth import CurrentUser
from ..embeddings.jobs import schedule_reindex
from .common import (
    ADMIN_ERRORS,
    CONFLICT,
    INVALID,
    NOT_FOUND,
    ConsoleAdmin,
    Row,
    audit,
    conflict,
    not_found,
    one,
    row_hash,
    rows,
    run_tx,
)
from .licence import GateRefused, source_terms

router = APIRouter(prefix="/api/admin/questions", tags=["admin"])

QUESTION_ID = r"^[a-z0-9][a-z0-9_-]{1,63}$"
TAXONOMY = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
LABELS = "ABCDEFGHIJ"
ACTIVE_HTML = re.compile(
    r"<\s*/?\s*(script|iframe|object|embed|style|link|meta|form|base)\b|<[^>]*\son[a-z]+\s*=|<[^>]*javascript\s*:", re.I
)
QuestionStatus = Literal["draft", "auto_checked", "in_review", "published", "retired"]
Difficulty = Literal["easy", "medium", "hard"]
Exam = Literal["step1", "step2ck", "step3"]
TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"auto_checked", "in_review"}),
    "auto_checked": frozenset({"draft", "in_review"}),
    "in_review": frozenset({"draft", "published"}),
    "published": frozenset({"retired", "in_review"}),
    "retired": frozenset({"draft"}),
}
_COLS = (
    "id, chapter_id, exam, block, stem_html, options, answer, answer_html, explanation_html, why_not_html,"
    " educational_objective, difficulty, taxonomy_ids, tags, source_id, status, review, stats, created_at"
)


def _html(v: str | None, *, required: bool = False, max_len: int = 20_000) -> str | None:
    if v is None or not v.strip():
        if required:
            raise ValueError("must not be blank")
        return None
    if len(v) > max_len:
        raise ValueError(f"at most {max_len} characters")
    if ACTIVE_HTML.search(v):
        raise ValueError("active content (scripts, event handlers, embedded frames) is not allowed")
    return v.strip()


class OptionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(pattern=r"^[A-J]$")
    html: str = Field(min_length=1, max_length=2_000)

    @field_validator("html")
    @classmethod
    def _h(cls, v: str) -> str:
        out = _html(v, required=True, max_len=2_000)
        assert out is not None
        return out


class QuestionFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chapter_id: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    exam: Exam
    block: int | None = Field(default=None, ge=1, le=999)
    stem_html: str = Field(min_length=1, max_length=20_000)
    options: list[OptionIn] = Field(min_length=2, max_length=10)
    answer: str = Field(pattern=r"^[A-J]$")
    answer_html: str | None = Field(default=None, max_length=5_000)
    explanation_html: str | None = Field(default=None, max_length=20_000)
    why_not_html: str | None = Field(default=None, max_length=20_000)
    educational_objective: str | None = Field(default=None, max_length=2_000)
    difficulty: Difficulty | None = None
    taxonomy_ids: list[str] = Field(default_factory=list, max_length=50)
    tags: list[Annotated[str, Field(min_length=1, max_length=60)]] = Field(default_factory=list, max_length=30)
    source_id: str = Field(pattern=r"^[a-z0-9][a-z0-9:._-]{0,127}$")

    @field_validator("stem_html")
    @classmethod
    def _stem(cls, v: str) -> str:
        out = _html(v, required=True)
        assert out is not None
        return out

    @field_validator("answer_html", "explanation_html", "why_not_html", "educational_objective")
    @classmethod
    def _optional_html(cls, v: str | None) -> str | None:
        return _html(v)

    @field_validator("taxonomy_ids")
    @classmethod
    def _tax(cls, v: list[str]) -> list[str]:
        if any(not TAXONOMY.match(t) for t in v):
            raise ValueError("taxonomy ids are lowercase dotted ids")
        return list(dict.fromkeys(v))

    @model_validator(mode="after")
    def _options(self) -> QuestionFields:
        labels = [o.label for o in self.options]
        if labels != list(LABELS[: len(labels)]):
            raise ValueError("options are labelled A, B, C … in order with no gaps")
        if self.answer not in labels:
            raise ValueError("the key must be one of the options")
        if len({o.html.strip().lower() for o in self.options}) != len(labels):
            raise ValueError("options must differ")
        return self


class QuestionCreate(QuestionFields):
    id: str | None = Field(default=None, pattern=QUESTION_ID, description="Leave out to generate one")


class QuestionUpdate(QuestionFields):
    expected_version: str = Field(pattern=r"^[0-9a-f]{64}$")


class QuestionStatusChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: QuestionStatus
    expected_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    note: str | None = Field(default=None, max_length=1000)


class OptionOut(BaseModel):
    label: str
    html: str


class AdminQuestion(BaseModel):
    id: str
    chapter_id: str | None
    exam: str
    block: int | None
    stem_html: str
    options: list[OptionOut]
    answer: str
    answer_html: str | None
    explanation_html: str | None
    why_not_html: str | None
    educational_objective: str | None
    difficulty: Difficulty | None
    taxonomy_ids: list[str]
    tags: list[str]
    source_id: str | None
    status: QuestionStatus
    review: dict[str, JsonValue]
    created_at: dt.datetime
    version: str = Field(description="Send back as expected_version when saving")


class AdminQuestionSummary(BaseModel):
    id: str
    exam: str
    chapter_id: str | None
    status: QuestionStatus
    difficulty: Difficulty | None
    stem_text: str = Field(description="Plain-text start of the stem")
    taxonomy_ids: list[str]
    source_id: str | None
    created_at: dt.datetime


class AdminQuestionList(BaseModel):
    items: list[AdminQuestionSummary]
    next_cursor: str | None


class LintFinding(BaseModel):
    rule: str
    severity: Literal["error", "warning", "info"]
    field: str | None = None
    detail: dict[str, JsonValue] = Field(default_factory=dict)


class LintResult(BaseModel):
    available: bool = Field(description="False until the authoring linter (segment 21) is installed")
    findings: list[LintFinding]


class QuestionRevisionItem(BaseModel):
    id: int
    version: int
    origin: Literal["admin", "author", "review", "seed"]
    author_user_id: uuid.UUID | None
    note: str | None
    created_at: dt.datetime
    status: str | None = Field(description="The question's status in that snapshot")


class QuestionRevisionList(BaseModel):
    items: list[QuestionRevisionItem]


def _plain(html: str, n: int = 160) -> str:
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", html)).strip()
    return text[:n]


def version_of(r: Row) -> str:
    snap = {k: r[k] for k in r if k != "version"}
    h = row_hash(snap)
    assert h is not None
    return h


def _question(r: Row) -> AdminQuestion:
    return AdminQuestion(
        **{
            k: r[k]
            for k in (
                "id",
                "chapter_id",
                "exam",
                "block",
                "stem_html",
                "answer",
                "answer_html",
                "explanation_html",
                "why_not_html",
                "educational_objective",
                "difficulty",
                "source_id",
                "status",
                "created_at",
            )
        },
        options=[
            OptionOut(label=str(o.get("label")), html=str(o.get("html")))
            for o in (r["options"] or [])
            if isinstance(o, dict)
        ],
        taxonomy_ids=list(r["taxonomy_ids"] or []),
        tags=list(r["tags"] or []),
        review=r["review"] if isinstance(r["review"], dict) else {},
        version=version_of(r),
    )


async def load_question(conn: AsyncConnection[Any], question_id: str, *, lock: bool = False) -> Row:
    r = await one(
        conn,
        f"select {_COLS} from public.questions where id = %s" + (" for update" if lock else ""),  # noqa: S608
        (question_id,),
    )
    if r is None:
        raise not_found("question")
    return r


async def snapshot_question(
    conn: AsyncConnection[Any],
    question_id: str,
    *,
    author: uuid.UUID | None,
    origin: Literal["admin", "author", "review", "seed"],
    note: str | None = None,
) -> int:
    """Store the question's current row in `question_revisions` (call AFTER the edit, on the same transaction).
    Returns the new version (1, 2, … per question). The question row is locked, so concurrent snapshots of one
    question are numbered in order."""
    r = await load_question(conn, question_id, lock=True)
    snap = {k: (v.isoformat() if isinstance(v, dt.datetime) else v) for k, v in r.items()}
    out = await one(
        conn,
        "insert into public.question_revisions (question_id, snapshot, author_user_id, origin, note)"
        " values (%s, %s, %s, %s, %s) returning version",  # version: the 0025 insert trigger
        (question_id, Jsonb(snap), author, origin, note),
    )
    assert out is not None
    return int(out["version"])


async def _check_refs(conn: AsyncConnection[Any], f: QuestionFields) -> None:
    if await source_terms(conn, f.source_id) is None:
        raise GateRefused("no_source", f.source_id)
    if (
        f.chapter_id
        and await one(conn, "select 1 from public.chapters where id = %s and owner_user_id is null", (f.chapter_id,))
        is None
    ):
        raise GateRefused("chapter_missing", f.chapter_id)
    if f.taxonomy_ids:
        known = {
            r["id"]
            for r in await rows(conn, "select id from public.taxonomy_nodes where id = any(%s)", (f.taxonomy_ids,))
        }
        missing = [t for t in f.taxonomy_ids if t not in known]
        if missing:
            raise GateRefused("taxonomy_missing", missing[0])


def _values(f: QuestionFields) -> dict[str, Any]:
    return {
        "chapter_id": f.chapter_id,
        "exam": f.exam,
        "block": f.block,
        "stem_html": f.stem_html,
        "options": Jsonb([{"label": o.label, "html": o.html} for o in f.options]),
        "answer": f.answer,
        "answer_html": f.answer_html,
        "explanation_html": f.explanation_html,
        "why_not_html": f.why_not_html,
        "educational_objective": f.educational_objective,
        "difficulty": f.difficulty,
        "taxonomy_ids": f.taxonomy_ids,
        "tags": f.tags,
        "source_id": f.source_id,
    }


def item_changed(before: Row, f: QuestionFields) -> bool:
    """Did the stem, the options or the key change (whitespace-insensitive)?"""

    def norm(x: str | None) -> str:
        return re.sub(r"\s+", " ", x or "").strip()

    old_opts = [
        (str(o.get("label")), norm(str(o.get("html")))) for o in (before["options"] or []) if isinstance(o, dict)
    ]
    new_opts = [(o.label, norm(o.html)) for o in f.options]
    return norm(before["stem_html"]) != norm(f.stem_html) or old_opts != new_opts or before["answer"] != f.answer


def _reindex(request: Request, ids: list[str]) -> None:
    schedule_reindex(request.app, ("questions",), ref_ids=ids)


@router.get(
    "",
    operation_id="listAdminQuestions",
    summary="Admin only: questions of every status (no keys or explanations in the list)",
    response_model=AdminQuestionList,
    responses=ADMIN_ERRORS,
)
async def list_questions(
    request: Request,
    admin: ConsoleAdmin,
    exam: Annotated[Exam | None, Query()] = None,
    status: Annotated[QuestionStatus | None, Query()] = None,
    chapter_id: Annotated[str | None, Query(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")] = None,
    taxonomy_id: Annotated[str | None, Query(max_length=128)] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(pattern=QUESTION_ID)] = None,
) -> AdminQuestionList:
    async def run(conn: AsyncConnection[Any]) -> AdminQuestionList:
        sql = (
            "select id, exam, chapter_id, status, difficulty, stem_html, taxonomy_ids, source_id, created_at"
            " from public.questions where true"
        )
        params: dict[str, Any] = {"limit": limit + 1}
        for name, value in (("exam", exam), ("status", status), ("chapter_id", chapter_id)):
            if value:
                sql += f" and {name} = %({name})s"
                params[name] = value
        if taxonomy_id:
            sql += " and %(tax)s = any(taxonomy_ids)"
            params["tax"] = taxonomy_id
        if q and q.strip():
            sql += " and (id ilike %(q)s or stem_html ilike %(q)s)"
            params["q"] = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        if cursor:
            sql += " and id > %(cursor)s"
            params["cursor"] = cursor
        sql += " order by id limit %(limit)s"
        found = await rows(conn, sql, params)
        more = len(found) > limit
        found = found[:limit]
        return AdminQuestionList(
            items=[
                AdminQuestionSummary(
                    id=r["id"],
                    exam=r["exam"],
                    chapter_id=r["chapter_id"],
                    status=r["status"],
                    difficulty=r["difficulty"],
                    stem_text=_plain(r["stem_html"]),
                    taxonomy_ids=list(r["taxonomy_ids"] or []),
                    source_id=r["source_id"],
                    created_at=r["created_at"],
                )
                for r in found
            ],
            next_cursor=found[-1]["id"] if more and found else None,
        )

    return await run_tx(request, run)


@router.get(
    "/{question_id}",
    operation_id="getAdminQuestion",
    summary="Admin only: one question with its key and explanations",
    response_model=AdminQuestion,
    responses={**ADMIN_ERRORS, **NOT_FOUND},
)
async def get_question(
    request: Request, admin: ConsoleAdmin, question_id: Annotated[str, Path(pattern=QUESTION_ID)]
) -> AdminQuestion:
    async def run(conn: AsyncConnection[Any]) -> AdminQuestion:
        return _question(await load_question(conn, question_id))

    return await run_tx(request, run)


@router.post(
    "",
    operation_id="createAdminQuestion",
    summary="Admin only: create a question (status draft, invisible to students)",
    response_model=AdminQuestion,
    status_code=201,
    responses={**ADMIN_ERRORS, **CONFLICT, **INVALID},
)
async def create_question(request: Request, admin: ConsoleAdmin, body: QuestionCreate) -> AdminQuestion:
    async def run(conn: AsyncConnection[Any]) -> AdminQuestion:
        await _check_refs(conn, body)
        qid = body.id or f"q-{body.exam}-{secrets.token_hex(4)}"
        if await one(conn, "select 1 from public.questions where id = %s", (qid,)):
            raise conflict("id_taken", "A question with this id exists.")
        v = _values(body)
        cols = ", ".join(v)
        await conn.execute(
            f"insert into public.questions (id, {cols}, status, review) values (%(id)s, "  # noqa: S608 - fixed column names
            + ", ".join(f"%({k})s" for k in v)
            + ", 'draft', %(review)s)",
            {"id": qid, **v, "review": Jsonb({"origin": "admin", "humanExpertReviewed": False})},
        )
        await snapshot_question(conn, qid, author=admin.user_id, origin="admin", note=None)
        row = await load_question(conn, qid)
        await audit(conn, admin, "question.create", "question", qid, after=row)
        return _question(row)

    return await run_tx(request, run)


@router.put(
    "/{question_id}",
    operation_id="updateAdminQuestion",
    summary="Admin only: replace a question's fields (a published item is re-embedded)",
    response_model=AdminQuestion,
    responses={**ADMIN_ERRORS, **NOT_FOUND, **CONFLICT, **INVALID},
)
async def update_question(
    request: Request, admin: ConsoleAdmin, question_id: Annotated[str, Path(pattern=QUESTION_ID)], body: QuestionUpdate
) -> AdminQuestion:
    async def run(conn: AsyncConnection[Any]) -> AdminQuestion:
        before = await load_question(conn, question_id, lock=True)
        if version_of(before) != body.expected_version:
            raise conflict("stale_write", "This question was changed since you opened it.")
        await _check_refs(conn, body)
        v = _values(body)
        sets = [f"{k} = %({k})s" for k in v]
        if before["status"] == "published" and item_changed(before, body):
            # authoring_rules.md Part C rule 7: a published item whose stem, options or key changed goes back to
            # review, and its psychometrics start again (they describe the old item).
            sets += ["status = 'in_review'", "stats = '{}'::jsonb"]
        await conn.execute(
            "update public.questions set " + ", ".join(sets) + " where id = %(id)s",  # noqa: S608
            {**v, "id": question_id},
        )
        await snapshot_question(conn, question_id, author=admin.user_id, origin="admin")
        after = await load_question(conn, question_id)
        await audit(conn, admin, "question.edit", "question", question_id, before=before, after=after)
        return _question(after)

    out = await run_tx(request, run)
    _reindex(request, [question_id])
    return out


@router.post(
    "/{question_id}/status",
    operation_id="setAdminQuestionStatus",
    summary="Admin only: move a question through its status workflow (publishing runs the gates)",
    response_model=AdminQuestion,
    responses={**ADMIN_ERRORS, **NOT_FOUND, **CONFLICT},
)
async def set_question_status(
    request: Request,
    admin: ConsoleAdmin,
    question_id: Annotated[str, Path(pattern=QUESTION_ID)],
    body: QuestionStatusChange,
) -> AdminQuestion:
    async def run(conn: AsyncConnection[Any]) -> AdminQuestion:
        before = await load_question(conn, question_id, lock=True)
        if version_of(before) != body.expected_version:
            raise conflict("stale_write", "This question was changed since you opened it.")
        if body.status == before["status"]:
            return _question(before)
        if body.status not in TRANSITIONS[before["status"]]:
            raise conflict("bad_transition", "This status change is not allowed.")
        if body.status == "published":
            terms = await source_terms(conn, before["source_id"])
            if terms is None:
                raise GateRefused("no_source", question_id)
            if not terms.licensed:
                raise GateRefused("no_license", question_id)
            findings = run_lint(before)
            if findings.available and any(f.severity == "error" for f in findings.findings):
                raise GateRefused("lint_errors", question_id)
        await conn.execute("update public.questions set status = %s where id = %s", (body.status, question_id))
        await snapshot_question(conn, question_id, author=admin.user_id, origin="admin", note=body.note)
        after = await load_question(conn, question_id)
        await audit(
            conn,
            admin,
            f"question.status_{body.status}",
            "question",
            question_id,
            before=before,
            after=after,
            note=body.note,
        )
        return _question(after)

    out = await run_tx(request, run)
    _reindex(request, [question_id])
    return out


def run_lint(r: Row) -> LintResult:
    """S21's `naadi_api.authoring.lint.lint_question(item)` when it is installed, else `available: false`."""
    try:
        mod = importlib.import_module("naadi_api.authoring.lint")
    except ImportError:
        return LintResult(available=False, findings=[])
    fn = getattr(mod, "lint_question", None)
    if fn is None:
        return LintResult(available=False, findings=[])
    item = {
        k: r[k]
        for k in (
            "id",
            "stem_html",
            "options",
            "answer",
            "explanation_html",
            "why_not_html",
            "educational_objective",
            "difficulty",
            "taxonomy_ids",
        )
    }
    report = fn(item)
    raw = getattr(report, "findings", None)
    if raw is None and isinstance(report, dict):
        raw = report.get("findings")
    if raw is None and isinstance(report, list):
        raw = report
    out: list[LintFinding] = []
    for f in raw or []:
        if hasattr(f, "as_dict"):
            f = f.as_dict()
        data: dict[str, Any] = (
            f.model_dump(mode="json")
            if hasattr(f, "model_dump")
            else (f if isinstance(f, dict) else dict(getattr(f, "__dict__", {})))
        )
        sev = {"warn": "warning"}.get(str(data.get("severity", "warning")), str(data.get("severity", "warning")))
        raw_params = data.get("params")
        params: dict[str, Any] = raw_params if isinstance(raw_params, dict) else {}
        out.append(
            LintFinding(
                rule=str(data.get("rule", "unknown")),
                severity=sev if sev in ("error", "warning", "info") else "warning",
                field=next((v for v in (data.get("field"), data.get("target")) if isinstance(v, str) and v), None),
                detail={k: v for k, v in params.items() if isinstance(v, str | int | float | bool) or v is None},
            )
        )
    return LintResult(available=True, findings=out)


@router.get(
    "/{question_id}/lint",
    operation_id="lintAdminQuestion",
    summary="Admin only: NBME item-writing lint findings for a question",
    response_model=LintResult,
    responses={**ADMIN_ERRORS, **NOT_FOUND},
)
async def lint_question(
    request: Request, admin: ConsoleAdmin, question_id: Annotated[str, Path(pattern=QUESTION_ID)]
) -> LintResult:
    async def run(conn: AsyncConnection[Any]) -> LintResult:
        return run_lint(await load_question(conn, question_id))

    return await run_tx(request, run)


@router.get(
    "/{question_id}/revisions",
    operation_id="listAdminQuestionRevisions",
    summary="Admin only: a question's edit snapshots, newest first",
    response_model=QuestionRevisionList,
    responses={**ADMIN_ERRORS, **NOT_FOUND},
)
async def question_revisions(
    request: Request, admin: ConsoleAdmin, question_id: Annotated[str, Path(pattern=QUESTION_ID)]
) -> QuestionRevisionList:
    async def run(conn: AsyncConnection[Any]) -> QuestionRevisionList:
        await load_question(conn, question_id)
        found = await rows(
            conn,
            "select id, version, origin, author_user_id, note, created_at, snapshot ->> 'status' as status"
            " from public.question_revisions where question_id = %s order by version desc limit 200",
            (question_id,),
        )
        return QuestionRevisionList(items=[QuestionRevisionItem(**r) for r in found])

    return await run_tx(request, run)


__all__ = ["CurrentUser", "router", "snapshot_question"]
