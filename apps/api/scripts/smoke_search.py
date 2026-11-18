"""Post-deploy smoke test of local search: wall-clock p95 of GET /api/search on a running server.

    SMOKE_ACCESS_TOKEN=<a signed-in user's access token> \\
        uv run python scripts/smoke_search.py --base-url https://<ns>-naadi.hf.space [--runs 40] [--budget-ms 300]

Queries come from the server itself (the chapter titles of GET /api/courses), so nothing is hardcoded and every
query has something to find. Each runs once to warm caches, then `--runs` timed requests are made one at a
time. Exit 0 when p95 is under the budget (default 300 ms, the segment 07 acceptance) and every request
answered; exit 1 otherwise; exit 2 on bad input. The token is read from the environment and never printed.
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time

import httpx


def pct(values: list[float], p: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, max(0, round(p / 100 * len(s) + 0.5) - 1))]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--runs", type=int, default=40)
    ap.add_argument("--budget-ms", type=float, default=300.0)
    args = ap.parse_args(argv)
    token = os.environ.get("SMOKE_ACCESS_TOKEN", "").strip()
    if not token:
        print("SMOKE_ACCESS_TOKEN is not set", file=sys.stderr)
        return 2
    headers = {"authorization": f"Bearer {token}"}
    base = args.base_url.rstrip("/")
    with httpx.Client(base_url=base, headers=headers, timeout=15.0) as http:
        courses = http.get("/api/courses")
        if courses.status_code != 200:
            print(f"GET /api/courses answered {courses.status_code}", file=sys.stderr)
            return 2
        titles = [c["title"].split("·")[-1].strip() for c in courses.json()["chapters"]]
        if not titles:
            print("the server lists no chapters to query", file=sys.stderr)
            return 2
        queries = [titles[i % len(titles)] for i in range(args.runs)]
        for q in titles[:3]:
            http.get("/api/search", params={"q": q})  # warm-up
        wall: list[float] = []
        bad = 0
        for q in queries:
            t0 = time.perf_counter()
            res = http.get("/api/search", params={"q": q})
            wall.append((time.perf_counter() - t0) * 1000)
            bad += res.status_code != 200
    p95 = pct(wall, 95)
    print(
        f"search smoke on {base}: {len(wall)} requests, {bad} failed; "
        f"p50 {statistics.median(wall):.0f} ms, p95 {p95:.0f} ms, max {max(wall):.0f} ms "
        f"(budget {args.budget_ms:.0f} ms)"
    )
    return 0 if bad == 0 and p95 < args.budget_ms else 1


if __name__ == "__main__":
    sys.exit(main())
