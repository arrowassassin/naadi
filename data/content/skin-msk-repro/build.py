#!/usr/bin/env python3
"""Build the skin-msk-repro pack (S23-F) from its authoring sources, one file per outline leaf.

    python data/content/skin-msk-repro/build.py            # check every src file, then write the pack files
    python data/content/skin-msk-repro/build.py --check    # check only (no writes); exit 1 on any error
    python data/content/skin-msk-repro/build.py --check --only 06.01.01 06.01.02
    python data/content/skin-msk-repro/build.py --report   # per-area counts vs the plan, difficulty spread
    python data/content/skin-msk-repro/build.py --resolve  # look up StatPearls NBK ids (NCBI E-utilities) into src/_links.json

Source file: src/<SS.CC.AA>.json, e.g. src/06.01.01.json (see src/README.md for the shape). The build derives
every id from the area code and the row's `n`, so ids stay stable as long as `n` does: never renumber, retire.

Writes chapters.json, questions.json, cards.json and figures.json next to this file. Afterwards run
`pnpm --filter @naadi/shared seed:convert` (section bodies) and tools/validate_content.py. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "src"
ROOT = HERE.parents[2]
PREFIX = "s23f"
AUTHOR = "seg23-f"
LINKS = SRC / "_links.json"
PLAN = SRC / "_plan.json"

SYSTEM_TITLES = {
    "06": "Skin and subcutaneous tissue",
    "07": "Musculoskeletal system",
    "12": "Pregnancy, childbirth and the puerperium",
    "13": "Female and transgender reproductive system and breast",
    "14": "Male and transgender reproductive system",
}
EXAMS = ("step1", "step2ck")
EXAM_LABEL = {"step1": "Step 1", "step2ck": "Step 2 CK"}
CHAPTER_ORDER = {"step1": 600, "step2ck": 700}

SECTION_TAGS = {
    "p", "h2", "h3", "h4", "b", "strong", "i", "em", "sub", "sup", "br", "ul", "ol", "li",
    "table", "thead", "tbody", "tr", "th", "td", "div", "span",
}  # fmt: skip
ITEM_TAGS = {
    "p", "br", "b", "strong", "i", "em", "u", "sub", "sup", "small", "code",
    "ul", "ol", "li", "table", "thead", "tbody", "tr", "th", "td", "span", "div",
}  # fmt: skip
CALLOUTS = {"trap", "pearl", "bridge"}
DISCIPLINES = {
    "pathology", "physiology", "nutrition", "anatomy_embryology", "microbiology", "pharmacology",
    "behavioral_sciences", "biochemistry", "histology_cell_biology", "immunology", "genetics",
}  # fmt: skip
CLINICAL = {"medicine", "pediatrics", "obgyn", "psychiatry", "surgery"}
TASKS = {
    "medical_knowledge", "pc_history_pe", "pc_diagnosis", "pc_lab_diagnostic", "pc_prognosis",
    "pc_health_maintenance", "pc_pharmacotherapy", "pc_clinical_interventions", "pc_mixed_management",
    "communication", "professionalism", "systems_based_practice", "pbli",
}  # fmt: skip
# Link-only reference rows that exist in config/sources.yaml (checked again at build time).
REFERENCE_ROWS = {"statpearls", "cdc-guidelines", "uspstf", "medlineplus-federal", "usmle-content-outline-2026",
                  "usmle-official-samples", "ahrq", "acog", "aap", "endocrine-society", "wpath", "acr-rheum",
                  "aad", "aaos", "nccn", "aua"}  # fmt: skip
BANNED_OPTION = re.compile(r"\b(all|none) of the above\b|\bboth [a-e] and [a-e]\b", re.I)
NEGATIVE_STEM = re.compile(r"\b(EXCEPT|NOT|LEAST)\b")
CODE = re.compile(r"^(06|07|12|13|14)\.\d\d\.\d\d$")


class _Tags(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data: str) -> None:
        self.text.append(data)


def html_problems(html: str, allowed: set[str], where: str) -> list[str]:
    p = _Tags()
    p.feed(html.replace("[[figure:", "[[fig:"))
    out = []
    for tag, attrs in p.tags:
        if tag not in allowed:
            out.append(f"{where}: <{tag}> is not allowed")
        if tag == "a" or "href" in attrs:
            out.append(f"{where}: links are not allowed in the body")
        if tag == "div" and (attrs.get("class") or "") not in CALLOUTS:
            out.append(f"{where}: <div> needs class trap, pearl or bridge")
        if "style" in attrs or any(k.startswith("on") for k in attrs):
            out.append(f"{where}: style/on* attributes are not allowed")
    return out


def plain(html: str) -> str:
    p = _Tags()
    p.feed(html)
    return re.sub(r"\s+", " ", "".join(p.text)).strip()


def area_id(code: str) -> str:
    return f"usmle2026.{code}"


def compact(code: str) -> str:
    return code.replace(".", "")


def load_links() -> dict[str, dict[str, str]]:
    return json.loads(LINKS.read_text()) if LINKS.exists() else {}


def resolve_reading(entry: dict, links: dict, where: str, problems: list[str]) -> dict | None:
    """`{"statpearls": "Psoriasis"}` -> a StatPearls link via the resolved cache; or a full {label,url,sourceId}."""
    if "statpearls" in entry:
        title = entry["statpearls"]
        hit = links.get(title)
        if not hit:
            problems.append(f"{where}: StatPearls title {title!r} not resolved (run build.py --resolve)")
            return None
        if hit.get("nbk") is None:
            problems.append(f"{where}: StatPearls title {title!r} has no chapter; pick another title")
            return None
        return {
            "label": f"StatPearls: {hit['title']}",
            "url": f"https://www.ncbi.nlm.nih.gov/books/{hit['nbk']}/",
            "sourceId": "statpearls",
        }
    if not {"label", "url", "sourceId"} <= set(entry):
        problems.append(f"{where}: furtherReading needs statpearls or label/url/sourceId")
        return None
    if entry["sourceId"] not in REFERENCE_ROWS:
        problems.append(f"{where}: sourceId {entry['sourceId']!r} is not an allowed reference row")
    if not str(entry["url"]).startswith("https://"):
        problems.append(f"{where}: url must be https")
    return {"label": entry["label"], "url": entry["url"], "sourceId": entry["sourceId"]}


def sources_rows() -> set[str]:
    text = (ROOT / "config" / "sources.yaml").read_text()
    return set(re.findall(r"^  - id: ([a-z0-9-]+)\s*$", text, re.M))


def build_area(code: str, doc: dict, links: dict, rows: set[str]) -> tuple[dict, list[str], list[str]]:
    """Return (rows by kind, errors, warnings) for one src file."""
    errs: list[str] = []
    warns: list[str] = []
    c6 = compact(code)
    aid = area_id(code)
    sysc = code[:2]
    if doc.get("area") != code:
        errs.append(f"{code}: 'area' must be {code!r}")
    out: dict[str, list] = {"sections": [], "items": [], "sets": [], "cards": [], "figures": []}

    def chapter(exam: str) -> str:
        return f"{PREFIX}-ch{sysc}-{'1' if exam == 'step1' else '2'}"

    def uniq(rows_: list, kind: str) -> None:
        seen = Counter(r.get("n") for r in rows_)
        for n, k in seen.items():
            if not isinstance(n, int) or n < 1:
                errs.append(f"{code}: {kind} n must be a positive integer, got {n!r}")
            elif k > 1:
                errs.append(f"{code}: duplicate {kind} n={n}")

    for kind in ("sections", "items", "sets", "cards", "figures"):
        uniq(doc.get(kind, []), kind)

    fig_ids = {f"{PREFIX}-fig{c6}-{f['n']}" for f in doc.get("figures", [])}
    fig_exam: dict[str, str] = {}
    for f in doc.get("figures", []):
        fid = f"{PREFIX}-fig{c6}-{f['n']}"
        exam = f.get("exam")
        if exam not in EXAMS:
            errs.append(f"{fid}: exam must be step1 or step2ck")
            continue
        fig_exam[fid] = exam
        file = HERE / "figures" / f"{fid}.svg"
        if not file.exists():
            errs.append(f"{fid}: missing figures/{fid}.svg")
        else:
            svg = file.read_text()
            if "<script" in svg or "href" in svg or "<image" in svg or "<foreignObject" in svg.lower():
                errs.append(f"{fid}: svg must not contain scripts, links, images or foreignObject")
            if "viewBox" not in svg and "viewbox" not in svg:
                errs.append(f"{fid}: svg needs a viewBox")
            if 'role="img"' not in svg or "aria-label" not in svg:
                warns.append(f"{fid}: svg should carry role=img and aria-label")
        if not (f.get("caption") or "").strip():
            errs.append(f"{fid}: caption required")
        src = f.get("source", "naadi-original-v1")
        if src not in rows:
            errs.append(f"{fid}: source {src!r} not in sources.yaml")
        if src != "naadi-original-v1" and not f.get("attribution"):
            errs.append(f"{fid}: licensed figures need attribution")
        out["figures"].append(
            {
                "id": fid,
                "chapterId": chapter(exam),
                "caption": f["caption"],
                "file": f"figures/{fid}.svg",
                "source": src,
                "attribution": f.get("attribution"),
            }
        )
    used_figs: set[str] = set()

    sec_ids: dict[int, str] = {}
    for s in doc.get("sections", []):
        exam = s.get("exam")
        sid = f"{PREFIX}-s{c6}-{s['n']}"
        sec_ids[s["n"]] = sid
        if exam not in EXAMS:
            errs.append(f"{sid}: exam must be step1 or step2ck")
            continue
        html = s.get("html", "")
        errs += html_problems(html, SECTION_TAGS, sid)
        figs = re.findall(r"\[\[figure:([a-z0-9_-]+)\]\]", html)
        for fid in figs:
            if fid not in fig_ids:
                errs.append(f"{sid}: figure {fid} is not defined in this area's figures")
            used_figs.add(fid)
        words = len(plain(re.sub(r"\[\[figure:[^\]]+\]\]", "", html)).split())
        if words < 350:
            warns.append(f"{sid}: only {words} words (aim for exam depth, 600-1500)")
        if not s.get("heading", "").strip():
            errs.append(f"{sid}: heading required")
        reading = []
        for i, e in enumerate(s.get("furtherReading", [])):
            r = resolve_reading(e, links, f"{sid} furtherReading[{i}]", errs)
            if r:
                if r["sourceId"] not in rows:
                    errs.append(f"{sid}: furtherReading source {r['sourceId']!r} is not in sources.yaml yet")
                else:
                    reading.append(r)
        if not reading:
            errs.append(f"{sid}: needs at least one further-reading link")
        extra = [area_id(a) for a in s.get("extraAreas", [])]
        out["sections"].append(
            {
                "_exam": exam,
                "_chapter": chapter(exam),
                "_order": (code, s["n"]),
                "id": sid,
                "heading": s["heading"],
                "html": html,
                "figureIds": figs,
                "areas": [aid, *extra],
                "furtherReading": reading,
            }
        )

    set_ids: dict[int, tuple[str, str, str]] = {}
    for st in doc.get("sets", []):
        sid = f"{PREFIX}-set{c6}-{st['n']}"
        if st.get("exam") not in EXAMS or st.get("kind") not in ("sequential", "abstract"):
            errs.append(f"{sid}: exam/kind invalid")
            continue
        set_ids[st["n"]] = (sid, st["exam"], st["kind"])
        row = {"id": sid, "exam": st["exam"], "kind": st["kind"], "title": st.get("title"), "source": "naadi-original-v1"}
        if st["kind"] == "abstract":
            if not (st.get("introHtml") or "").strip():
                errs.append(f"{sid}: abstract set needs introHtml")
            errs += html_problems(st.get("introHtml", ""), ITEM_TAGS, sid)
            row["introHtml"] = st["introHtml"]
        out["sets"].append(row)

    item_ids: dict[int, str] = {}
    set_members: dict[str, list[int]] = defaultdict(list)
    for q in doc.get("items", []):
        qid = f"{PREFIX}-q{c6}-{q['n']:02d}" if isinstance(q.get("n"), int) else f"{PREFIX}-q{c6}-xx"
        item_ids[q.get("n")] = qid
        exam = q.get("exam")
        if exam not in EXAMS:
            errs.append(f"{qid}: exam must be step1 or step2ck")
            continue
        opts = q.get("options", [])
        if not isinstance(opts, list) or len(opts) < 5 or len(opts) > 10:
            errs.append(f"{qid}: needs 5-10 options (list of html strings)")
            continue
        labels = "ABCDEFGHIJ"[: len(opts)]
        ans = q.get("answer")
        if ans not in labels:
            errs.append(f"{qid}: answer {ans!r} is not an option label")
            continue
        wn = q.get("whyNot", {})
        if set(wn) != set(labels) - {ans}:
            errs.append(f"{qid}: whyNot must cover exactly {sorted(set(labels) - {ans})}")
        if any(len(plain(str(v))) < 15 for v in wn.values()):
            warns.append(f"{qid}: a whyNot is very short")
        norm = [plain(o).lower() for o in opts]
        if len(set(norm)) != len(norm):
            errs.append(f"{qid}: duplicate options")
        for o in opts:
            if BANNED_OPTION.search(plain(o)):
                errs.append(f"{qid}: 'all/none of the above' style option")
        if NEGATIVE_STEM.search(plain(q.get("stemHtml", ""))):
            warns.append(f"{qid}: negative stem (EXCEPT/NOT/LEAST)")
        lens = [len(x) for x in norm]
        others = sorted(lens[i] for i, lab in enumerate(labels) if lab != ans)
        med = others[len(others) // 2]
        if med and lens[labels.index(ans)] > 2.2 * med and lens[labels.index(ans)] > 40:
            warns.append(f"{qid}: the key is much longer than the distractors (length cue)")
        for fld in ("stemHtml", "explanationHtml", "objective"):
            if not str(q.get(fld, "")).strip():
                errs.append(f"{qid}: {fld} required")
        errs += html_problems(q.get("stemHtml", ""), ITEM_TAGS, f"{qid} stem")
        errs += html_problems(q.get("explanationHtml", ""), ITEM_TAGS, f"{qid} explanation")
        for o in opts:
            errs += html_problems(o, ITEM_TAGS, f"{qid} option")
        if "Takeaway" not in q.get("explanationHtml", ""):
            warns.append(f"{qid}: explanation lacks a Takeaway line")
        d = q.get("difficulty")
        if d not in (1, 2, 3, 4, 5):
            errs.append(f"{qid}: difficulty 1-5")
        disc = q.get("disciplines", [])
        cs = q.get("clinicalSciences", [])
        tasks = q.get("tasks", [])
        for v in disc:
            if v not in DISCIPLINES:
                errs.append(f"{qid}: unknown discipline {v!r}")
        for v in cs:
            if v not in CLINICAL:
                errs.append(f"{qid}: unknown clinical science {v!r}")
        for v in tasks:
            if v not in TASKS:
                errs.append(f"{qid}: unknown task {v!r}")
        if exam == "step1" and not disc:
            errs.append(f"{qid}: step1 items need at least one discipline")
        if exam == "step2ck" and not cs:
            errs.append(f"{qid}: step2ck items need at least one clinical science")
        if not tasks:
            errs.append(f"{qid}: needs at least one task")
        refs = q.get("references", [])
        for r in refs:
            if r not in rows:
                errs.append(f"{qid}: reference {r!r} is not in sources.yaml yet")
        fmt = q.get("format", "single")
        row = {
            "id": qid,
            "exam": exam,
            "format": fmt,
            "chapterId": chapter(exam),
            "stemHtml": q["stemHtml"],
            "options": [{"label": lab, "html": h} for lab, h in zip(labels, opts, strict=True)],
            "answer": ans,
            "explanationHtml": q["explanationHtml"],
            "whyNot": {k: wn[k] for k in sorted(wn)},
            "objective": q["objective"],
            "areas": [aid, *[area_id(a) for a in q.get("extraAreas", [])]],
            "disciplines": disc,
            "clinicalSciences": cs,
            "tasks": tasks,
            "difficulty": d,
            "source": "naadi-original-v1",
            "references": [r for r in refs if r in rows],
            "figureIds": [],
            "tags": q.get("tags", []),
            "status": "published",
            "review": {"status": "self_reviewed", "humanExpertReviewed": False, "author": AUTHOR},
        }
        for fn in q.get("figures", []):
            fid = f"{PREFIX}-fig{c6}-{fn}"
            if fid not in fig_ids:
                errs.append(f"{qid}: figure {fn} not defined")
            row["figureIds"].append(fid)
            used_figs.add(fid)
        if fmt in ("sequential", "abstract"):
            sn = q.get("set")
            if sn not in set_ids:
                errs.append(f"{qid}: set {sn!r} not defined")
            else:
                sid, sexam, skind = set_ids[sn]
                if skind != fmt or sexam != exam:
                    errs.append(f"{qid}: format/exam must match set {sid}")
                row["setId"] = sid
                row["setOrder"] = q.get("setOrder")
                set_members[sid].append(q.get("setOrder"))
        elif fmt not in ("single", "chart"):
            errs.append(f"{qid}: format must be single, chart, sequential or abstract")
        if len(row["areas"]) > 3:
            errs.append(f"{qid}: at most 3 areas")
        out["items"].append(row)

    for sid, kind_exam_kind in ((v[0], v) for v in set_ids.values()):
        orders = sorted(o for o in set_members.get(sid, []) if isinstance(o, int))
        kind = kind_exam_kind[2]
        if orders != list(range(1, len(orders) + 1)):
            errs.append(f"{sid}: setOrder must be 1..n, got {orders}")
        if kind == "sequential" and not 2 <= len(orders) <= 3:
            errs.append(f"{sid}: sequential sets have 2-3 items")
        if kind == "abstract" and len(orders) < 2:
            errs.append(f"{sid}: abstract sets have 2+ items")

    for c in doc.get("cards", []):
        cid = f"{PREFIX}-c{c6}-{c['n']:02d}"
        sec = c.get("section")
        if sec not in sec_ids:
            errs.append(f"{cid}: section {sec!r} not defined")
        qn = c.get("question")
        if qn is not None and qn not in item_ids:
            errs.append(f"{cid}: question {qn!r} not defined")
        for fld in ("front", "back"):
            if not str(c.get(fld, "")).strip():
                errs.append(f"{cid}: {fld} required")
            errs += html_problems(c.get(fld, ""), ITEM_TAGS, f"{cid} {fld}")
        out["cards"].append(
            {
                "id": cid,
                "frontHtml": c.get("front", ""),
                "backHtml": c.get("back", ""),
                "sectionId": sec_ids.get(sec),
                "questionId": item_ids.get(qn) if qn is not None else None,
                "areas": [aid],
                "source": "naadi-original-v1",
                "tags": c.get("tags", []),
            }
        )
    for s in sec_ids:
        if not any(c.get("section") == s for c in doc.get("cards", [])):
            errs.append(f"{sec_ids[s]}: section has no flashcards")
    for fid in fig_ids - used_figs:
        errs.append(f"{fid}: figure is never placed in a section or item")
    for exam in EXAMS:
        if not any(s.get("exam") == exam for s in doc.get("sections", [])):
            errs.append(f"{code}: no {exam} section")
    return out, errs, warns


def load_src(only: list[str] | None) -> dict[str, dict]:
    docs = {}
    for f in sorted(SRC.glob("*.json")):
        if f.name.startswith("_"):
            continue
        code = f.stem
        if not CODE.match(code):
            print(f"ERROR {f.name}: file name must be the area code, e.g. 06.01.01.json", file=sys.stderr)
            continue
        if only and code not in only:
            continue
        try:
            docs[code] = json.loads(f.read_text())
        except json.JSONDecodeError as exc:
            docs[code] = {"_bad": str(exc)}
    return docs


def _esearch(term: str) -> list[dict]:
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
    q = urllib.parse.urlencode({"db": "books", "term": term, "retmode": "json", "retmax": 20})
    with urllib.request.urlopen(f"{base}esearch.fcgi?{q}", timeout=30) as r:
        ids = json.load(r)["esearchresult"]["idlist"]
    if not ids:
        return []
    q = urllib.parse.urlencode({"db": "books", "id": ",".join(ids), "retmode": "json"})
    with urllib.request.urlopen(f"{base}esummary.fcgi?{q}", timeout=30) as r:
        res = json.load(r)["result"]
    return [res[i] for i in res.get("uids", []) if res[i].get("book") == "statpearls" and res[i].get("chapteraccessionid")]


def resolve(docs: dict[str, dict]) -> int:
    """Resolve StatPearls titles to NBK ids. Safe to run from several agents at once (file lock, merge on write)."""
    import fcntl

    def wanted(links: dict) -> list[str]:
        return sorted(
            {e["statpearls"] for d in docs.values() for s in d.get("sections", []) for e in s.get("furtherReading", [])
             if "statpearls" in e} - set(links)
        )  # fmt: skip

    found: dict[str, dict] = {}
    for title in wanted(load_links()):
        cands = _esearch(f'"{title}"[title] AND statpearls[book]')
        if not cands:
            words = [w for w in re.findall(r"[A-Za-z0-9]+", title) if len(w) > 2]
            cands = _esearch(" AND ".join(f"{w}[title]" for w in words) + " AND statpearls[book]") if words else []
        exact = [c for c in cands if c["title"].strip().lower() == title.strip().lower()]
        pick = (exact or sorted(cands, key=lambda c: len(c["title"])))[:1]
        hit = {"title": pick[0]["title"], "nbk": pick[0]["chapteraccessionid"], "exact": bool(exact)} if pick else {
            "title": title, "nbk": None}
        found[title] = hit
        print(f"{title!r} -> {hit}")
        if pick and not exact:
            others = ", ".join(repr(c["title"]) for c in cands[:6])
            print(f"   (not exact; candidates: {others})")
    with open(SRC / "_links.lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        links = load_links()
        links.update(found)
        tmp = LINKS.with_suffix(".tmp")
        tmp.write_text(json.dumps(links, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
        tmp.replace(LINKS)
    for t, h in found.items():
        if not h.get("nbk"):
            print(f"UNRESOLVED {t!r}: pick a title that exists in StatPearls", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--resolve", action="store_true")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--quiet-warnings", action="store_true")
    a = ap.parse_args(argv)
    docs = load_src(a.only)
    if a.resolve:
        return resolve(docs)
    links = load_links()
    rows = sources_rows()
    built: dict[str, dict] = {}
    errors: list[str] = []
    warns: list[str] = []
    for code, doc in docs.items():
        if "_bad" in doc:
            errors.append(f"{code}: invalid JSON: {doc['_bad']}")
            continue
        out, e, w = build_area(code, doc, links, rows)
        built[code] = out
        errors += e
        warns += w
    # cross-file id uniqueness and an exact-duplicate stem check
    stems: dict[str, str] = {}
    for out in built.values():
        for q in out["items"]:
            k = plain(q["stemHtml"]).lower()[:220]
            if k in stems:
                errors.append(f"{q['id']}: stem duplicates {stems[k]}")
            stems[k] = q["id"]
    if not a.quiet_warnings:
        for w in warns:
            print(f"warn  {w}", file=sys.stderr)
    for e in errors:
        print(f"ERROR {e}", file=sys.stderr)
    if a.report or a.check:
        plan = json.loads(PLAN.read_text()) if PLAN.exists() else {}
        diff: Counter[int] = Counter()
        tot: Counter[str] = Counter()
        for code, out in sorted(built.items()):
            n = Counter(q["exam"] for q in out["items"])
            tot.update(n)
            diff.update(q["difficulty"] for q in out["items"])
            p = plan.get(code, {})
            cells = "  ".join(f"{EXAM_LABEL[e]} {n[e]}/{p.get(e, '?')}" for e in EXAMS)
            secs = Counter(s["_exam"] for s in out["sections"])
            print(
                f"{code}  {cells}  sections {secs['step1']}+{secs['step2ck']}  cards {len(out['cards'])}"
                f"  figures {len(out['figures'])}  sets {len(out['sets'])}"
            )
        total = sum(diff.values()) or 1
        spread = " ".join(f"d{k}={100 * diff[k] / total:.0f}%" for k in range(1, 6))
        print(f"total items step1={tot['step1']} step2ck={tot['step2ck']}  difficulty {spread}")
    if errors:
        print(f"{len(errors)} error(s)", file=sys.stderr)
        return 1
    if a.check or a.report or a.only:
        return 0

    chapters: dict[str, dict] = {}
    for code in sorted(built):
        sysc = code[:2]
        for exam in EXAMS:
            cid = f"{PREFIX}-ch{sysc}-{'1' if exam == 'step1' else '2'}"
            chapters.setdefault(
                cid,
                {
                    "id": cid,
                    "exam": exam,
                    "order": CHAPTER_ORDER[exam] + int(sysc),
                    "title": f"{SYSTEM_TITLES[sysc]} · {EXAM_LABEL[exam]}",
                    "meta": "USMLE-2026 outline system " + sysc,
                    "source": "naadi-original-v1",
                    "sections": [],
                },
            )
    secs = sorted((s for out in built.values() for s in out["sections"]), key=lambda s: s["_order"])
    for s in secs:
        chapters[s["_chapter"]]["sections"].append({k: v for k, v in s.items() if not k.startswith("_")})
    chapter_list = [c for c in sorted(chapters.values(), key=lambda c: (c["exam"], c["order"])) if c["sections"]]
    items = [q for code in sorted(built) for q in built[code]["items"]]
    sets = [s for code in sorted(built) for s in built[code]["sets"]]
    cards = [c for code in sorted(built) for c in built[code]["cards"]]
    figures = [f for code in sorted(built) for f in built[code]["figures"]]

    def dump(name: str, obj: object) -> None:
        (HERE / name).write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n")

    dump("chapters.json", chapter_list)
    dump("questions.json", {"sets": sets, "items": items})
    dump("cards.json", cards)
    dump("figures.json", figures)
    print(
        f"wrote {len(chapter_list)} chapters, {len(secs)} sections, {len(items)} items, {len(sets)} sets, "
        f"{len(cards)} cards, {len(figures)} figures"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
