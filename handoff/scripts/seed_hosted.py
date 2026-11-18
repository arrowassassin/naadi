#!/usr/bin/env python3
"""Run the content seed on the LIVE Space as an admin and print the full report.

    cd apps/api
    E2E_ALLOW_HOSTED=1 uv run python ../../handoff/scripts/seed_hosted.py me     # who am I (is_admin?)
    E2E_ALLOW_HOSTED=1 uv run python ../../handoff/scripts/seed_hosted.py dry    # plan, writes nothing
    E2E_ALLOW_HOSTED=1 uv run python ../../handoff/scripts/seed_hosted.py real   # imports (idempotent upserts)
    uv run python -m tests.db.live_check --seed                                  # read-only verification

Why this exists: the sandbox cannot open a Postgres connection to the hosted project (no DNS/TCP), so the seed
runs INSIDE the Space (POST /api/admin/seed). That endpoint needs an admin session. We mint one for the first
ADMIN_EMAILS address with the Supabase admin SDK (a magic-link token hash, verified straight away) and use the
resulting short-lived access token. Nothing is printed except status and counts. Needs these env vars (names
only here; values live in the environment / docs/SECRETS.md): SUPABASE_URL, SUPABASE_ANON_KEY,
SUPABASE_SERVICE_ROLE_KEY, ADMIN_EMAILS. If the Space is private also HF_TOKEN + HF_SPACE_ID and
NAADI_SPACE_PRIVATE=1. Optional: NAADI_BASE_URL (default https://arrowassassin-naadi.hf.space).

Raw httpx is used only for the Space's /api/admin/seed job endpoints and the private-Space JWT exchange,
which no SDK covers.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time

import httpx
from supabase import create_client

from tests.auth.e2e_users import link as mint_link  # refuses non-local Supabase unless E2E_ALLOW_HOSTED=1


def main(mode: str) -> int:
    if mode not in {"me", "dry", "real"}:
        print(__doc__)
        return 2
    url = os.environ["SUPABASE_URL"]
    admin = next(x for x in re.split(r"[,;\s]+", os.environ["ADMIN_EMAILS"]) if x)
    created = mint_link(admin, None)  # {"user_id", "token_hash", "type"}
    anon = create_client(url, os.environ["SUPABASE_ANON_KEY"])
    session = anon.auth.verify_otp({"token_hash": created["token_hash"], "type": created["type"]}).session
    auth = {"Authorization": f"Bearer {session.access_token}"}

    base = os.environ.get("NAADI_BASE_URL", "https://arrowassassin-naadi.hf.space")
    client = httpx.Client(base_url=base, timeout=60, follow_redirects=True)
    if os.environ.get("NAADI_SPACE_PRIVATE") == "1":  # private Space: exchange HF_TOKEN for the Space's own cookie
        space = os.environ.get("HF_SPACE_ID", "arrowassassin/naadi")
        jwt = httpx.get(
            f"https://huggingface.co/api/spaces/{space}/jwt",
            headers={"Authorization": f"Bearer {os.environ['HF_TOKEN']}"},
        ).json()["token"]
        client.get("/", params={"__sign": jwt})

    me = client.get("/api/me", headers=auth)
    print("me:", me.status_code, "is_admin =", me.json().get("is_admin") if me.status_code == 200 else me.text[:200])
    if mode == "me":
        return 0
    run = client.post("/api/admin/seed", headers=auth, json={"dry_run": mode == "dry"})
    print("start:", run.status_code)
    if run.status_code >= 300:
        print(run.text[:400])
        return 1
    run_id = run.json().get("id") or run.json().get("run", {}).get("id")
    while True:
        time.sleep(5)
        job = client.get(f"/api/admin/seed/runs/{run_id}", headers=auth).json()
        if job.get("status") != "running":
            break
    report = job.get("report") or {}
    print("status:", job.get("status"), "| ok:", report.get("ok"))
    print("counts:", json.dumps(report.get("actual"), indent=1))
    tables = report.get("tables") or {}
    changed = {k: v for k, v in tables.items() if v.get("inserted", 0) + v.get("updated", 0) + v.get("deleted", 0)}
    print("changed tables:", json.dumps(changed, indent=1) if changed else "none (idempotent: nothing to change)")
    print("failed checks:", [c for c in report.get("checks", []) if not c.get("ok")] or "none")
    return 0 if job.get("status") == "succeeded" and report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
