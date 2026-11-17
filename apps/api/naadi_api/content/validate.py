"""Content-library validators (segment 23, S23-0 phase 2). Pure: no database, no network.

`validate(bundle, config_dir)` runs every check on the pack content of a loaded seed bundle (load_bundle has
already enforced the schema, ids, tags, sets and references) and returns findings:

- `lint`: the NBME item-writing linter of the author graph (naadi_api/authoring/lint.py, the same rules the admin
  review queue runs): banned options, negative stems, cueing, homogeneity, vignette, lab values and units against
  Naadi's own lab table (L13). Options beyond E are allowed (the 2026 brief: "at least 5").
- `why_not`: every wrong option has a reason of at least 4 words, and none restates another option's reason.
- `key`: the key is consistent: no two options share a text, the explanation never names another letter as the
  answer ("Answer: C", "the answer is C", "correct answer (C)"), and items of one set share its exam.
- `licence`: every stored row (chapter, item, set, card, figure) uses a source the publishing gate allows for
  stored text or a hosted file (naadi_api/admin/licence.py with the shipped `sources_policy`); further-reading and
  reference sources only need to exist (they are links).
- `originality`: 8-gram shingles of every stem, explanation, section and card. (a) Against the forbidden-corpus
  fingerprints in config/originality_fingerprints.txt (salted SHA-256 of 8-grams; the corpus itself is never
  stored); (b) the FALLBACK that applies while that file is empty: an internal near-duplicate check, where a
  pack text that shares more than ORIGINALITY_THRESHOLD of its 8-grams with another stored text is an error,
  plus the stratified manual review sample (`review_sample`) that S23-R reads. docs/CONTENT_AUTHORING.md §9
  says which mode is active.

Severity: "error" fails the run (and CI); "warn" is listed for the author and the reviewer.
"""

from __future__ import annotations

import hashlib
import random
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from ..admin.licence import AssetFacts, SourceTerms, decide
from ..authoring.lint import LintContext, SourceInfo, flat, lab_refs, lint_question
from ..config_api.schemas import SourcesPolicy
from ..seed.data import SeedBundle

Severity = Literal["error", "warn"]
SHINGLE = 8
ORIGINALITY_THRESHOLD = 0.35  # share of a text's 8-grams found in one other text
MIN_SHINGLES = 6  # texts shorter than this many 8-grams are too short to judge
FINGERPRINT_FILE = "originality_fingerprints.txt"
FINGERPRINT_THRESHOLD = 0.08  # share of a text's 8-grams that match forbidden fingerprints
_ANSWER_CLAIM = re.compile(
    r"\b(?:answer\s*(?:is|:)\s*\(?|correct\s+(?:answer|option|choice)\s*(?:is\s*)?\(?|choice\s+)([A-J])\b(?!\w)"
)
_WORD = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class Finding:
    check: str
    severity: Severity
    ref: str  # "item s23b-q0001", "section s23b-s0101", ...
    pack: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {
            "check": self.check,
            "severity": self.severity,
            "ref": self.ref,
            "pack": self.pack,
            "detail": self.detail,
        }


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    originality_mode: Literal["fingerprints", "fallback"] = "fallback"
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def ok(self) -> bool:
        return not self.errors


# ── helpers ──


def words(text: str) -> list[str]:
    return _WORD.findall(flat(text).lower())


def shingles(text: str, n: int = SHINGLE) -> set[str]:
    w = words(text)
    return {" ".join(w[i : i + n]) for i in range(len(w) - n + 1)}


def fingerprint(shingle: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}\x00{shingle}".encode()).hexdigest()


def load_fingerprints(config_dir: Path) -> tuple[str, set[str]]:
    """`# salt: <hex>` then one hex digest per line. Missing or empty: no fingerprints (fallback mode)."""
    path = config_dir / FINGERPRINT_FILE
    if not path.is_file():
        return "", set()
    salt = ""
    digests: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("# salt:"):
            salt = line.split(":", 1)[1].strip()
        elif line and not line.startswith("#"):
            digests.add(line.lower())
    return salt, digests


def _sources(config_dir: Path) -> dict[str, dict[str, Any]]:
    raw = yaml.safe_load((config_dir / "sources.yaml").read_text(encoding="utf-8"))
    return {str(r["id"]): dict(r) for r in raw.get("sources", []) if isinstance(r, Mapping) and "id" in r}


def _policy(config_dir: Path) -> SourcesPolicy:
    app = yaml.safe_load((config_dir / "app_config.yaml").read_text(encoding="utf-8")) or {}
    raw = app.get("sources_policy")
    return SourcesPolicy.model_validate(raw) if isinstance(raw, Mapping) else SourcesPolicy()


