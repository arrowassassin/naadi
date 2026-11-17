"""The item linter (`config/authoring_rules.md` Part B): pure Python, no database, no model.

    report = lint_question(item, ctx)       # item has the `questions` row shape; ctx is optional

`item` keys (snake_case; `normalize_item` also accepts the seed's camelCase): `exam`, `stem_html`,
`options[{label, html}]`, `answer`, `explanation_html`, `why_not_html`, `educational_objective`, `difficulty`,
`taxonomy_ids`, `source_id`, and optionally `excerpt_source_ids`, `demographic_relevance`, `citations`.

Findings are structured (rule id, severity, field, params): the clients word them with `ui_copy`. ERROR blocks
`draft -> auto_checked`, WARN goes to the human reviewer, INFO is logged only. Context that needs the database
(taxonomy, sources, similarity) is handed in by the caller; a rule whose context is absent is skipped, never
guessed. The editor (segment 19) and the author graph call the same function.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any, Literal

Severity = Literal["error", "warn", "info"]
LABELS = ("A", "B", "C", "D", "E")
DIFFICULTIES = ("easy", "medium", "hard")

# Licences an AI draft may be grounded on (HANDOFF 7.1): Naadi originals, US public domain, CC BY.
GROUNDING_LICENSE = re.compile(r"^(proprietary-naadi|public-domain.*|us-gov.*|cc-by(-4\.0|-3\.0)?|cc0.*)$", re.I)


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: Severity
    target: str = ""
    params: dict[str, Any] = dataclass_field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"rule": self.rule, "severity": self.severity, "target": self.target, "params": self.params}


@dataclass(frozen=True)
class LintReport:
    findings: tuple[Finding, ...]

    @property
    def errors(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity == "error")

    @property
    def warnings(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity == "warn")

    @property
    def ok(self) -> bool:
        return not self.errors

    def rules(self) -> set[str]:
        return {f.rule for f in self.findings}

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "findings": [f.as_dict() for f in self.findings]}


@dataclass(frozen=True)
class SourceInfo:
    allow_ai_ingest: bool
    license: str = ""


@dataclass(frozen=True)
class LabRef:
    names: tuple[str, ...]  # lower-case names this analyte goes by in a stem
    low: float
    high: float
    units: frozenset[str]  # conventional and SI units, normalised
    si_low: float | None = None
    si_high: float | None = None
    si_unit: str | None = None
    conventional_unit: str = ""


@dataclass
class LintContext:
    """What the linter cannot know by itself. Every field is optional; a rule without its context is skipped."""

    taxonomy: Mapping[str, str] | None = None  # taxonomy id -> kind
    sources: Mapping[str, SourceInfo] | None = None
    labs: Sequence[LabRef] | None = None
    max_similarity: float | None = None  # highest cosine against the existing bank (L16)
    solver_confidence: float | None = None  # from the blind solve (L18)
    requested_difficulty: str | None = None
    # Legacy seed items carry no educational objective: a missing one is a WARN for them, an ERROR for new items.
    objective_optional: bool = False
    similarity_error: float = 0.92
    similarity_warn: float = 0.85


# ── text helpers ──

_TAG = re.compile(r"<[^>]+>")
_BLOCK_END = re.compile(r"</(p|div|li|ul|ol|h[1-6]|tr|table)>|<br\s*/?>", re.I)
_WS = re.compile(r"\s+")
_WORD = re.compile(r"[a-z0-9][a-z0-9'-]*")


def _words(s: str) -> frozenset[str]:
    return frozenset(s.split())


STOPWORDS = _words(
    "a an the of and or in on at to for with without from by as is are was were be been being has have had do does "
    "did this that these those it its his her their he she they them which what who whom whose when where while "
    "than then there here also into onto over under after before during between most more less least very can "
    "could will would should may might must not no yes if but so such each any all both either other another same"
)
# Words every vignette contains: they say nothing about which option is the key.
GENERIC = _words(
    "patient patients following likely diagnosis management treatment mechanism cause finding findings history "
    "examination physician presents presented comes brought year-old man woman boy girl male female years months "
    "weeks days hours presentation symptoms most next step appropriate"
)


def plain(s: str | None) -> str:
    """HTML to text: block ends become newlines, tags go, entities decode, spaces collapse (case kept)."""
    if not s:
        return ""
    s = _BLOCK_END.sub("\n", s)
    s = html.unescape(_TAG.sub(" ", s))
    lines = [_WS.sub(" ", ln).strip() for ln in s.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def flat(s: str | None) -> str:
    return plain(s).replace("\n", " ")


def tokens(s: str) -> list[str]:
    return _WORD.findall(s.lower())


def content_tokens(s: str) -> set[str]:
    return {t for t in tokens(s) if len(t) > 2 and t not in STOPWORDS and t not in GENERIC}


def contained(option: set[str], stem: set[str]) -> float:
    """Share of an option's content words that also appear in the stem. (The spec names Jaccard, but against a
    stem of 40+ words a two-word option can never reach a Jaccard of 0.15, so the cue is measured as
    containment; the 0.15 key-minus-best-distractor margin is unchanged.)"""
    return len(option & stem) / len(option) if option else 0.0


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


_SENT_SPLIT = re.compile(r"(?<=[.?!])\s+(?=[A-Z0-9\"'(])")


def lead_in_of(stem_html: str) -> str:
    """The last sentence of the stem."""
    text = plain(stem_html)
    last_line = text.split("\n")[-1] if text else ""
    parts = [p for p in _SENT_SPLIT.split(last_line) if p.strip()]
    return parts[-1].strip() if parts else last_line.strip()


def normalize_item(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Accept the seed's camelCase or the row's snake_case; return the snake_case dict the rules read."""
    pairs = {
        "stemHtml": "stem_html",
        "explanationHtml": "explanation_html",
        "whyNotHtml": "why_not_html",
        "educationalObjective": "educational_objective",
        "taxonomyIds": "taxonomy_ids",
        "sourceId": "source_id",
        "source": "source_id",
        "excerptSourceIds": "excerpt_source_ids",
        "demographicRelevance": "demographic_relevance",
    }
    out: dict[str, Any] = {}
    for k, v in raw.items():
        out[pairs.get(k, k)] = v
    return out


