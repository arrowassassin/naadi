"""The taxonomy cell an item is written for: exam, system, discipline (Step 1) or clinical science (Step 2 CK),
competency and topic. Pure: the taxonomy is handed in."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

EXAMS = ("step1", "step2ck")


@dataclass(frozen=True)
class TaxNode:
    id: str
    kind: str
    label: str
    parent_id: str | None
    exam: tuple[str, ...]
    sort: int = 0


@dataclass(frozen=True)
class Cell:
    exam: str
    system_id: str
    second_id: str  # discipline (step1) or clinical_science (step2ck)
    competency_id: str
    topic_id: str

    @property
    def taxonomy_ids(self) -> list[str]:
        return [self.system_id, self.second_id, self.competency_id, self.topic_id]

    def as_dict(self) -> dict[str, str]:
        return {
            "exam": self.exam,
            "system": self.system_id,
            "discipline" if self.exam == "step1" else "clinical_science": self.second_id,
            "competency": self.competency_id,
            "topic": self.topic_id,
        }


class CellError(ValueError):
    """A cell the taxonomy does not allow. `code` is a stable machine code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def system_for(taxonomy: Mapping[str, TaxNode], exam: str, topic: TaxNode) -> str | None:
    """Topics hang under a Step 1 system; the same system under another exam shares its suffix
    (`step1.sys.renal` -> `step2ck.sys.renal`)."""
    if not topic.parent_id:
        return None
    if exam == "step1":
        return topic.parent_id
    suffix = topic.parent_id.split(".sys.", 1)[-1]
    cand = f"{exam}.sys.{suffix}"
    return cand if cand in taxonomy else None


def default_competency(taxonomy: Mapping[str, TaxNode], exam: str) -> str | None:
    cand = f"{exam}.comp.medical_knowledge"
    return cand if cand in taxonomy else None


def first_of_kind(taxonomy: Mapping[str, TaxNode], exam: str, kind: str) -> str | None:
    nodes = sorted((n for n in taxonomy.values() if n.kind == kind and exam in n.exam), key=lambda n: (n.sort, n.id))
    return nodes[0].id if nodes else None


def build_cell(
    taxonomy: Mapping[str, TaxNode],
    exam: str,
    topic_id: str,
    *,
    second_id: str | None = None,
    competency_id: str | None = None,
    system_id: str | None = None,
    common_second: str | None = None,
) -> Cell:
    """Validate (or derive) every part of a cell against the taxonomy. `common_second` is the discipline most
    published questions of the topic already carry (the default when the admin names none)."""
    if exam not in EXAMS:
        raise CellError("exam_unsupported")
    topic = taxonomy.get(topic_id)
    if topic is None or topic.kind != "topic" or exam not in topic.exam:
        raise CellError("topic_unknown")
    system = system_id or system_for(taxonomy, exam, topic)
    if (
        system is None
        or taxonomy.get(system, TaxNode("", "", "", None, ())).kind != "system"
        or exam not in taxonomy[system].exam
    ):
        raise CellError("system_unknown")
    second_kind = "discipline" if exam == "step1" else "clinical_science"
    second = second_id or common_second or first_of_kind(taxonomy, exam, second_kind)
    if (
        second is None
        or taxonomy.get(second, TaxNode("", "", "", None, ())).kind != second_kind
        or exam not in taxonomy[second].exam
    ):
        raise CellError("second_unknown")
    comp = competency_id or default_competency(taxonomy, exam)
    if (
        comp is None
        or taxonomy.get(comp, TaxNode("", "", "", None, ())).kind != "competency"
        or exam not in taxonomy[comp].exam
    ):
        raise CellError("competency_unknown")
    return Cell(exam, system, second, comp, topic_id)


def labels(taxonomy: Mapping[str, TaxNode], cell: Cell) -> list[str]:
    return [taxonomy[i].label for i in (cell.system_id, cell.second_id, cell.competency_id, cell.topic_id)]
