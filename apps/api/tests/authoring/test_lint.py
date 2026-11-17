"""Part B of config/authoring_rules.md: every rule has a pass and a fail fixture (Naadi-original items)."""

from __future__ import annotations

from typing import Any

import pytest

from naadi_api.authoring.lint import LintContext, SourceInfo, lint_question, normalize_item

from .fixtures import context, good_item, taxonomy_kinds, variant, with_option


def rules(item: dict[str, Any], ctx: LintContext | None = None, severity: str | None = None) -> set[str]:
    report = lint_question(item, ctx or context())
    return {f.rule for f in report.findings if severity is None or f.severity == severity}


def test_a_well_formed_item_has_no_errors_and_no_warnings() -> None:
    report = lint_question(good_item(), context())
    assert report.ok
    assert report.findings == ()


def test_findings_are_structured_and_json_ready() -> None:
    report = lint_question(variant(answer="Z"), context())
    d = report.as_dict()
    assert d["ok"] is False
    assert d["findings"][0] == {"rule": "S02", "severity": "error", "target": "answer", "params": {"answer": "Z"}}


# ── schema rules ──


def test_s01_exactly_five_unique_labelled_options() -> None:
    assert "S01" not in rules(good_item())
    four = variant(options=good_item()["options"][:4])
    assert "S01" in rules(four, severity="error")
    dup = good_item()
    dup["options"][4]["label"] = "D"
    assert "S01" in rules(dup)
    empty = with_option(good_item(), "C", "  ")
    assert "S01" in rules(empty)


def test_s02_the_key_is_one_of_the_options() -> None:
    assert "S02" not in rules(good_item())
    assert "S02" in rules(variant(answer="F"))
    assert "S02" in rules(variant(answer=""))


def test_s03_required_fields_and_a_why_not_for_every_distractor() -> None:
    assert "S03" not in rules(good_item())
    f = lint_question(variant(explanation_html=""), context()).findings
    assert [x.params["missing"] for x in f if x.rule == "S03"] == [["explanation_html"]]
    assert "S03" in rules(variant(educational_objective=""))
    assert "S03" in rules(variant(difficulty="impossible"))
    partial = variant(why_not_html="Why the others are wrong: B — no. C — no. D — no.")
    f = lint_question(partial, context()).findings
    assert [x.params["unmentioned"] for x in f if x.rule == "S03"] == [["E"]]


def test_s03_seed_items_without_an_objective_are_a_warning_not_an_error() -> None:
    seed = variant(educational_objective=None)
    assert "S03" in rules(seed, severity="error")
    assert rules(seed, context(objective_optional=True), severity="error") == set()
    assert "S03" in rules(seed, context(objective_optional=True), severity="warn")


def test_s04_taxonomy_ids_exist_and_cover_each_kind() -> None:
    assert "S04" not in rules(good_item())
    assert "S04" in rules(variant(taxonomy_ids=["nope.nothing"]))
    no_topic = variant(taxonomy_ids=good_item()["taxonomy_ids"][:3])
    f = [x for x in lint_question(no_topic, context()).findings if x.rule == "S04"]
    assert f[0].params["missing"] == ["topic"]
    step2 = variant(exam="step2ck")  # a Step 1 discipline is not a clinical science
    assert "S04" in rules(step2)
    assert "S04" not in rules(good_item(), LintContext())  # no taxonomy handed in: the rule is skipped


def test_s05_every_source_must_allow_ai_ingestion_under_a_grounding_licence() -> None:
    assert "S05" not in rules(good_item())
    assert "S05" not in rules(variant(excerpt_source_ids=["naadi-original-v1", "cdc"]))
    for bad in ("youtube", "statpearls", "missing-source"):
        assert "S05" in rules(variant(excerpt_source_ids=[bad])), bad
    ctx = context(sources={"x": SourceInfo(True, "link-only")})
    assert "S05" in rules(variant(source_id="x"), ctx)
    assert "S05" in rules(variant(source_id=None), context())


# ── wording rules ──


@pytest.mark.parametrize("text", ["All of the above", "none of the above", "Both A and B"])
def test_l01_banned_option_text(text: str) -> None:
    assert "L01" in rules(with_option(good_item(), "E", text), severity="error")
    assert "L01" not in rules(good_item())


def test_l02_negative_lead_ins() -> None:
    for lead in (
        "Which of the following is NOT a cause?",
        "All of these occur EXCEPT which?",
        "What is the least likely cause?",
    ):
        item = variant(
            stem_html=good_item()["stem_html"].replace(
                "Which of the following is the most likely causal organism?", lead
            )
        )
        assert "L02" in rules(item, severity="error"), lead
    assert "L02" not in rules(good_item())


def test_l03_absolute_and_vague_frequency_words_are_a_warning() -> None:
    item = with_option(good_item(), "B", "Klebsiella pneumoniae, which is usually mucoid")
    f = lint_question(item, context()).findings
    assert [(x.rule, x.severity, x.params["words"]) for x in f if x.rule == "L03"] == [("L03", "warn", ["usually"])]
    assert "L03" not in rules(good_item())


