#!/usr/bin/env python3
"""Export the review book fragments into structured seed data for the Naadi app.

Outputs (in handoff/data/):
  chapters.json   - chapter metadata + sections (HTML) + figure refs + question ids
  questions.json  - every practice question as structured data
  figures/*.svg   - each teaching diagram as a standalone SVG file
  figures.json    - figure index (id, chapter, caption, file)
  study_plan_step1_60d.csv - copy of the 60-day plan
"""
import json, re, csv, shutil, pathlib, sys
from bs4 import BeautifulSoup

BOOK = pathlib.Path("/home/claude/book")
OUT = pathlib.Path("/home/claude/handoff/data")
(OUT / "figures").mkdir(parents=True, exist_ok=True)

ORDER = [("step1", ["ch01", "ch02", "ch03", "ch04", "ch05", "ch06", "ch07", "mock1"]),
         ("step2ck", ["ch08", "ch09", "ch10", "ch11", "ch12", "mock2"])]

def inner(tag):
    return "".join(str(c) for c in tag.contents).strip() if tag else ""

chapters, questions, figures = [], [], []
for exam, ids in ORDER:
    for order, cid in enumerate(ids, 1):
        soup = BeautifulSoup((BOOK / f"{cid}.html").read_text(), "html.parser")
        sec = soup.find("section", class_="chapter")
        title = sec.find("h1").get_text(" ", strip=True)
        meta = sec.find("p", class_="meta")
        kind = "mock_exam" if cid.startswith("mock") else "chapter"

        # figures -> standalone svg
        fig_ids = []
        for n, fig in enumerate(sec.find_all("figure", class_="dia"), 1):
            svg = fig.find("svg")
            cap = fig.find("figcaption")
            fid = f"{cid}-fig{n:02d}"
            if svg is not None:
                s = str(svg)
                if "xmlns=" not in s[:200]:
                    s = s.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)
                (OUT / "figures" / f"{fid}.svg").write_text(s)
            figures.append({"id": fid, "chapterId": cid, "caption": cap.get_text(" ", strip=True) if cap else "",
                            "file": f"figures/{fid}.svg"})
            fig.replace_with(soup.new_string(f"[[figure:{fid}]]"))
            fig_ids.append(fid)

        # questions
        q_ids = []
        for qn, q in enumerate(sec.find_all("div", class_="q"), 1):
            qid = q.get("id")
            stem = q.find("div", class_="stem")
            opts = [inner(li) for li in q.find("ol", class_="opts").find_all("li", recursive=False)]
            det = q.find("details")
            ans_p = det.find("p", class_="ans")
            m = re.search(r"Answer[^A-Z]*([A-J])\b", ans_p.get_text())
            key = m.group(1)
            why_not = det.find("p", class_="why-not")
            tag_p = det.find("p", class_="tag")
            expl_ps = [p for p in det.find_all("p") if not set(p.get("class") or []) & {"ans", "why-not", "tag"}]
            tag_txt = tag_p.get_text(" ", strip=True) if tag_p else ""
            diff = re.search(r"Difficulty:\s*(\w+)", tag_txt)
            # block for mock exams
            block = None
            if kind == "mock_exam":
                prev_h2 = q.find_previous("h2")
                bm = re.search(r"Block\s*(\d)", prev_h2.get_text()) if prev_h2 else None
                block = int(bm.group(1)) if bm else None
            stem_html = inner(stem)
            stem_html = re.sub(r"^<b>\s*\d+\.\s*</b>\s*", "", stem_html)
            questions.append({
                "id": qid, "chapterId": cid, "exam": exam, "number": qn, "block": block,
                "stemHtml": stem_html,
                "options": [{"label": chr(65 + i), "html": o} for i, o in enumerate(opts)],
                "answer": key,
                "answerHtml": inner(ans_p),
                "explanationHtml": "".join(str(p) for p in expl_ps),
                "whyNotHtml": inner(why_not),
                "tags": [t.strip() for t in tag_txt.split("·")] if tag_txt else [],
                "difficulty": diff.group(1).lower() if diff else None,
                "source": "naadi-original-v1",
                "review": {"status": "ai-authored, independent-ai-reviewed", "humanExpertReviewed": False},
            })
            q_ids.append(qid)
            q.decompose()

        # remove the practice-question heading; split notes by h2
        sections, cur = [], {"heading": "Overview", "html": ""}
        for el in list(sec.children):
            name = getattr(el, "name", None)
            if name == "h1" or (name == "p" and "meta" in (el.get("class") or [])):
                continue
            if name == "h2":
                if "qhead" in (el.get("class") or []):
                    continue
                if cur["html"].strip():
                    sections.append(cur)
                cur = {"heading": el.get_text(" ", strip=True), "html": ""}
                continue
            cur["html"] += str(el)
        if cur["html"].strip():
            sections.append(cur)
        for i, s in enumerate(sections, 1):
            s["id"] = f"{cid}-s{i:02d}"
            s["figureIds"] = re.findall(r"\[\[figure:([\w-]+)\]\]", s["html"])
            s["html"] = s["html"].strip()

        chapters.append({"id": cid, "exam": exam, "order": order, "kind": kind, "title": title,
                         "meta": meta.get_text(" ", strip=True) if meta else "",
                         "sections": sections if kind == "chapter" else [{"id": f"{cid}-s01", "heading": "Instructions", "html": sections[0]["html"] if sections else "", "figureIds": []}],
                         "figureIds": fig_ids, "questionIds": q_ids})

(OUT / "chapters.json").write_text(json.dumps(chapters, indent=1, ensure_ascii=False))
(OUT / "questions.json").write_text(json.dumps(questions, indent=1, ensure_ascii=False))
(OUT / "figures.json").write_text(json.dumps(figures, indent=1, ensure_ascii=False))
shutil.copy("/home/claude/plan/Step1_60_day_plan.csv", OUT / "study_plan_step1_60d.csv")

# sanity
assert len(questions) == len({q["id"] for q in questions})
assert all(q["answer"] and len(q["options"]) >= 4 for q in questions)
print(len(chapters), "chapters;", len(questions), "questions;", len(figures), "figures;",
      sum(len(c["sections"]) for c in chapters), "sections")
