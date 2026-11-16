"""Load and validate the seed bundle (`data/`, `config/`). Pure: no database, no network.

Every input file is parsed into a typed model first, so a malformed file fails before anything is written.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..settings import APP_DIR, REPO_ROOT

ORIGINAL_SOURCE = "naadi-original-v1"
SECTION_BODIES = Path("generated") / "section_bodies.json"
CONVERT_CMD = "pnpm --filter @naadi/shared seed:convert"


# The content schema this importer understands (packages/shared/src/content: CONTENT_SCHEMA_VERSION and the
# node / mark lists). A schema change bumps the version there; the importer then refuses the file until it is
# updated here too, so a body with a node the reader cannot render never reaches the database.
CONTENT_SCHEMA_VERSION = 1
CONTENT_NODE_TYPES = frozenset(
    {
        "doc",
        "paragraph",
        "heading",
        "bulletList",
        "orderedList",
        "listItem",
        "table",
        "tableRow",
        "tableHeader",
        "tableCell",
        "callout",
        "figure",
        "video",
        "pdf",
        "questionEmbed",
        "mermaid",
        "math",
        "hardBreak",
        "text",
    }
)
CONTENT_MARK_TYPES = frozenset({"bold", "italic", "subscript", "superscript", "highlight"})


def body_problems(section_id: str, body: Mapping[str, Any], figure_ids: list[str]) -> list[str]:
    """Node/mark types inside the schema, figure nodes matching figureIds, no placeholder text."""
    problems: list[str] = []
    figures: list[str] = []

    def walk(node: Any, path: str) -> None:
        if not isinstance(node, Mapping):
            problems.append(f"section {section_id}: {path} is not a node")
            return
        t = node.get("type")
        if t not in CONTENT_NODE_TYPES:
            problems.append(f"section {section_id}: unknown node type {t!r} at {path}")
        for m in node.get("marks") or []:
            mt = m.get("type") if isinstance(m, Mapping) else None
            if mt not in CONTENT_MARK_TYPES:
                problems.append(f"section {section_id}: unknown mark type {mt!r} at {path}")
        if t == "text" and "[[figure:" in str(node.get("text", "")):
            problems.append(f"section {section_id}: [[figure: placeholder left at {path}")
        if t == "figure":
            attrs = node.get("attrs")
            figures.append(str(attrs.get("mediaId")) if isinstance(attrs, Mapping) else "")
        children = node.get("content") or []
        if not isinstance(children, list):
            problems.append(f"section {section_id}: content of {path} is not a list")
            return
        for i, child in enumerate(children):
            walk(child, f"{path}.{i}")

    if body.get("type") != "doc":
        problems.append(f"section {section_id}: body is not a doc")
    walk(body, "doc")
    if figures != figure_ids:
        problems.append(f"section {section_id}: figure nodes {figures} differ from figureIds {figure_ids}")
    return problems


def default_data_dir() -> Path:
    """`./data` next to the app (container), else `<repo>/data` (checkout)."""
    for c in (APP_DIR / "data", REPO_ROOT / "data"):
        if c.is_dir():
            return c
    return REPO_ROOT / "data"


class SeedDataError(ValueError):
    """The seed bundle is invalid; `problems` lists every issue found."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems[:10]) + (f" (+{len(problems) - 10} more)" if len(problems) > 10 else ""))
        self.problems = problems


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


Exam = Literal["step1", "step2ck", "step3"]
ID = r"^[a-z0-9][a-z0-9_.-]{0,127}$"


class SectionIn(_In):
    id: str = Field(pattern=ID)
    heading: str = Field(min_length=1)
    html: str = Field(min_length=1)
    figure_ids: list[str] = Field(alias="figureIds")


class ChapterIn(_In):
    id: str = Field(pattern=ID)
    exam: Exam
    order: int = Field(ge=0, le=999)
    kind: Literal["chapter", "mock_exam"]
    title: str = Field(min_length=1)
    meta: str | None = None
    sections: list[SectionIn] = Field(min_length=1)
    figure_ids: list[str] = Field(alias="figureIds")
    question_ids: list[str] = Field(alias="questionIds")