def test_l04_the_lead_in_is_one_closed_question() -> None:
    assert "L04" not in rules(good_item())
    no_q = variant(stem_html=good_item()["stem_html"].replace("causal organism?", "causal organism."))
    assert "L04" in rules(no_q, severity="error")
    two = variant(stem_html=good_item()["stem_html"].replace("for 2 days.", "for 2 days? "))
    assert "L04" in rules(two, severity="error")
    tf = variant(
        stem_html=good_item()["stem_html"].replace(
            "Which of the following is the most likely causal organism?", "Which of the following is true?"
        )
    )
    assert "L04" in rules(tf, severity="error")


# ── testwiseness cues ──


def test_l05_the_key_is_not_much_longer_than_the_distractors() -> None:
    long_key = with_option(
        good_item(), "A", "Escherichia coli, a lactose-fermenting gram-negative rod of the gut flora"
    )
    assert "L05" in rules(long_key, severity="warn")
    assert "L05" not in rules(good_item())


def test_l06_a_stem_word_repeated_only_in_the_key_is_a_cue() -> None:
    item = variant(
        stem_html=good_item()["stem_html"].replace("a gram-negative rod", "a gram-negative rod resembling Escherichia")
    )
    assert "L06" in rules(item, severity="warn")
    assert "L06" not in rules(good_item())


def test_l07_the_key_sharing_the_most_words_with_the_others_is_convergence() -> None:
    opts = [
        ("A", "Hepatic steatosis with iron overload"),
        ("B", "Hepatic fibrosis with copper overload"),
        ("C", "Hepatic steatosis with fat overload"),
        ("D", "Hepatic iron overload"),
        ("E", "Splenic infarction"),
    ]
    item = good_item()
    item["options"] = [{"label": a, "html": b} for a, b in opts]
    item["answer"] = "A"
    item["why_not_html"] = "B — x. C — x. D — x. E — x."
    assert "L07" in rules(item, severity="warn")
    item["answer"] = "E"
    item["why_not_html"] = "A — x. B — x. C — x. D — x."
    assert "L07" not in rules(item)


def test_l08_option_order() -> None:
    shuffled = good_item()
    shuffled["options"][0]["html"], shuffled["options"][3]["html"] = "Staphylococcus x", "Aeromonas x"
    assert "L08" in rules(shuffled, severity="warn")
    assert "L08" not in rules(good_item())
    numeric = good_item()
    numeric["options"] = [
        {"label": lab, "html": f"{n} mg"} for lab, n in zip("ABCDE", (5, 10, 20, 40, 80), strict=True)
    ]
    assert "L08" not in rules(numeric)
    numeric["options"][1]["html"] = "100 mg"
    assert "L08" in rules(numeric)


def test_l09_numeric_options_share_a_unit_a_format_and_do_not_overlap() -> None:
    def numeric(texts: list[str]) -> dict[str, Any]:
        item = good_item()
        item["options"] = [{"label": lab, "html": t} for lab, t in zip("ABCDE", texts, strict=True)]
        return item

    assert "L09" not in rules(numeric(["5 mg", "10 mg", "20 mg", "40 mg", "80 mg"]))
    assert "L09" in rules(numeric(["5 mg", "10 mg", "20 g", "40 mg", "80 mg"]), severity="warn")
    assert "L09" in rules(numeric(["1-5 mg", "6-10 mg", "11-15 mg", "20 mg", "30 mg"]), severity="warn")
    assert "L09" in rules(numeric(["1-5 mg", "4-10 mg", "11-15 mg", "16-20 mg", "21-30 mg"]), severity="warn")


def test_l10_options_are_homogeneous_and_doubles_are_all_or_none() -> None:
    mixed = good_item()
    mixed["options"][1]["html"] = "Serum ferritin level"
    mixed["options"][2]["html"] = "Amoxicillin"
    assert "L10" in rules(mixed, severity="warn")
    doubled = with_option(good_item(), "B", "Klebsiella and Proteus")
    assert "L10" in rules(doubled, severity="warn")
    assert "L10" not in rules(good_item())


def test_l11_grammatical_cues() -> None:
    item = variant(stem_html="<p>" + "word " * 45 + "</p><p>The organism is most likely an</p>")
    assert "L11" in rules(item, severity="warn")  # "Escherichia" starts with a vowel, the others do not
    assert "L11" not in rules(good_item())


def test_l12_labs_before_vitals_or_exam_is_a_warning() -> None:
    wrong = variant(
        stem_html="<p>A 34-year-old woman comes to the clinic because of dysuria. "
        "Urinalysis shows positive nitrite and "
        "leukocyte esterase, and urine culture grows a lactose-fermenting rod. Her temperature is 37.0°C (98.6°F) and "
        "blood pressure is 118/74 mm Hg. Examination shows mild suprapubic tenderness and no other findings at all "
        "today.</p><p>Which of the following is the most likely causal organism?</p>"
    )
    assert "L12" in rules(wrong, severity="warn")
    assert "L12" not in rules(good_item())


