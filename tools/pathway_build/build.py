"""Build Naadi Pathway seed content: pathway_stages.json, pathway_tasks.json, per-stage guides.
Run: python3 -I build.py  (from this directory: python3 build.py)"""
import json, re, sys, os
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from urls import U, LABELS
from stages import STAGES, LV
import tasks_a, tasks_b, tasks_c, tasks_d
from guides import G, OVERVIEW

OUT = "/home/claude/handoff/data/pathway"
ALL_TASKS = {**tasks_a.TASKS, **tasks_b.TASKS, **tasks_c.TASKS, **tasks_d.TASKS}

ANCHORS = OrderedDict([
    ("start", "the day you start the Pathway in Naadi"),
    ("oet_date", "your OET test date"),
    ("step1_date", "your Step 1 exam date"),
    ("step2ck_date", "your Step 2 CK exam date"),
    ("observership_start", "the start of your first observership"),
    ("eras_open", "the day MyERAS opens (early June)"),
    ("eras_submit", "the day applicants may submit to programs (early September)"),
    ("rol_deadline", "the NRMP Rank Order List certification deadline"),
    ("match_day", "NRMP Match Day"),
    ("arrival", "your US arrival date"),
    ("residency_start", "your residency (DS-2019) start date"),
    ("residency_end", "your residency end date"),
])
RULE_RE = re.compile(r"^(?:(?P<anchor>[a-z0-9_]+)(?P<off>[+-]\d+d)?|date:(?P<date>\d{4}-\d{2}-\d{2})|annual:(?P<md>\d{2}-\d{2}))$")
LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
URL2KEY = {v: k for k, v in U.items()}


def human_due(rule):
    m = RULE_RE.match(rule)
    if m["date"]:
        return f"by {m['date']}"
    if m["md"]:
        return f"every year by {m['md']} (MM-DD)"
    a, off = m["anchor"], m["off"]
    names = {"start": "Start", "oet_date": "OET date", "step1_date": "Step 1 date", "step2ck_date": "Step 2 CK date",
             "observership_start": "observership start", "eras_open": "MyERAS opens", "eras_submit": "ERAS submission opens",
             "rol_deadline": "ROL deadline", "match_day": "Match Day", "arrival": "arrival", "residency_start": "residency start",
             "residency_end": "residency end"}
    n = names[a]
    if not off:
        return "immediately" if a == "start" else f"on {n}"
    d = int(off[1:-1])
    if a == "start" and off[0] == "+":
        return f"within {d} days of starting"
    return f"{d} days {'before' if off[0] == '-' else 'after'} {n}"


def sentences(txt):
    t = LINK_RE.sub(r"\1", txt)
    t = re.sub(r"\b(e\.g|i\.e|vs|Dr|St|No|approx|Pub|Rep|Sen|U\.S|D\.|N\.D|Cal)\.", "X", t)
    t = re.sub(r"\d\.\d", "0", t)
    return [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9“(*$₹])", t.strip()) if s]


def build():
    stage_keys = [s[0] for s in STAGES]
    stages_json, tasks_json, problems = [], [], []
    for i, (key, slug, title, summary, links) in enumerate(STAGES, 1):
        stages_json.append(dict(key=key, sort=i * 10, title=title, summary_md=summary, official_links=links, last_verified=LV))
        if key not in ALL_TASKS:
            problems.append(f"no tasks for {key}")
            continue
        prefix, items = ALL_TASKS[key]
        for j, (t_title, detail, due, cost, url_key) in enumerate(items, 1):
            tid = f"{prefix}-{j:02d}"
            if not RULE_RE.match(due):
                problems.append(f"{tid}: bad due_rule {due}")
            else:
                m = RULE_RE.match(due)
                if m["anchor"] and m["anchor"] not in ANCHORS:
                    problems.append(f"{tid}: unknown anchor {m['anchor']}")
            if not LINK_RE.search(detail):
                problems.append(f"{tid}: no inline link")
            n = len(sentences(detail))
            if not 1 <= n <= 6:
                problems.append(f"{tid}: {n} sentences")
            tasks_json.append(OrderedDict(id=tid, stage_key=key, sort=j * 10, title=t_title, detail_md=detail,
                                          due_rule=due, cost_usd=cost, official_url=U[url_key], last_verified=LV))
    # validation
    ids = [t["id"] for t in tasks_json]
    assert len(ids) == len(set(ids)), "duplicate ids"
    for t in tasks_json:
        assert t["stage_key"] in stage_keys, t
    for url in set(m[1] for t in tasks_json for m in LINK_RE.findall(t["detail_md"])):
        if url not in URL2KEY:
            problems.append(f"unregistered url {url}")
    with open(f"{OUT}/pathway_stages.json", "w") as f:
        json.dump(stages_json, f, ensure_ascii=False, indent=2)
    with open(f"{OUT}/pathway_tasks.json", "w") as f:
        json.dump(tasks_json, f, ensure_ascii=False, indent=2)
    return stages_json, tasks_json, problems