class FigureIn(_In):
    id: str = Field(pattern=ID)
    chapter_id: str = Field(alias="chapterId")
    caption: str = Field(min_length=1)
    file: str = Field(pattern=r"^figures/[a-z0-9_.-]+\.svg$")


class OptionIn(_In):
    label: str = Field(pattern=r"^[A-J]$")
    html: str = Field(min_length=1)


class ReviewIn(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)
    status: str
    human_expert_reviewed: bool = Field(alias="humanExpertReviewed")


class QuestionIn(_In):
    id: str = Field(pattern=ID)
    chapter_id: str = Field(alias="chapterId")
    exam: Exam
    number: int = Field(ge=1)
    block: int | None = Field(ge=1, le=99)
    stem_html: str = Field(alias="stemHtml", min_length=1)
    options: list[OptionIn] = Field(min_length=2, max_length=10)
    answer: str = Field(pattern=r"^[A-J]$")
    answer_html: str | None = Field(alias="answerHtml")
    explanation_html: str | None = Field(alias="explanationHtml")
    why_not_html: str | None = Field(alias="whyNotHtml")
    tags: list[str]
    difficulty: Literal["easy", "medium", "hard"]
    source: str
    review: ReviewIn


class TaxonomyIn(_In):
    id: str = Field(pattern=ID)
    kind: Literal["exam", "system", "discipline", "competency", "clinical_science", "topic"]
    parent_id: str | None
    label: str = Field(min_length=1)
    exam: list[Exam] = Field(min_length=1)
    weight_min: float | None
    weight_max: float | None
    sort: int


class LinkIn(_In):
    label: str = Field(min_length=1)
    url: str = Field(pattern=r"^https://")


class StageIn(_In):
    key: str = Field(pattern=r"^[a-z0-9_]{1,64}$")
    sort: int
    title: str = Field(min_length=1)
    summary_md: str = Field(min_length=1)
    official_links: list[LinkIn]
    last_verified: dt.date


class TaskIn(_In):
    id: str = Field(pattern=ID)
    stage_key: str
    sort: int
    title: str = Field(min_length=1)
    detail_md: str = Field(min_length=1)
    due_rule: str | None
    cost_usd: float | None = Field(ge=0)
    official_url: str | None = Field(pattern=r"^https://")
    last_verified: dt.date


class SourceIn(BaseModel):
    """A sources.yaml row. Extra research fields (quota_reference, also_see, ...) are not stored."""

    model_config = ConfigDict(extra="ignore")
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9:_.-]{0,127}$")
    name: str = Field(min_length=1)
    url: str | None = None
    license: str = Field(min_length=1)
    allow_copy: bool
    allow_modify: bool
    allow_commercial: bool
    allow_ai_ingest: bool
    attribution_template: str | None = None
    notes: str | None = None
    verified: bool = False
    verified_url: str | None = Field(default=None, pattern=r"^https://")


class SectionBody(_In):
    source_sha256: str = Field(alias="sourceSha256", pattern=r"^[0-9a-f]{64}$")
    figure_ids: list[str] = Field(alias="figureIds")
    reading_minutes: int = Field(alias="readingMinutes", ge=1)
    body_html: str = Field(alias="bodyHtml")
    body: dict[str, Any]


class SectionBodies(_In):
    schema_version: int = Field(alias="schemaVersion")
    generator: str
    node_types: list[str] = Field(alias="nodeTypes")
    mark_types: list[str] = Field(alias="markTypes")
    sections: dict[str, SectionBody]


@dataclass(frozen=True)
class GuideIn:
    key: str
    stage_key: str | None
    sort: int
    title: str
    body_md: str


