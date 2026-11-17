"""Seed bundle loading: counts, cross-references, license gate, plan CSV, guides, stale conversions."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from naadi_api.seed.data import (
    SeedDataError,
    TaxonomyIn,
    license_gate,
    load_bundle,
    load_guides,
    parse_plan_csv,
    topological_taxonomy,
)

from .pack_totals import LEGACY_CHAPTERS, LEGACY_FIGURES, LEGACY_QUESTIONS, LEGACY_SECTIONS, pack_totals

REPO = Path(__file__).resolve().parents[4]
DATA, CONFIG = REPO / "data", REPO / "config"


@pytest.fixture(scope="module")
def bundle():  # type: ignore[no-untyped-def]
    return load_bundle(DATA, CONFIG)


def test_real_bundle_has_the_expected_counts(bundle) -> None:  # type: ignore[no-untyped-def]
    # legacy bundle pinned on its own rows; S23 pack rows counted from data/content/* (tests/seed/pack_totals.py)
    packs = pack_totals()
    assert len([c for c in bundle.chapters if c.pack_id is None]) == LEGACY_CHAPTERS
    assert len(bundle.chapters) == LEGACY_CHAPTERS + packs.chapters
    assert [c.id for c in bundle.chapters if c.kind == "mock_exam"] == ["mock1", "mock2"]
    assert len([s for c, _i, s in bundle.sections if c.pack_id is None]) == LEGACY_SECTIONS
    assert len(bundle.sections) == LEGACY_SECTIONS + packs.sections
    assert len([q for q in bundle.questions if q.pack_id is None]) == LEGACY_QUESTIONS
    assert len(bundle.questions) == LEGACY_QUESTIONS + packs.items
    assert len([f for f in bundle.figures if not f.file.startswith("content/")]) == LEGACY_FIGURES
    assert len(bundle.figures) == LEGACY_FIGURES + packs.figures
    assert len(bundle.taxonomy) == 688  # 217 legacy + 471 USMLE-2026
    assert len(bundle.question_map) == LEGACY_QUESTIONS + packs.items
    assert len(bundle.sets) == packs.sets
    assert len(bundle.cards) == packs.cards
    assert len(bundle.stages) == 16
    assert len(bundle.tasks) == 186
    assert len(bundle.plan.days) == 60
    assert len(bundle.guides) == 17
    assert {g.key for g in bundle.guides} == {"overview"} | {s.key for s in bundle.stages}
    assert len(bundle.sources.accepted) == 31  # catalog, internal, user-provided, pathway links, outline, 10 S23 rows
    assert bundle.sources.rejected == []
    assert sum(len(bundle.bodies.sections[s.id].figure_ids) for _c, _i, s in bundle.sections) == (
        LEGACY_FIGURES + packs.section_figures
    )


def test_mock_questions_keep_blocks_and_reviews_stay_unreviewed(bundle) -> None:  # type: ignore[no-untyped-def]
    mocks = [q for q in bundle.questions if q.chapter_id in {"mock1", "mock2"}]
    assert len(mocks) == 160
    assert {q.block for q in mocks} == {1, 2}
    assert not any(q.review.human_expert_reviewed for q in bundle.questions)


def _copy_bundle(tmp_path: Path) -> tuple[Path, Path]:
    data, config = tmp_path / "data", tmp_path / "config"
    shutil.copytree(DATA, data)
    shutil.copytree(CONFIG, config)
    return data, config


def test_stale_converted_body_is_refused(tmp_path: Path) -> None:
    data, config = _copy_bundle(tmp_path)
    chapters = json.loads((data / "chapters.json").read_text())
    chapters[0]["sections"][0]["html"] += "<p>edited after conversion</p>"
    (data / "chapters.json").write_text(json.dumps(chapters))
    with pytest.raises(SeedDataError) as e:
        load_bundle(data, config)
    assert any("ch01-s01" in p and "stale" in p and "seed:convert" in p for p in e.value.problems)


def test_unresolved_references_are_all_reported(tmp_path: Path) -> None:
    data, config = _copy_bundle(tmp_path)
    qmap = json.loads((data / "question_taxonomy_map.json").read_text())
    qmap["ch01-q01"] = ["step1.sys.nope"]
    qmap["ghost-q"] = ["step1"]
    (data / "question_taxonomy_map.json").write_text(json.dumps(qmap))
    tasks = json.loads((data / "pathway" / "pathway_tasks.json").read_text())
    tasks[0]["stage_key"] = "nowhere"
    (data / "pathway" / "pathway_tasks.json").write_text(json.dumps(tasks))
    (data / "figures" / "ch01-fig01.svg").unlink()
    with pytest.raises(SeedDataError) as e:
        load_bundle(data, config)
    text = "\n".join(e.value.problems)
    assert "ch01-q01: unknown taxonomy id step1.sys.nope" in text
    assert "unknown question ghost-q" in text
    assert "unknown stage nowhere" in text
    assert "missing file figures/ch01-fig01.svg" in text


def test_expert_review_flag_must_stay_false(tmp_path: Path) -> None:
    data, config = _copy_bundle(tmp_path)
    qs = json.loads((data / "questions.json").read_text())
    qs[0]["review"]["humanExpertReviewed"] = True
    (data / "questions.json").write_text(json.dumps(qs))
    with pytest.raises(SeedDataError, match="humanExpertReviewed"):
        load_bundle(data, config)


def test_license_gate_seeds_only_complete_rows() -> None:
    complete = {
        "id": "ok",
        "name": "OK",
        "license": "CC-BY-4.0",
        "allow_copy": True,
        "allow_modify": True,
        "allow_commercial": True,
        "allow_ai_ingest": False,
    }
    gate = license_gate(
        {
            "sources": [
                complete,
                {**complete, "id": "no-license", "license": " "},
                {k: v for k, v in complete.items() if k != "allow_ai_ingest"} | {"id": "no-ai-flag"},
                {**complete, "id": "stringly", "allow_copy": "yes"},
                {**complete},  # duplicate id
                "not a mapping",
            ]
        }
    )
    assert [s.id for s in gate.accepted] == ["ok"]
    reasons = dict(gate.rejected)
    assert "license" in reasons["no-license"]
    assert "allow_ai_ingest" in reasons["no-ai-flag"]
    assert "true/false" in reasons["stringly"]
    assert reasons["ok"] == "duplicate id"
    assert license_gate({}).accepted == []


def test_real_sources_keep_verification_fields() -> None:
    import yaml

    gate = license_gate(yaml.safe_load((CONFIG / "sources.yaml").read_text()))
    by_id = {s.id: s for s in gate.accepted}
    assert by_id["youtube"].verified is True
    assert by_id["youtube"].verified_url == "https://developers.google.com/youtube/terms/developer-policies"
    assert by_id["cdc-phil"].verified is False
    assert by_id["naadi-original-v1"].allow_ai_ingest is True


def test_plan_csv_parser(tmp_path: Path) -> None:
    plan = parse_plan_csv(
        DATA / "study_plan_step1_60d.csv",
        template_id="step1-60d",
        exam="step1",
        title="t",
        chapter_ids={f"ch{i:02d}" for i in range(1, 13)},
    )
    assert [d.day for d in plan.days] == list(range(1, 61))
    d2 = plan.days[1]
    assert d2.chapter_ids == ["ch03"]
    assert d2.weekday == "Tue"
    assert d2.target_hours == 8
    assert plan.days[0].chapter_ids == []  # "Intro"
    assert all(d.qbank != "—" for d in plan.days)  # em dash placeholders become null

    bad = tmp_path / "bad.csv"
    bad.write_text(
        "Day,Date,Weekday,Week,Phase,Book chapter,Topic / focus,Target hours\n"
        "1,2026-11-16,Mon,1,Build,Ch 99 Nothing,x,30\n3,not-a-date,Tue,1,Build,,y,8\n"
    )
    with pytest.raises(SeedDataError) as e:
        parse_plan_csv(bad, template_id="x", exam="step1", title="t", chapter_ids={"ch01"})
    text = "\n".join(e.value.problems)
    assert "ch99" in text
    assert "out of range" in text
    assert "not-a-date" in text


def test_guides_map_to_stages(tmp_path: Path) -> None:
    (tmp_path / "00-overview.md").write_text("# 00. Pathway overview\n\nbody")
    (tmp_path / "01-match-day.md").write_text("# 01. Match day\n\nbody")
    (tmp_path / "02-unknown.md").write_text("# 02. Unknown\n")
    (tmp_path / "03-notitle.md").write_text("no title")
    (tmp_path / "README.md").write_text("# readme")
    guides, problems = load_guides(tmp_path, {"match_day"})
    assert [(g.key, g.stage_key, g.title) for g in guides] == [
        ("overview", None, "Pathway overview"),
        ("match_day", "match_day", "Match day"),
    ]
    assert len(problems) == 2


def test_taxonomy_parents_come_first() -> None:
    def node(i: str, parent: str | None, sort: int) -> TaxonomyIn:
        return TaxonomyIn(
            id=i, kind="topic", parent_id=parent, label=i, exam=["step1"], weight_min=None, weight_max=None, sort=sort
        )

    ordered = topological_taxonomy([node("c", "b", 0), node("b", "a", 1), node("a", None, 2)])
    assert [n.id for n in ordered] == ["a", "b", "c"]
    with pytest.raises(SeedDataError, match="cycle"):
        topological_taxonomy([node("x", "y", 0), node("y", "x", 1)])