# ── per-option helpers ──


def _options(item: Mapping[str, Any]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for o in item.get("options") or []:
        if isinstance(o, Mapping):
            out.append((str(o.get("label") or "").strip().upper(), flat(str(o.get("html") or ""))))
    return out


_LEADING_NUM = re.compile(r"^\s*[<>≤≥~]?\s*(-?\d+(?:\.\d+)?)")
_RANGE = re.compile(r"(-?\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(-?\d+(?:\.\d+)?)")


# ── rules ──


def _schema_rules(
    item: Mapping[str, Any], opts: list[tuple[str, str]], out: list[Finding], objective_optional: bool
) -> None:
    labels = [lab for lab, _ in opts]
    if len(opts) != 5 or sorted(labels) != list(LABELS) or len(set(labels)) != 5 or any(not t for _, t in opts):
        out.append(Finding("S01", "error", "options", {"count": len(opts), "labels": labels}))
    answer = str(item.get("answer") or "").strip().upper()
    if answer not in labels:
        out.append(Finding("S02", "error", "answer", {"answer": answer}))
    missing: list[str] = []
    for key in ("explanation_html", "why_not_html", "educational_objective"):
        if not flat(item.get(key)):
            missing.append(key)
    if str(item.get("difficulty") or "") not in DIFFICULTIES:
        missing.append("difficulty")
    why = flat(item.get("why_not_html"))
    unmentioned = [
        lab for lab in labels if lab != answer and why and not re.search(rf"(?<![A-Za-z]){lab}(?![A-Za-z])", why)
    ]
    if objective_optional and missing == ["educational_objective"] and not unmentioned:
        out.append(Finding("S03", "warn", "educational_objective", {"missing": missing, "unmentioned": []}))
    elif missing or unmentioned:
        out.append(Finding("S03", "error", "why_not_html", {"missing": missing, "unmentioned": unmentioned}))


def _taxonomy_rule(item: Mapping[str, Any], ctx: LintContext, out: list[Finding]) -> None:
    if ctx.taxonomy is None:
        return
    ids = [str(i) for i in (item.get("taxonomy_ids") or [])]
    unknown = [i for i in ids if i not in ctx.taxonomy]
    kinds = [ctx.taxonomy[i] for i in ids if i in ctx.taxonomy]
    exam = str(item.get("exam") or "")
    second = "discipline" if exam == "step1" else "clinical_science"
    lacking = [k for k in ("system", second, "competency", "topic") if k not in kinds]
    if kinds.count("competency") > 1:
        lacking.append("one_competency")
    if unknown or lacking:
        out.append(Finding("S04", "error", "taxonomy_ids", {"unknown": unknown, "missing": lacking}))


def _source_rule(item: Mapping[str, Any], ctx: LintContext, out: list[Finding]) -> None:
    if ctx.sources is None:
        return
    bad: list[dict[str, str]] = []
    used = [str(item["source_id"])] if item.get("source_id") else []
    used += [str(s) for s in (item.get("excerpt_source_ids") or [])]
    if not used:
        bad.append({"id": "", "why": "no_source"})
    for sid in dict.fromkeys(used):
        src = ctx.sources.get(sid)
        if src is None:
            bad.append({"id": sid, "why": "unknown_source"})
        elif not src.allow_ai_ingest:
            bad.append({"id": sid, "why": "not_ai_ingestible"})
        elif src.license and not GROUNDING_LICENSE.match(src.license):
            bad.append({"id": sid, "why": "licence_not_allowed"})
    if bad:
        out.append(Finding("S05", "error", "source_id", {"sources": bad}))


_BANNED_OPTION = re.compile(r"\b(?:all|none) of the above\b|\bboth [a-e] and [a-e]\b", re.I)
_NEGATIVE = re.compile(r"\b(?:except|not|least)\b", re.I)
_ABSOLUTE = re.compile(r"\b(always|never|only|rarely|usually|frequently|often|sometimes|commonly)\b", re.I)
_NOT_TRUE = re.compile(r"which of the following (?:is|are) (?:true|correct)", re.I)


def _wording_rules(stem: str, lead: str, opts: list[tuple[str, str]], out: list[Finding]) -> None:
    banned = [lab for lab, t in opts if _BANNED_OPTION.search(t)]
    if banned:
        out.append(Finding("L01", "error", "options", {"options": banned}))
    if _NEGATIVE.search(lead):
        out.append(
            Finding("L02", "error", "stem_html", {"words": sorted({m.lower() for m in _NEGATIVE.findall(lead)})})
        )
    found = sorted(
        {m.lower() for m in _ABSOLUTE.findall(stem)} | {m.lower() for _, t in opts for m in _ABSOLUTE.findall(t)}
    )
    if found:
        out.append(Finding("L03", "warn", "stem_html", {"words": found}))
    flat_stem = stem.replace("\n", " ").strip()
    if not flat_stem.endswith("?") or flat_stem.count("?") != 1 or _NOT_TRUE.search(lead):
        out.append(
            Finding(
                "L04",
                "error",
                "stem_html",
                {"ends_with_question": flat_stem.endswith("?"), "question_marks": flat_stem.count("?")},
            )
        )


def _cue_rules(stem: str, opts: list[tuple[str, str]], answer: str, out: list[Finding]) -> None:
    key = next((t for lab, t in opts if lab == answer), None)
    distractors = [(lab, t) for lab, t in opts if lab != answer]
    if key is None or len(distractors) < 2:
        return
    mean_len = sum(len(t) for _, t in distractors) / len(distractors)
    if mean_len and len(key) > 1.5 * mean_len:
        out.append(
            Finding("L05", "warn", "options", {"key_length": len(key), "mean_distractor_length": round(mean_len, 1)})
        )
    stem_tokens = content_tokens(stem)
    key_j = contained(content_tokens(key), stem_tokens)
    best = max(contained(content_tokens(t), stem_tokens) for _, t in distractors)
    if key_j - best > 0.15:
        out.append(
            Finding(
                "L06", "warn", "options", {"key_overlap": round(key_j, 2), "best_distractor_overlap": round(best, 2)}
            )
        )
    sets = {lab: content_tokens(t) for lab, t in opts}
    means: dict[str, float] = {}
    for lab, s in sets.items():
        others = [jaccard(s, o) for olab, o in sets.items() if olab != lab]
        means[lab] = sum(others) / len(others) if others else 0.0
    ranked = sorted(means.values(), reverse=True)
    if ranked and ranked[0] > 0 and means.get(answer) == ranked[0] and (len(ranked) < 2 or ranked[0] > ranked[1]):
        out.append(Finding("L07", "warn", "options", {"overlap": round(ranked[0], 2)}))


def _numeric(t: str) -> float | None:
    m = _LEADING_NUM.match(t)
    return float(m.group(1)) if m else None


def _order_and_numbers(opts: list[tuple[str, str]], out: list[Finding]) -> None:
    texts = [t for _, t in opts]
    if len(texts) < 3:
        return
    nums = [_numeric(t) for t in texts]
    numeric = all(n is not None for n in nums)
    if numeric:
        vals = [n for n in nums if n is not None]
        ordered = vals == sorted(vals) or vals == sorted(vals, reverse=True)
    else:
        folded = [t.casefold() for t in texts]
        ordered = folded == sorted(folded)
    if not ordered:
        out.append(Finding("L08", "warn", "options", {"numeric": numeric}))
    # L09: numeric options, one unit and one format, and no overlapping ranges.
    with_num = [(lab, t) for (lab, t), n in zip(opts, nums, strict=True) if n is not None]
    if len(with_num) >= 3:
        units = {
            re.sub(r"^[<>≤≥~\s]*-?\d+(?:\.\d+)?(?:\s*(?:-|–|to)\s*-?\d+(?:\.\d+)?)?\s*", "", t).strip().lower()
            for _, t in with_num
        }
        is_range = {bool(_RANGE.search(t)) for _, t in with_num}
        problems: list[str] = []
        if len(units) > 1:
            problems.append("units")
        if len(is_range) > 1:
            problems.append("format")
        ranges = [(lab, float(m.group(1)), float(m.group(2))) for lab, t in with_num if (m := _RANGE.search(t))]
        overlap = [
            (a[0], b[0]) for i, a in enumerate(ranges) for b in ranges[i + 1 :] if min(a[2], b[2]) >= max(a[1], b[1])
        ]
        if overlap:
            problems.append("overlap")
        if problems:
            out.append(Finding("L09", "warn", "options", {"problems": problems}))


def _alt(*parts: str, tail: str = r"\b") -> re.Pattern[str]:
    return re.compile(r"\b(?:" + "|".join(parts) + ")" + tail, re.I)


_DRUG = re.compile(
    "(?:"
    + "|".join(
        sorted(
            _words(
                "mab pril sartan olol statin azole cillin mycin floxacin vir pam lam tidine prazole dipine thiazide "
                "parin xaban gliptin gliflozin tinib ciclib curonium"
            )
        )
    )
    + r")\b",
    re.I,
)
_TEST = _alt(
    "test",
    "assay",
    "biopsy",
    "ct",
    "mri",
    "x-ray",
    "radiograph",
    "ultrasound",
    "ultrasonography",
    "culture",
    "angiography",
    "scan",
    "endoscopy",
    "colonoscopy",
    r"electrocardiogra\w+",
    "ecg",
    r"echocardiogra\w+",
    "measurement",
    "level",
    "titer",
    "smear",
    "polysomnography",
    "spirometry",
    "lumbar puncture",
)
_MECH = _alt(
    "inhibition",
    "inhibitor",
    "activation",
    "blockade",
    "agonist",
    "antagonist",
    "binding",
    "deficiency",
    "mutation",
    "impaired",
    "increased",
    "decreased",
    "defective",
    "loss of",
    "release",
    "synthesis",
    "upregulation",
    "downregulation",
    "overexpression",
    "reuptake",
    "receptor",
    "enzyme",
    "channel",
)
_DOUBLE = re.compile(r"\s(?:and|plus|\+)\s|;", re.I)


def _kind_of(t: str) -> str:
    if _numeric(t) is not None:
        return "number"
    if _DRUG.search(t):
        return "drug"
    if _TEST.search(t):
        return "test"
    if _MECH.search(t):
        return "mechanism"
    return "other"


def _homogeneity_rules(lead: str, opts: list[tuple[str, str]], answer: str, out: list[Finding]) -> None:
    kinds = {_kind_of(t) for _, t in opts} - {"other"}
    doubles = [lab for lab, t in opts if _DOUBLE.search(" " + t + " ")]
    problems: list[str] = []
    if len(kinds) > 1:
        problems.append("types")
    if 0 < len(doubles) < len(opts):
        problems.append("double")
    if problems:
        out.append(Finding("L10", "warn", "options", {"problems": problems, "types": sorted(kinds)}))
    # L11: article and number agreement.
    last = re.sub(r"[^a-z ]", "", lead.lower()).split()
    if last and last[-1] in ("a", "an"):
        want_vowel = last[-1] == "an"
        bad = [lab for lab, t in opts if t and _starts_with_vowel_sound(t) != want_vowel]
        if bad:
            out.append(Finding("L11", "warn", "options", {"article": last[-1], "options": bad}))
        return
    plural = {lab: _plural(t) for lab, t in opts if t}
    key = plural.get(answer)
    rest = {v for lab, v in plural.items() if lab != answer}
    if key is not None and len(rest) == 1 and key not in rest:
        out.append(Finding("L11", "warn", "options", {"number_mismatch": answer}))


_CONSONANT_SOUND = re.compile(r"^(?:uni|use|usu|uti|eu|one|ur[ei])", re.I)
_VOWEL_SOUND = re.compile(r"^(?:hour|honest|heir|mri|hiv|x-?ray|nsaid|sle|ecg|rna|dna|llm)", re.I)


def _starts_with_vowel_sound(t: str) -> bool:
    w = t.strip().lower()
    if _VOWEL_SOUND.match(w):
        return True
    if _CONSONANT_SOUND.match(w):
        return False
    return bool(w) and w[0] in "aeiou"


def _plural(t: str) -> bool:
    w = (re.findall(r"[A-Za-z]+", t) or [""])[-1].lower()
    return len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "is", "us", "os", "as", "ics"))