def test_l13_lab_values_have_units_and_plausible_magnitudes() -> None:
    def with_lab(text: str) -> dict[str, Any]:
        return variant(stem_html=good_item()["stem_html"].replace("Urinalysis shows", f"{text} Urinalysis shows"))

    assert "L13" not in rules(with_lab("Serum sodium concentration is 128 mEq/L and glucose 90 mg/dL."))
    assert "L13" in rules(with_lab("Serum sodium is 128."), severity="error")  # no unit
    assert "L13" in rules(with_lab("Serum sodium is 12800 mEq/L."), severity="error")  # implausible
    assert "L13" in rules(with_lab("Serum magnesium is 1.8 mg/dL."), severity="warn")  # a unit the table lacks
    assert "L13" not in rules(with_lab("Serum magnesium is 1.8 mg/dL."), severity="error")
    assert "L13" not in rules(with_lab("Serum AST is 4,200 U/L."))  # thousands separator, enzyme range
    assert "L13" not in rules(with_lab("Arterial pH is 7.32."))  # unitless analyte
    assert "L13" not in rules(with_lab("24-hour urinary calcium was 350 mg."))  # other fluid
    assert "L13" not in rules(with_lab("Hemoglobin was 12 months ago normal."))


def test_l14_stem_length() -> None:
    assert "L14" in rules(
        variant(stem_html="<p>A woman has dysuria.</p><p>Which of the following is the most likely cause?</p>")
    )
    assert "L14" in rules(
        variant(stem_html="<p>" + "word " * 260 + "</p><p>Which of the following is the diagnosis?</p>")
    )
    assert "L14" not in rules(good_item())


def test_l15_hinged_items() -> None:
    hinged = variant(
        stem_html=good_item()["stem_html"].replace(
            "A 34-year-old woman", "The patient in the previous question, a woman"
        )
    )
    assert "L15" in rules(hinged, severity="warn")
    assert "L15" not in rules(good_item())


def test_l16_originality_against_the_bank() -> None:
    assert "L16" not in rules(good_item(), context(max_similarity=0.5))
    assert "L16" in rules(good_item(), context(max_similarity=0.87), severity="warn")
    assert "L16" in rules(good_item(), context(max_similarity=0.95), severity="error")
    assert "L16" not in rules(good_item())  # no similarity computed: skipped, not guessed


def test_l17_demographics_need_a_stated_relevance() -> None:
    item = variant(stem_html=good_item()["stem_html"].replace("A 34-year-old woman", "A 34-year-old Hispanic woman"))
    assert "L17" in rules(item, severity="warn")
    assert "L17" not in rules({**item, "demographic_relevance": "Disease prevalence differs by ancestry."})
    assert "L17" not in rules(variant(stem_html=good_item()["stem_html"].replace("urine", "white blood cell")))


def test_l18_difficulty_target_mismatch_is_info_only() -> None:
    r = lint_question(good_item(), context(solver_confidence=0.2, requested_difficulty="easy"))
    assert [(f.rule, f.severity) for f in r.findings] == [("L18", "info")]
    assert r.ok


def test_normalize_item_accepts_the_seed_shape() -> None:
    seed = {
        "stemHtml": good_item()["stem_html"],
        "options": good_item()["options"],
        "answer": "A",
        "explanationHtml": good_item()["explanation_html"],
        "whyNotHtml": good_item()["why_not_html"],
        "educationalObjective": "x",
        "difficulty": "easy",
        "taxonomyIds": good_item()["taxonomy_ids"],
        "source": "naadi-original-v1",
        "exam": "step1",
    }
    assert normalize_item(seed)["stem_html"] == good_item()["stem_html"]
    assert lint_question(seed, context()).ok


def test_the_seed_bank_has_no_error_other_than_known_negative_lead_ins() -> None:
    import json

    from .fixtures import ROOT

    questions = json.loads((ROOT / "data" / "questions.json").read_text())
    mapping = json.loads((ROOT / "data" / "question_taxonomy_map.json").read_text())
    ctx = context(objective_optional=True)
    bad: dict[str, set[str]] = {}
    for q in questions:
        q = {**q, "taxonomy_ids": mapping.get(q["id"], [])}
        errs = {f.rule for f in lint_question(q, ctx).errors}
        if errs:
            bad[q["id"]] = errs
    # The rules exist to catch defects: the shipped bank is expected to be nearly clean, and what is flagged is a
    # genuine "not" in a lead-in (L02) that the human spot-check will look at.
    assert all(v <= {"L02", "S03"} for v in bad.values()), bad
    assert len(bad) < 15


def test_taxonomy_fixture_is_the_real_taxonomy() -> None:
    assert taxonomy_kinds()["step1.sys.respiratory_renal"] == "system"