@dataclass(frozen=True)
class PlanDayIn:
    day: int
    example_date: dt.date | None
    weekday: str | None
    week: int | None
    phase: str | None
    chapter_ref: str | None
    chapter_ids: list[str]
    topic: str | None
    chapter_questions: str | None
    qbank: str | None
    self_assessment: str | None
    admin_task: str | None
    extra_resource: str | None
    target_hours: float | None


@dataclass(frozen=True)
class PlanTemplateIn:
    id: str
    exam: Exam
    title: str
    days: list[PlanDayIn]


@dataclass(frozen=True)
class LicenseGate:
    accepted: list[SourceIn]
    rejected: list[tuple[str, str]]  # (id or index, reason)


@dataclass
class SeedBundle:
    data_dir: Path
    config_dir: Path
    chapters: list[ChapterIn]
    figures: list[FigureIn]
    questions: list[QuestionIn]
    taxonomy: list[TaxonomyIn]
    question_map: dict[str, list[str]]
    stages: list[StageIn]
    tasks: list[TaskIn]
    guides: list[GuideIn]
    plan: PlanTemplateIn
    sources: LicenseGate
    bodies: SectionBodies
    problems: list[str] = field(default_factory=list)

    @property
    def sections(self) -> list[tuple[ChapterIn, int, SectionIn]]:
        return [(c, i, s) for c in self.chapters for i, s in enumerate(c.sections)]