_VITALS = _alt(
    "temperature",
    "blood pressure",
    "pulse",
    "heart rate",
    "respirations?",
    "respiratory rate",
    "oxygen saturation",
    "pulse oximetry",
    "bp",
)
_EXAM = _alt("examination", "physical exam", "on exam", "exam shows", "auscultation", "palpation", "inspection")
_LABS = _alt(
    "laboratory",
    "serum",
    "plasma",
    "hemoglobin",
    "leukocyte",
    "platelet",
    "urinalysis",
    "x-ray",
    "radiograph",
    "ct scan",
    "mri",
    "ultrasonography",
    "biopsy",
    "culture",
    "electrocardiogram",
    "ecg",
    "arterial blood gas",
)
_HINGE = re.compile(
    r"\b(?:previous|prior|preceding|above|next) (?:question|item)\b|\b(?:question|item) \d+\b"
    r"|\bch\d+-q\d+\b|\bsame patient as\b",
    re.I,
)
_DEMOGRAPHIC = re.compile(
    r"\b(?:african[- ]american|hispanic|latino|latina|caucasian|asian|native american|pacific islander|"
    r"ashkenazi|jewish|middle eastern|indigenous)\b"
    r"|\b(?:black|white)\s+(?:man|woman|boy|girl|male|female|patient|adult|child|infant|person)\b"
    r"|\b(?:man|woman|boy|girl|patient)\s+of\s+(?:black|white|\w+)\s+(?:descent|ancestry|ethnicity)\b",
    re.I,
)