# ── the checks ──


def _lint(b: SeedBundle, config_dir: Path, out: list[Finding]) -> None:
    app = yaml.safe_load((config_dir / "app_config.yaml").read_text(encoding="utf-8")) or {}
    srcs = _sources(config_dir)
    ctx = LintContext(
        sources={k: SourceInfo(bool(v.get("allow_ai_ingest")), str(v.get("license") or "")) for k, v in srcs.items()},
        labs=lab_refs(app.get("lab_values") or {}),
    )
    for q in b.questions:
        if q.pack_id is None:
            continue
        item = {
            "stem_html": q.stem_html,
            "options": [o.model_dump() for o in q.options],
            "answer": q.answer,
            "explanation_html": q.explanation_html,
            "why_not_html": q.why_not_html,
            "educational_objective": q.objective,
            "difficulty": q.difficulty,
            "source_id": q.source,
            "exam": q.exam,
        }
        report = lint_question(item, ctx)
        for f in report.findings:
            if (
                f.rule == "S01"
                and len(q.options) > 5
                and [o.label for o in q.options] == [chr(65 + i) for i in range(len(q.options))]
            ):
                continue  # 6+ options are allowed by the 2026 brief; the linter's other rules still run
            if f.severity not in ("error", "warn"):
                continue
            if f.rule == "L14" and q.item_format == "abstract":
                continue  # the study summary (the set's intro) is the vignette; the stem only asks

            sev: Severity = "error" if f.severity == "error" else "warn"
            out.append(Finding("lint", sev, f"item {q.id}", q.pack_id, f"{f.rule} {f.target} {f.params}"))


def _why_not_and_key(b: SeedBundle, out: list[Finding]) -> None:
    sets = {s.id: s for _p, s in b.sets}
    for q in b.questions:
        if q.pack_id is None:
            continue
        ref = f"item {q.id}"
        texts = [flat(o.html).lower() for o in q.options]
        dupes = [t for t, n in Counter(texts).items() if n > 1]
        if dupes:
            out.append(Finding("key", "error", ref, q.pack_id, f"options share a text: {dupes[:2]}"))
        for lab, why in (q.why_not or {}).items():
            if len(words(why)) < 4:
                out.append(Finding("why_not", "error", ref, q.pack_id, f"why-not {lab} is under 4 words"))
        reasons = [flat(v).lower() for v in (q.why_not or {}).values()]
        if len(set(reasons)) < len(reasons):
            out.append(Finding("why_not", "error", ref, q.pack_id, "two wrong options share the same why-not text"))
        for m in _ANSWER_CLAIM.finditer(flat(q.explanation_html)):
            if m.group(1) != q.answer:
                out.append(
                    Finding("key", "error", ref, q.pack_id, f"explanation names {m.group(1)} but the key is {q.answer}")
                )
        if q.set_id and q.set_id in sets and sets[q.set_id].exam != q.exam:
            out.append(Finding("key", "error", ref, q.pack_id, f"exam differs from set {q.set_id}"))


def _licence(b: SeedBundle, config_dir: Path, out: list[Finding]) -> None:
    srcs = _sources(config_dir)
    policy = _policy(config_dir)

    def gate(source_id: str, ref: str, pack: str, *, hosted: bool) -> None:
        row = srcs.get(source_id)
        terms = SourceTerms.of(row) if row is not None else None
        asset = AssetFacts("svg")
        reason = decide(terms, asset, "host" if hosted else "text", policy)
        if reason is not None:
            out.append(Finding("licence", "error", ref, pack, f"source {source_id}: {reason}"))

    for c in b.chapters:
        if c.pack_id:
            gate(c.source, f"chapter {c.id}", c.pack_id, hosted=False)
    for q in b.questions:
        if q.pack_id:
            gate(q.source, f"item {q.id}", q.pack_id, hosted=False)
    for pid, s in b.sets:
        gate(s.source, f"set {s.id}", pid, hosted=False)
    for pid, card in b.cards:
        gate(card.source, f"card {card.id}", pid, hosted=False)
    for f in b.figures:
        if f.pack_id:
            gate(f.source, f"figure {f.id}", f.pack_id, hosted=True)
            if f.source != "naadi-original-v1" and not (f.attribution or "").strip():
                out.append(
                    Finding("licence", "error", f"figure {f.id}", f.pack_id, "a licensed image needs attribution")
                )