def _json(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _models[T: BaseModel](model: type[T], items: Any, what: str, problems: list[str]) -> list[T]:
    if not isinstance(items, list):
        problems.append(f"{what}: expected a list")
        return []
    out: list[T] = []
    for i, raw in enumerate(items):
        try:
            out.append(model.model_validate(raw))
        except ValidationError as exc:
            ident = raw.get("id", raw.get("key", i)) if isinstance(raw, Mapping) else i
            first = exc.errors()[0]
            problems.append(f"{what}[{ident}]: {'.'.join(map(str, first['loc']))}: {first['msg']}")
    return out


def license_gate(raw: Any) -> LicenseGate:
    """Seed only sources whose license fields are all present (HANDOFF §7.1: no asset without a license)."""
    accepted: list[SourceIn] = []
    rejected: list[tuple[str, str]] = []
    rows = raw.get("sources") if isinstance(raw, Mapping) else None
    if not isinstance(rows, list):
        return LicenseGate([], [("sources.yaml", "no `sources:` list")])
    required = ("id", "name", "license", "allow_copy", "allow_modify", "allow_commercial", "allow_ai_ingest")
    seen: set[str] = set()
    for i, row in enumerate(rows):
        ident = str(row.get("id", f"#{i}")) if isinstance(row, Mapping) else f"#{i}"
        if not isinstance(row, Mapping):
            rejected.append((ident, "not a mapping"))
            continue
        missing = [k for k in required if row.get(k) is None or (isinstance(row.get(k), str) and not row[k].strip())]
        if missing:
            rejected.append((ident, f"missing license fields: {', '.join(missing)}"))
            continue
        flags = [k for k in required[3:] if not isinstance(row[k], bool)]
        if flags:
            rejected.append((ident, f"license flags must be true/false: {', '.join(flags)}"))
            continue
        try:
            src = SourceIn.model_validate(row)
        except ValidationError as exc:
            first = exc.errors()[0]
            rejected.append((ident, f"{'.'.join(map(str, first['loc']))}: {first['msg']}"))
            continue
        if src.id in seen:
            rejected.append((ident, "duplicate id"))
            continue
        seen.add(src.id)
        accepted.append(src)
    return LicenseGate(accepted, rejected)


_CHAPTER_REF = re.compile(r"\bCh\s*(\d{1,2})\b")


def _blank(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip()
    return None if not v or v in {"—", "-"} else v


def parse_plan_csv(path: Path, *, template_id: str, exam: Exam, title: str, chapter_ids: set[str]) -> PlanTemplateIn:
    """The day-by-day study plan CSV. Status / score columns are per-student state and are not stored."""
    problems: list[str] = []
    days: list[PlanDayIn] = []
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        need = {"Day", "Date", "Weekday", "Week", "Phase", "Book chapter", "Topic / focus", "Target hours"}
        if reader.fieldnames is None or not need <= set(reader.fieldnames):
            raise SeedDataError([f"{path.name}: missing columns {sorted(need - set(reader.fieldnames or []))}"])
        for n, row in enumerate(reader, start=2):
            try:
                day = int(row["Day"])
                date = dt.date.fromisoformat(row["Date"]) if _blank(row["Date"]) else None
                week = int(row["Week"]) if _blank(row["Week"]) else None
                hours = float(row["Target hours"]) if _blank(row["Target hours"]) else None
            except ValueError as exc:
                problems.append(f"{path.name}:{n}: {exc}")
                continue
            ref = _blank(row.get("Book chapter"))
            refs = [f"ch{int(m):02d}" for m in _CHAPTER_REF.findall(ref or "")]
            unknown = [c for c in refs if c not in chapter_ids]
            if unknown:
                problems.append(f"{path.name}:{n}: chapter reference {unknown} not in chapters.json")
            if hours is not None and not 0 <= hours <= 24:
                problems.append(f"{path.name}:{n}: target hours {hours} out of range")
            weekday = _blank(row["Weekday"])
            if weekday is not None and weekday not in {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}:
                problems.append(f"{path.name}:{n}: weekday {weekday!r}")
            days.append(
                PlanDayIn(
                    day=day,
                    example_date=date,
                    weekday=weekday,
                    week=week,
                    phase=_blank(row["Phase"]),
                    chapter_ref=ref,
                    chapter_ids=refs,
                    topic=_blank(row["Topic / focus"]),
                    chapter_questions=_blank(row.get("Book questions")),
                    qbank=_blank(row.get("UWorld Step 1")),
                    self_assessment=_blank(row.get("Self-assessment")),
                    admin_task=_blank(row.get("Admin / application task")),
                    extra_resource=_blank(row.get("Extra resource")),
                    target_hours=hours,
                )
            )
    numbers = [d.day for d in days]
    if numbers != list(range(1, len(days) + 1)):
        problems.append(f"{path.name}: days must run 1..{len(days)} without gaps")
    if problems:
        raise SeedDataError(problems)
    return PlanTemplateIn(template_id, exam, title, days)


_GUIDE_FILE = re.compile(r"^(\d{2})-([a-z0-9-]+)\.md$")


def load_guides(pathway_dir: Path, stage_keys: set[str]) -> tuple[list[GuideIn], list[str]]:
    guides: list[GuideIn] = []
    problems: list[str] = []
    for path in sorted(pathway_dir.glob("*.md")):
        m = _GUIDE_FILE.match(path.name)
        if not m:
            continue  # README.md documents the bundle; it is not a guide
        text = path.read_text(encoding="utf-8")
        first = text.lstrip().splitlines()[0] if text.strip() else ""
        if not first.startswith("# "):
            problems.append(f"{path.name}: must start with a '# ' title")
            continue
        title = re.sub(r"^\d{2}\.\s*", "", first[2:].strip())
        number, slug = int(m.group(1)), m.group(2).replace("-", "_")
        if number == 0:
            guides.append(GuideIn("overview", None, 0, title, text))
        elif slug in stage_keys:
            guides.append(GuideIn(slug, slug, number * 10, title, text))
        else:
            problems.append(f"{path.name}: no pathway stage {slug!r}")
    return guides, problems


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def load_bundle(data_dir: Path, config_dir: Path) -> SeedBundle:
    """Parse and cross-check the whole bundle. Raises SeedDataError listing every problem."""
    problems: list[str] = []
    chapters = _models(ChapterIn, _json(data_dir / "chapters.json"), "chapters", problems)
    figures = _models(FigureIn, _json(data_dir / "figures.json"), "figures", problems)
    questions = _models(QuestionIn, _json(data_dir / "questions.json"), "questions", problems)
    taxonomy = _models(TaxonomyIn, _json(data_dir / "taxonomy.json"), "taxonomy", problems)
    stages = _models(StageIn, _json(data_dir / "pathway" / "pathway_stages.json"), "pathway_stages", problems)
    tasks = _models(TaskIn, _json(data_dir / "pathway" / "pathway_tasks.json"), "pathway_tasks", problems)
    raw_map = _json(data_dir / "question_taxonomy_map.json")
    question_map: dict[str, list[str]] = {}
    if isinstance(raw_map, dict) and all(isinstance(v, list) for v in raw_map.values()):
        question_map = {str(k): [str(x) for x in v] for k, v in raw_map.items()}
    else:
        problems.append("question_taxonomy_map.json: expected {questionId: [taxonomy ids]}")
    with (config_dir / "sources.yaml").open(encoding="utf-8") as fh:
        gate = license_gate(yaml.safe_load(fh))
    bodies_path = data_dir / SECTION_BODIES
    try:
        bodies = SectionBodies.model_validate(_json(bodies_path))
    except (OSError, ValidationError, json.JSONDecodeError) as exc:
        raise SeedDataError([f"{bodies_path}: unreadable ({type(exc).__name__}); run `{CONVERT_CMD}`"]) from exc

    if bodies.schema_version != CONTENT_SCHEMA_VERSION:
        problems.append(
            f"{SECTION_BODIES}: content schema version {bodies.schema_version}, this importer reads"
            f" {CONTENT_SCHEMA_VERSION}; update naadi_api/seed/data.py with the new node set"
        )
    unknown_nodes = sorted(set(bodies.node_types) - CONTENT_NODE_TYPES)
    unknown_marks = sorted(set(bodies.mark_types) - CONTENT_MARK_TYPES)
    if unknown_nodes or unknown_marks:
        problems.append(f"{SECTION_BODIES}: unknown node types {unknown_nodes} / mark types {unknown_marks}")

    def dupes(ids: list[str], what: str) -> None:
        seen: set[str] = set()
        for i in ids:
            if i in seen:
                problems.append(f"{what}: duplicate id {i}")
            seen.add(i)

    chapter_ids = {c.id for c in chapters}
    figure_ids = {f.id for f in figures}
    question_ids = {q.id for q in questions}
    taxonomy_ids = {t.id for t in taxonomy}
    stage_keys = {s.key for s in stages}
    source_ids = {s.id for s in gate.accepted}
    dupes([c.id for c in chapters], "chapters")
    dupes([s.id for c in chapters for s in c.sections], "sections")
    dupes([f.id for f in figures], "figures")
    dupes([q.id for q in questions], "questions")
    dupes([t.id for t in taxonomy], "taxonomy")
    dupes([s.key for s in stages], "pathway_stages")
    dupes([t.id for t in tasks], "pathway_tasks")

    if ORIGINAL_SOURCE not in source_ids:
        problems.append(f"license gate: source {ORIGINAL_SOURCE} is not seedable")
    for t in taxonomy:
        if t.parent_id is not None and t.parent_id not in taxonomy_ids:
            problems.append(f"taxonomy {t.id}: unknown parent {t.parent_id}")
    for f in figures:
        if f.chapter_id not in chapter_ids:
            problems.append(f"figure {f.id}: unknown chapter {f.chapter_id}")
        if not (data_dir / f.file).is_file():
            problems.append(f"figure {f.id}: missing file {f.file}")
    placed: list[str] = []
    for c in chapters:
        section_figs = [fid for s in c.sections for fid in s.figure_ids]
        placed += section_figs
        if section_figs != c.figure_ids:
            problems.append(f"chapter {c.id}: figureIds differ from its sections' placeholders")
        for qid in c.question_ids:
            if qid not in question_ids:
                problems.append(f"chapter {c.id}: unknown question {qid}")
        for s in c.sections:
            for fid in s.figure_ids:
                if fid not in figure_ids:
                    problems.append(f"section {s.id}: unknown figure {fid}")
            body = bodies.sections.get(s.id)
            if body is None:
                problems.append(f"section {s.id}: not in {SECTION_BODIES}; run `{CONVERT_CMD}`")
            elif body.source_sha256 != sha256_text(s.html):
                problems.append(f"section {s.id}: {SECTION_BODIES} is stale; run `{CONVERT_CMD}`")
            elif body.figure_ids != s.figure_ids:
                problems.append(f"section {s.id}: converted figures differ from figureIds")
            else:
                problems += body_problems(s.id, body.body, s.figure_ids)
                if "[[figure:" in body.body_html:
                    problems.append(f"section {s.id}: [[figure: placeholder left in bodyHtml")
    unplaced = sorted(figure_ids - set(placed))
    if unplaced:
        problems.append(f"figures never placed in a section: {unplaced}")
    extra = sorted(set(bodies.sections) - {s.id for c in chapters for s in c.sections})
    if extra:
        problems.append(f"{SECTION_BODIES} has sections not in chapters.json: {extra[:5]}")
    for q in questions:
        if q.chapter_id not in chapter_ids:
            problems.append(f"question {q.id}: unknown chapter {q.chapter_id}")
        if q.source not in source_ids:
            problems.append(f"question {q.id}: source {q.source} is not seedable")
        labels = [o.label for o in q.options]
        if labels != [chr(ord("A") + i) for i in range(len(labels))] or q.answer not in labels:
            problems.append(f"question {q.id}: options must be labelled A.. in order and include the answer")
        if q.review.human_expert_reviewed:
            problems.append(f"question {q.id}: review.humanExpertReviewed must stay false until a physician review")
        mapped = question_map.get(q.id)
        if mapped is None:
            problems.append(f"question {q.id}: missing from question_taxonomy_map.json")
        else:
            for tid in mapped:
                if tid not in taxonomy_ids:
                    problems.append(f"question {q.id}: unknown taxonomy id {tid}")
    for qid in question_map:
        if qid not in question_ids:
            problems.append(f"question_taxonomy_map.json: unknown question {qid}")
    for task in tasks:
        if task.stage_key not in stage_keys:
            problems.append(f"pathway task {task.id}: unknown stage {task.stage_key}")
    guides, guide_problems = load_guides(data_dir / "pathway", stage_keys)
    problems += guide_problems
    try:
        plan = parse_plan_csv(
            data_dir / "study_plan_step1_60d.csv",
            template_id="step1-60d",
            exam="step1",
            title="Step 1 in 60 days",
            chapter_ids=chapter_ids,
        )
    except SeedDataError as exc:
        problems += exc.problems
        plan = PlanTemplateIn("step1-60d", "step1", "Step 1 in 60 days", [])
    if problems:
        raise SeedDataError(problems)
    return SeedBundle(
        data_dir=data_dir,
        config_dir=config_dir,
        chapters=chapters,
        figures=figures,
        questions=questions,
        taxonomy=taxonomy,
        question_map=question_map,
        stages=stages,
        tasks=tasks,
        guides=guides,
        plan=plan,
        sources=gate,
        bodies=bodies,
    )


def topological_taxonomy(nodes: list[TaxonomyIn]) -> list[TaxonomyIn]:
    """Parents before children (the parent_id foreign key is checked per row)."""
    by_id = {n.id: n for n in nodes}
    out: list[TaxonomyIn] = []
    done: set[str] = set()

    def visit(n: TaxonomyIn, path: tuple[str, ...]) -> None:
        if n.id in done:
            return
        if n.id in path:
            raise SeedDataError([f"taxonomy cycle through {n.id}"])
        if n.parent_id is not None:
            visit(by_id[n.parent_id], (*path, n.id))
        done.add(n.id)
        out.append(n)

    for n in sorted(nodes, key=lambda x: (x.sort, x.id)):
        visit(n, ())
    return out