def _vignette_rules(item: Mapping[str, Any], stem: str, out: list[Finding]) -> None:
    pos = {
        name: (m.start() if (m := rx.search(stem)) else None)
        for name, rx in (("v", _VITALS), ("e", _EXAM), ("l", _LABS))
    }
    earliest_core = min((p for k, p in pos.items() if k in "ve" and p is not None), default=None)
    if pos["l"] is not None and earliest_core is not None and pos["l"] < earliest_core:
        out.append(Finding("L12", "warn", "stem_html", {}))
    words = len(tokens(stem))
    if words < 40 or words > 250:
        out.append(Finding("L14", "warn", "stem_html", {"words": words}))
    if _HINGE.search(stem):
        out.append(Finding("L15", "warn", "stem_html", {}))
    if _DEMOGRAPHIC.search(stem) and not flat(item.get("demographic_relevance")):
        out.append(Finding("L17", "warn", "stem_html", {}))


# ── lab values (L13) ──

_UNIT_MAP = str.maketrans({"µ": "u", "μ": "u"})
_NUMBER_UNIT = re.compile(
    r"^\s*(?:(?:level|concentration|count|value)s?\s*)?(?:of|is|was|:|=)?\s*(?!\d+\s*(?:months?|weeks?|days?|hours?|years?|times|minutes?)\b)(-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?)\s*(?P<unit>[A-Za-zµμ%][A-Za-zµμ%0-9]*(?:/[A-Za-zµμ0-9]+)*)?",
    re.I,
)