def md_cell(s):
    return s.replace("|", "\\|").replace("\n", " ")


def sources_list(*texts):
    keys = []
    for t in texts:
        for _, url in LINK_RE.findall(t):
            k = URL2KEY.get(url)
            if k and k not in keys:
                keys.append(k)
    return "\n".join(f"- [{LABELS[k]}]({U[k]})" for k in keys)


def write_guides(stages_json, tasks_json):
    files = []
    for i, st in enumerate(stages_json, 1):
        key = st["key"]
        slug = [s[1] for s in STAGES if s[0] == key][0]
        tasks = [t for t in tasks_json if t["stage_key"] == key]
        g = G[key]
        rows = ["| # | Task | When | Cost (USD) | Link |", "|---|---|---|---|---|"]
        for t in tasks:
            cost = "—" if t["cost_usd"] is None else f"${t['cost_usd']:,}"
            rows.append(f"| `{t['id']}` | {md_cell(t['title'])} | {human_due(t['due_rule'])} (`{t['due_rule']}`) | {cost} | [official]({t['official_url']}) |")
        details = "\n\n".join(f"**{t['title']}** (`{t['id']}`) — {t['detail_md']}" for t in tasks)
        sections = "\n\n".join(f"## {h}\n\n{body.strip()}" for h, body in g["sections"])
        mistakes = "\n".join(f"- {m}" for m in g["mistakes"])
        links = "\n".join(f"- [{l['label']}]({l['url']})" for l in st["official_links"])
        all_text = st["summary_md"] + details + sections + mistakes + links
        md = f"""# {i:02d}. {st['title']}

*Stage key `{key}` · last verified {st['last_verified']} · re-verify quarterly and immediately on policy news.*

{st['summary_md']}

## Checklist

{chr(10).join(rows)}

"(verify)" marks facts the research could not confirm from a primary source. Cost is the fee Naadi could source; "$0" means no fee or an information-only task; "—" means variable or not sourced.

## Task details

{details}

{sections}

## Common mistakes

{mistakes}

## Official links

{links}

## Sources

{sources_list(all_text)}
"""
        fn = f"{i:02d}-{slug}.md"
        with open(f"{OUT}/{fn}", "w") as f:
            f.write(md)
        files.append(fn)
    ov = f"""# 00. Pathway overview: budgets, master timeline, policy watch

*Cross-cutting reference for all stages · last verified {LV}.*

The route from an Indian MBBS to US residency is a parallel race, not a staged journey: exams, ECFMG certification, US clinical exposure with letters, and visas must converge by the September ERAS submission. A slip in any one pushes the Match back a year.

## Master timeline (2028 Match target)

{OVERVIEW['timeline'].strip()}

## Budget to Match Day

{OVERVIEW['budget'].strip()}

## 2026 policy watch

{OVERVIEW['policy'].strip()}

## Best / typical / worst scenarios

{OVERVIEW['scenarios'].strip()}

## Sources

{sources_list(*OVERVIEW.values())}
"""
    with open(f"{OUT}/00-overview.md", "w") as f:
        f.write(ov)
    return ["00-overview.md"] + files


if __name__ == "__main__":
    s, t, problems = build()
    files = write_guides(s, t)
    print(f"stages={len(s)} tasks={len(t)} guides={len(files)}")
    from collections import Counter
    print(Counter(x['stage_key'] for x in t))
    print("PROBLEMS:", *problems, sep="\n  ") if problems else print("no problems")