def _texts(b: SeedBundle) -> list[tuple[str, str | None, str]]:
    """(ref, pack or None for legacy, text) of every stored text the originality check compares."""
    out: list[tuple[str, str | None, str]] = []
    for c in b.chapters:
        for s in c.sections:
            out.append((f"section {s.id}", c.pack_id, s.html))
    for q in b.questions:
        out.append((f"item {q.id} stem", q.pack_id, q.stem_html))
        out.append((f"item {q.id} explanation", q.pack_id, q.explanation_html or ""))
    for pid, card in b.cards:
        if "generated" in card.tags:
            continue  # tools/generate_cards.py derives them from texts this check already compares
        out.append((f"card {card.id}", pid, f"{card.front_html} {card.back_html}"))
    for pid, qs in b.sets:
        if qs.intro_html:
            out.append((f"set {qs.id}", pid, qs.intro_html))
    return out


def _originality(b: SeedBundle, config_dir: Path, report: Report) -> None:
    out = report.findings
    texts = [(ref, pack, shingles(t)) for ref, pack, t in _texts(b)]
    # A hand-written card may restate its own section or item; that pair is not a duplicate.
    linked: dict[str, set[str]] = defaultdict(set)
    for _pid, card in b.cards:
        if card.section_id:
            linked[f"card {card.id}"].add(f"section {card.section_id}")
            linked[f"section {card.section_id}"].add(f"card {card.id}")
        if card.question_id:
            for part in ("stem", "explanation"):
                linked[f"card {card.id}"].add(f"item {card.question_id} {part}")
                linked[f"item {card.question_id} {part}"].add(f"card {card.id}")
    salt, prints = load_fingerprints(config_dir)
    report.originality_mode = "fingerprints" if prints else "fallback"
    if prints:
        for ref, pack, sh in texts:
            if pack is None or len(sh) < MIN_SHINGLES:
                continue
            hits = sum(1 for s in sh if fingerprint(s, salt) in prints)
            if hits / len(sh) > FINGERPRINT_THRESHOLD:
                out.append(
                    Finding("originality", "error", ref, pack, f"{hits}/{len(sh)} 8-grams match forbidden fingerprints")
                )
    # Internal near-duplicates (always on): a pack text against every other stored text.
    index: dict[str, set[int]] = defaultdict(set)
    for i, (_r, _p, sh) in enumerate(texts):
        for s in sh:
            index[s].add(i)
    for i, (ref, pack, sh) in enumerate(texts):
        if pack is None or len(sh) < MIN_SHINGLES:
            continue
        shared: Counter[int] = Counter()
        for s in sh:
            for j in index[s]:
                if j != i:
                    shared[j] += 1
        if not shared:
            continue
        allowed = linked.get(ref, set())
        best = [
            (n, j)
            for j, n in shared.items()
            if texts[j][0] not in allowed and texts[j][0].split(" ")[:2] != ref.split(" ")[:2]
        ]
        if not best:
            continue
        n, j = max(best)
        other = texts[j][0]
        if n / len(sh) > ORIGINALITY_THRESHOLD:
            out.append(Finding("originality", "error", ref, pack, f"shares {n}/{len(sh)} 8-grams with {other}"))


def review_sample(b: SeedBundle, share: float = 0.15, seed: int = 23) -> list[str]:
    """A stratified sample of pack item ids (by pack, Step, system and difficulty) for the manual review: at least
    one per stratum, `share` overall. Deterministic for a given bundle and seed."""
    strata: dict[tuple[str, str, str, int], list[str]] = defaultdict(list)
    for q in b.questions:
        if q.pack_id is None:
            continue
        system = next((t.split(".")[1] for t in b.question_map.get(q.id, []) if re.match(r"^usmle2026\.\d\d\.", t)), "")
        strata[(q.pack_id, q.exam, system, q.difficulty_level or 0)].append(q.id)
    rng = random.Random(seed)  # noqa: S311 - a reproducible review sample, not a secret
    picked: list[str] = []
    for key in sorted(strata):
        ids = sorted(strata[key])
        k = max(1, round(len(ids) * share))
        picked += sorted(rng.sample(ids, k))
    return picked


def validate(b: SeedBundle, config_dir: Path, *, checks: Iterable[str] | None = None) -> Report:
    wanted = set(checks or ("lint", "why_not", "key", "licence", "originality"))
    report = Report()
    if "lint" in wanted:
        _lint(b, config_dir, report.findings)
    if wanted & {"why_not", "key"}:
        _why_not_and_key(b, report.findings)
    if "licence" in wanted:
        _licence(b, config_dir, report.findings)
    if "originality" in wanted:
        _originality(b, config_dir, report)
    report.counts = dict(Counter(f"{f.check}.{f.severity}" for f in report.findings))
    return report