def norm_unit(u: str) -> str:
    return u.translate(_UNIT_MAP).lower().replace(" ", "")


def lab_refs(lab_values: Mapping[str, Any]) -> list[LabRef]:
    """`LabRef`s from the `app_config.lab_values` value (Naadi's own reference table)."""
    refs: list[LabRef] = []
    claimed: set[str] = set()  # "Bilirubin, total" is listed before "Bilirubin, direct": a bare "bilirubin" is total
    for group in lab_values.get("groups", []):
        for row in group.get("rows", []):
            conv = _parse_range(str(row.get("range") or ""))
            if conv is None:
                continue
            si = _parse_range(str(row.get("si") or "")) if row.get("si") else None
            name = str(row["analyte"])
            names = {re.sub(r"\s*\(.*?\)", "", name).split(",")[0].strip().lower()}
            for paren in re.findall(r"\((.*?)\)", name):
                names.add(paren.strip().lower())
            names -= claimed
            claimed |= names
            units = {norm_unit(conv[2])} | ({norm_unit(si[2])} if si else set())
            refs.append(
                LabRef(
                    tuple(sorted(n for n in names if n)),
                    conv[0],
                    conv[1],
                    frozenset(u for u in units if u),
                    si[0] if si else None,
                    si[1] if si else None,
                    norm_unit(si[2]) if si else None,
                    norm_unit(conv[2]),
                )
            )
    return refs


def _parse_range(s: str) -> tuple[float, float, str] | None:
    m = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*(?:-|–)\s*(-?\d+(?:\.\d+)?)\s*(.*)$", s)
    return (float(m.group(1)), float(m.group(2)), m.group(3).strip()) if m else None


# The plausible ceiling is a generous multiple of the upper reference limit: an eGFR-sized slip (a value in the
# wrong unit, or a misplaced decimal) is caught without rejecting a genuine critical value.
PLAUSIBLE_FACTOR = 25.0
ENZYME_FACTOR = 400.0  # transaminases reach thousands of U/L in fulminant hepatitis


# The reference table is for blood: a urine, CSF or 24-hour value of the same analyte has other ranges and units.
_OTHER_FLUID = re.compile(
    r"urin|24-h|csf|cerebrospinal|salivary|stool|fecal|sputum|synovial|pleural|ascitic|fractional|excretion", re.I
)


def _lab_rule(stem: str, ctx: LintContext, out: list[Finding]) -> None:
    if not ctx.labs:
        return
    problems: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []
    for ref in ctx.labs:
        for name in ref.names:
            for m in re.finditer(rf"(?<![a-z]){re.escape(name)}(?![a-z])(?!-\d)", stem, re.I):
                parsed = _NUMBER_UNIT.match(stem[m.end() : m.end() + 60])
                if not parsed or _OTHER_FLUID.search(stem[max(0, m.start() - 30) : m.start()]):
                    continue
                after = stem[m.end() + parsed.end(1) : m.end() + parsed.end(1) + 12]
                if re.match(r"\s*(?:[×x]|/\s*-?\d)", after):
                    continue  # "3× the upper limit" and "10.2 / 3.2 g/dL" (before / after): not a single value
                value = float(parsed.group(1).replace(",", ""))
                unit = norm_unit(parsed.group("unit") or "")
                known = unit in ref.units or (unit == "%" and any(u.startswith("%") for u in ref.units))
                if not known and not (("/" in unit) or unit == "%"):
                    unit = ""  # a trailing word, not a unit
                if ref.units and not unit:
                    problems.append({"analyte": name, "value": value, "why": "unit_missing"})
                    continue
                if "/kg" in unit or "/day" in unit:
                    continue  # a dose, not a result
                if unit and not known:
                    notes.append({"analyte": name, "value": value, "unit": unit, "why": "unit_unknown"})
                    continue
                si = ref.si_unit is not None and unit == ref.si_unit and unit != ref.conventional_unit
                factor = ENZYME_FACTOR if unit == "u/l" else PLAUSIBLE_FACTOR
                high = (ref.si_high if si and ref.si_high is not None else ref.high) * factor
                if value < 0 or value > max(high, 1.0):
                    problems.append({"analyte": name, "value": value, "unit": unit, "why": "implausible"})
    if problems:
        out.append(Finding("L13", "error", "stem_html", {"values": problems[:10]}))
    if notes:  # a unit the reference table does not list (magnesium in mg/dL): the reviewer decides
        out.append(Finding("L13", "warn", "stem_html", {"values": notes[:10]}))


def _similarity_and_difficulty(ctx: LintContext, out: list[Finding]) -> None:
    if ctx.max_similarity is not None:
        if ctx.max_similarity > ctx.similarity_error:
            out.append(Finding("L16", "error", "stem_html", {"similarity": round(ctx.max_similarity, 3)}))
        elif ctx.max_similarity >= ctx.similarity_warn:
            out.append(Finding("L16", "warn", "stem_html", {"similarity": round(ctx.max_similarity, 3)}))
    if ctx.solver_confidence is not None and ctx.requested_difficulty in DIFFICULTIES:
        c = ctx.solver_confidence
        mismatch = (ctx.requested_difficulty == "easy" and c < 0.5) or (ctx.requested_difficulty == "hard" and c > 0.9)
        if mismatch:
            out.append(
                Finding("L18", "info", "difficulty", {"confidence": round(c, 2), "requested": ctx.requested_difficulty})
            )


def lint_question(item: Mapping[str, Any], ctx: LintContext | None = None) -> LintReport:
    ctx = ctx or LintContext()
    item = normalize_item(item)
    opts = _options(item)
    stem = flat(item.get("stem_html"))
    lead = lead_in_of(str(item.get("stem_html") or ""))
    answer = str(item.get("answer") or "").strip().upper()
    out: list[Finding] = []
    _schema_rules(item, opts, out, ctx.objective_optional)
    _taxonomy_rule(item, ctx, out)
    _source_rule(item, ctx, out)
    _wording_rules(stem, lead, opts, out)
    if len(opts) == 5:
        _cue_rules(stem, opts, answer, out)
        _order_and_numbers(opts, out)
        _homogeneity_rules(lead, opts, answer, out)
    _vignette_rules(item, stem, out)
    _lab_rule(stem, ctx, out)
    _similarity_and_difficulty(ctx, out)
    return LintReport(tuple(out))


def blocking(findings: Iterable[Finding]) -> list[Finding]:
    return [f for f in findings if f.severity == "error"]


SimilarityFn = Callable[[str], float]
