# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "httpx==0.28.1",
#   "psycopg[binary]==3.3.9",
#   "huggingface_hub==2.4.0",
#   "supabase==2.34.1",
#   "openrouter==1.3.179",
#   "tavily-python==0.8.5",
#   "langchain-nvidia-ai-endpoints==1.5.0",
# ]
# ///
"""Verify Naadi's environment without ever printing a value.

Usage:  uv run scripts/check_env.py            (all checks; Tavily costs 1 credit)
        uv run scripts/check_env.py --no-tavily

Uses the official SDKs (supabase, openrouter, tavily-python, huggingface_hub, psycopg). Raw HTTPS is used
only where no SDK method exists: Supabase auth settings + JWKS endpoints and the Supabase Management API.

Reads os.environ first, then `.env` in the repo root as a local-dev fallback.
Prints PASS / FAIL / WARN / SKIP per key and per live check. Exit code 1 if any required check fails.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

ROOT = Path(__file__).resolve().parent.parent
TIMEOUT = httpx.Timeout(15.0)


@dataclass(frozen=True)
class Key:
    name: str
    secret: bool
    required: bool


KEYS: list[Key] = [
    Key("DATABASE_URL", True, True),
    Key("SUPABASE_URL", False, True),
    Key("SUPABASE_ANON_KEY", False, True),
    Key("SUPABASE_SERVICE_ROLE_KEY", True, True),
    Key("SUPABASE_JWT_SECRET", True, False),  # optional when the project uses JWKS (asymmetric keys)
    Key("SUPABASE_ACCESS_TOKEN", True, False),  # build-sandbox only: SQL via the HTTPS Management API
    Key("VITE_SUPABASE_URL", False, True),
    Key("VITE_SUPABASE_ANON_KEY", False, True),
    Key("OPENROUTER_API_KEY", True, True),
    Key("NVIDIA_API_KEY", True, False),  # NVIDIA API catalog (build.nvidia.com): higher-limit provider
    Key("OPENROUTER_DAILY_CAP", False, True),
    Key("OPENROUTER_APP_URL", False, True),
    Key("OPENROUTER_APP_TITLE", False, True),
    Key("TAVILY_API_KEY", True, True),
    Key("HF_TOKEN", True, True),
    Key("HF_SPACE_ID", False, True),
    Key("HF_BACKUP_DATASET", False, True),
    Key("ADMIN_EMAILS", False, True),
    Key("GOOGLE_OAUTH_CONFIGURED", False, True),
    Key("YOUTUBE_API_KEY", True, False),
    Key("R2_ACCOUNT_ID", False, False),
    Key("R2_ACCESS_KEY_ID", True, False),
    Key("R2_SECRET_ACCESS_KEY", True, False),
    Key("R2_BUCKET", False, False),
    Key("R2_PUBLIC_BASE_URL", False, False),
    Key("APP_ENV", False, True),
    Key("APP_BASE_URL", False, True),
    Key("LOG_LEVEL", False, True),
]


# ───────────── loading ─────────────
def load_dotenv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:]
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1]
        out[k.strip()] = v
    return out


DOTENV = load_dotenv(ROOT / ".env")


def get(name: str) -> str:
    v = os.environ.get(name, "")
    return v if v else DOTENV.get(name, "")


def source_of(name: str) -> str:
    return "env" if os.environ.get(name) else (".env" if DOTENV.get(name) else "-")


SECRET_VALUES: list[str] = []


def scrub(msg: str) -> str:
    """Remove every known secret (and the DB password) from a message before printing."""
    for s in SECRET_VALUES:
        if s and len(s) >= 4:
            msg = msg.replace(s, "***")
    msg = re.sub(r"(postgres(?:ql)?://[^:/@\s]+:)[^@\s]+@", r"\1***@", msg)
    msg = re.sub(r"(Bearer\s+)[A-Za-z0-9._\-]+", r"\1***", msg)
    return msg[:300]


# ───────────── reporting ─────────────
RESULTS: list[tuple[str, str, str, bool]] = []  # (status, label, detail, required)
COLORS = {"PASS": "\033[32m", "FAIL": "\033[31m", "WARN": "\033[33m", "SKIP": "\033[90m"}


def report(status: str, label: str, detail: str = "", required: bool = True) -> None:
    RESULTS.append((status, label, scrub(detail), required))
    tty = sys.stdout.isatty()
    c, r = (COLORS[status], "\033[0m") if tty else ("", "")
    print(f"  {c}{status:<4}{r}  {label:<34} {scrub(detail)}")


def b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def jwt_parts(token: str) -> tuple[dict, dict, bytes, bytes] | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        header = json.loads(b64url_decode(parts[0]))
        payload = json.loads(b64url_decode(parts[1]))
        return header, payload, f"{parts[0]}.{parts[1]}".encode(), b64url_decode(parts[2])
    except Exception:
        return None


# ───────────── static checks ─────────────
def check_presence() -> None:
    print("\nPresence (values never shown)")
    for k in KEYS:
        v = get(k.name)
        if k.secret and v:
            SECRET_VALUES.append(v)
        if v:
            report("PASS", k.name, f"set (from {source_of(k.name)})", k.required)
        elif k.required:
            report("FAIL", k.name, "missing", True)
        else:
            report("SKIP", k.name, "not set (optional)", False)
    # DB password is a secret even though it's embedded in a URL
    db = get("DATABASE_URL")
    if db:
        pw = urlparse(db).password
        if pw:
            SECRET_VALUES.append(pw)


def check_formats() -> None:
    print("\nFormat")
    db = get("DATABASE_URL")
    if db:
        u = urlparse(db)
        q = parse_qs(u.query)
        problems = []
        if u.scheme not in ("postgresql", "postgres"):
            problems.append("scheme must be postgresql://")
        if u.port != 5432:
            problems.append(f"port is {u.port}, expected 5432 (Session pooler)")
        if q.get("sslmode", [""])[0] != "require":
            problems.append("missing sslmode=require")
        if u.hostname and "pooler.supabase.com" not in u.hostname:
            problems.append("host is not a Supabase pooler host")
        report("WARN" if problems else "PASS", "DATABASE_URL format", "; ".join(problems) or f"host {u.hostname}")

    su, vsu = get("SUPABASE_URL"), get("VITE_SUPABASE_URL")
    if su:
        ok = re.fullmatch(r"https://[a-z0-9]{20}\.supabase\.co/?", su) is not None
        report("PASS" if ok else "WARN", "SUPABASE_URL format", "https://<ref>.supabase.co" if ok else "unexpected shape")
    if su and vsu:
        report("PASS" if su.rstrip("/") == vsu.rstrip("/") else "FAIL", "VITE_SUPABASE_URL == SUPABASE_URL",
               "" if su.rstrip("/") == vsu.rstrip("/") else "values differ")
    ak, vak = get("SUPABASE_ANON_KEY"), get("VITE_SUPABASE_ANON_KEY")
    if ak and vak:
        report("PASS" if ak == vak else "FAIL", "VITE_SUPABASE_ANON_KEY == ANON", "" if ak == vak else "values differ")

    cap = get("OPENROUTER_DAILY_CAP")
    if cap:
        report("PASS" if cap in ("50", "1000") else "FAIL", "OPENROUTER_DAILY_CAP", f"= {cap}" if cap in ("50", "1000") else "must be 50 or 1000")
    for name in ("OPENROUTER_APP_URL", "APP_BASE_URL", "R2_PUBLIC_BASE_URL"):
        v = get(name)
        if v:
            ok = v.startswith(("https://", "http://localhost", "http://127.0.0.1"))
            report("PASS" if ok else "WARN", f"{name} format", v if ok else "should be an https:// URL", name != "R2_PUBLIC_BASE_URL")
    for name in ("HF_SPACE_ID", "HF_BACKUP_DATASET"):
        v = get(name)
        if v:
            ok = re.fullmatch(r"[A-Za-z0-9][\w.\-]*/[\w.\-]+", v) is not None
            report("PASS" if ok else "FAIL", f"{name} format", v if ok else "must be <namespace>/<name>")
    emails = get("ADMIN_EMAILS")
    if emails:
        items = [e.strip() for e in emails.split(",") if e.strip()]
        bad = [e for e in items if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", e)]
        report("FAIL" if bad else "PASS", "ADMIN_EMAILS format", f"{len(bad)} malformed" if bad else f"{len(items)} address(es)")
    g = get("GOOGLE_OAUTH_CONFIGURED")
    if g:
        report("PASS" if g.lower() in ("true", "false") else "FAIL", "GOOGLE_OAUTH_CONFIGURED", f"= {g.lower()}" if g.lower() in ("true", "false") else "must be true|false")
    env = get("APP_ENV")
    if env:
        report("PASS" if env in ("local", "staging", "prod") else "FAIL", "APP_ENV", f"= {env}" if env in ("local", "staging", "prod") else "must be local|staging|prod")
    lvl = get("LOG_LEVEL")
    if lvl:
        ok = lvl.upper() in ("DEBUG", "INFO", "WARNING", "ERROR")
        report("PASS" if ok else "FAIL", "LOG_LEVEL", f"= {lvl.upper()}" if ok else "must be DEBUG|INFO|WARNING|ERROR")


# ───────────── live checks ─────────────
def check_postgres() -> None:
    db = get("DATABASE_URL")
    if not db:
        report("SKIP", "Postgres select 1", "DATABASE_URL missing")
        return
    import psycopg

    try:
        with psycopg.connect(db, connect_timeout=10) as conn, conn.cursor() as cur:
            cur.execute("select 1")
            one = cur.fetchone()[0]
            cur.execute("show server_version")
            ver = cur.fetchone()[0]
            cur.execute("select name from pg_available_extensions where name in ('vector','pg_trgm') order by 1")
            exts = [r[0] for r in cur.fetchall()]
        ok = one == 1
        missing = {"vector", "pg_trgm"} - set(exts)
        report("PASS" if ok else "FAIL", "Postgres select 1", f"server {ver.split()[0]}")
        report("PASS" if not missing else "FAIL", "Postgres extensions available",
               "vector, pg_trgm" if not missing else f"missing: {', '.join(sorted(missing))}")
    except Exception as e:  # noqa: BLE001
        host = urlparse(db).hostname
        sandbox = "failed to resolve host" in str(e) and get("SUPABASE_ACCESS_TOKEN")
        if sandbox:
            # Claude Code cloud sandbox: no DNS / raw TCP egress. The Mgmt API checks below verify the DB;
            # the real pooler connection is exercised in GitHub Actions and on the Space (/api/health).
            report("WARN", "Postgres select 1", f"no raw TCP/DNS in this sandbox (host {host}:5432); see Mgmt API checks", False)
        else:
            report("FAIL", "Postgres select 1", f"{type(e).__name__}: {e} (host {host}, TCP 5432)")


def check_supabase() -> None:
    su, anon, srv = get("SUPABASE_URL").rstrip("/"), get("SUPABASE_ANON_KEY"), get("SUPABASE_SERVICE_ROLE_KEY")
    if not (su and anon):
        report("SKIP", "Supabase auth settings", "SUPABASE_URL / SUPABASE_ANON_KEY missing")
        return
    # No SDK method exposes GET /auth/v1/settings, so this one call is raw HTTPS.
    try:
        r = httpx.get(f"{su}/auth/v1/settings", headers={"apikey": anon}, timeout=TIMEOUT)
        if r.status_code == 200:
            ext = r.json().get("external", {})
            report("PASS", "Supabase auth settings", "reachable with anon key")
            report("PASS" if ext.get("email") else "FAIL", "Supabase email (magic link)", "enabled" if ext.get("email") else "disabled in Auth → Providers")
            g_on = bool(ext.get("google"))
            flag = get("GOOGLE_OAUTH_CONFIGURED").lower() == "true"
            if g_on and flag:
                report("PASS", "Supabase Google provider", "enabled, flag true")
            elif g_on != flag:
                report("WARN", "Supabase Google provider", f"provider {'enabled' if g_on else 'disabled'} but GOOGLE_OAUTH_CONFIGURED={str(flag).lower()}")
            else:
                report("WARN", "Supabase Google provider", "not enabled yet (Auth → Providers → Google)")
        else:
            report("FAIL", "Supabase auth settings", f"HTTP {r.status_code}")
    except Exception as e:  # noqa: BLE001
        report("FAIL", "Supabase auth settings", f"{type(e).__name__}: {e}")

    # service role: list storage buckets via the SDK (requires the elevated key)
    if srv:
        from supabase import create_client

        try:
            n = len(create_client(su, srv).storage.list_buckets())
            report("PASS", "Supabase service role key", f"storage admin OK via SDK ({n} bucket(s))")
        except Exception as e:  # noqa: BLE001
            report("FAIL", "Supabase service role key", f"{type(e).__name__}: {e}")

    # JWT verification path: HS256 legacy secret, or JWKS
    secret = get("SUPABASE_JWT_SECRET")
    parts = jwt_parts(anon)
    if parts and parts[0].get("alg") == "HS256":
        if not secret:
            report("FAIL", "Supabase JWT secret", "anon key is HS256 but SUPABASE_JWT_SECRET is missing")
        else:
            sig = hmac.new(secret.encode(), parts[2], hashlib.sha256).digest()
            ok = hmac.compare_digest(sig, parts[3])
            report("PASS" if ok else "FAIL", "Supabase JWT secret", "verifies the anon key signature" if ok else "does NOT verify the anon key (wrong secret?)")
    else:
        try:
            r = httpx.get(f"{su}/auth/v1/.well-known/jwks.json", headers={"apikey": anon}, timeout=TIMEOUT)
            keys = r.json().get("keys", []) if r.status_code == 200 else []
            if keys:
                report("PASS", "Supabase JWKS", f"{len(keys)} signing key(s); JWT secret optional")
            elif secret:
                report("PASS", "Supabase JWT secret", "set (JWKS empty; HS256 user tokens)")
            else:
                report("FAIL", "Supabase JWT verification", "no JWKS keys and no SUPABASE_JWT_SECRET")
        except Exception as e:  # noqa: BLE001
            report("FAIL", "Supabase JWKS", f"{type(e).__name__}: {e}")


def check_supabase_mgmt() -> None:
    """select 1 over HTTPS via the Supabase Management API (works where raw TCP 5432 cannot)."""
    tok, su = get("SUPABASE_ACCESS_TOKEN"), get("SUPABASE_URL")
    if not tok:
        report("SKIP", "Supabase Mgmt API select 1", "SUPABASE_ACCESS_TOKEN not set (optional)", False)
        return
    m = re.match(r"https://([a-z0-9]{20})\.supabase\.co", su or "")
    if not m:
        report("FAIL", "Supabase Mgmt API select 1", "cannot derive project ref from SUPABASE_URL", False)
        return
    try:
        r = httpx.post(f"https://api.supabase.com/v1/projects/{m.group(1)}/database/query",
                       headers={"Authorization": f"Bearer {tok}"},
                       json={"query": "select 1 as one, current_setting('server_version') as v"}, timeout=TIMEOUT)
        if r.status_code in (200, 201):
            row = (r.json() or [{}])[0]
            ok = row.get("one") == 1
            report("PASS" if ok else "FAIL", "Supabase Mgmt API select 1", f"server {row.get('v')}", False)
        else:
            report("FAIL", "Supabase Mgmt API select 1", f"HTTP {r.status_code}", False)
    except Exception as e:  # noqa: BLE001
        report("FAIL", "Supabase Mgmt API select 1", f"{type(e).__name__}: {e}", False)

    # Compare DATABASE_URL's host/port/user with the project's real session pooler (password not compared).
    db = get("DATABASE_URL")
    if not db:
        return
    try:
        r = httpx.get(f"https://api.supabase.com/v1/projects/{m.group(1)}/config/database/pooler",
                      headers={"Authorization": f"Bearer {tok}"}, timeout=TIMEOUT)
        if r.status_code != 200:
            report("WARN", "DATABASE_URL matches pooler", f"pooler config HTTP {r.status_code}", False)
            return
        cfgs = r.json() if isinstance(r.json(), list) else [r.json()]
        want = urlparse(db)
        match = None
        for c in cfgs:
            cu = urlparse(re.sub(r"\[[^\]]*\]", "x", c.get("connection_string", "") or ""))
            if cu.hostname == want.hostname and cu.username == want.username:
                match = c
                break
        if match:
            report("PASS", "DATABASE_URL matches pooler", "host + user match the session pooler; password is verified in CI and on the Space", False)
        else:
            hosts = sorted({urlparse(re.sub(r"\[[^\]]*\]", "x", c.get('connection_string', '') or '')).hostname for c in cfgs} - {None})
            report("FAIL", "DATABASE_URL matches pooler", f"host/user differ from project pooler (expected host one of: {', '.join(hosts)})", True)
    except Exception as e:  # noqa: BLE001
        report("WARN", "DATABASE_URL matches pooler", f"{type(e).__name__}: {e}", False)


def check_nvidia() -> None:
    """NVIDIA API catalog via the official LangChain integration (ChatNVIDIA). Lists models; no generation."""
    key = get("NVIDIA_API_KEY")
    if not key:
        report("SKIP", "NVIDIA API models", "NVIDIA_API_KEY not set (optional)", False)
        return
    try:
        from langchain_nvidia_ai_endpoints import ChatNVIDIA

        models = ChatNVIDIA.get_available_models(api_key=key)
        n = len(models)
        tool_capable = sum(1 for m in models if getattr(m, "supports_tools", False))
        report("PASS" if n else "FAIL", "NVIDIA API models", f"{n} chat model(s), {tool_capable} with tool calling", False)
    except Exception as e:  # noqa: BLE001
        report("FAIL", "NVIDIA API models", f"{type(e).__name__}: {e}", False)


def check_openrouter() -> None:
    key = get("OPENROUTER_API_KEY")
    if not key:
        report("SKIP", "OpenRouter key metadata", "OPENROUTER_API_KEY missing")
        return
    from openrouter import OpenRouter

    try:
        with OpenRouter(api_key=key) as client:
            d = client.api_keys.get_current_key_metadata().data
        free_tier = d.is_free_tier
        report("PASS", "OpenRouter key metadata", f"valid (SDK api_keys.get_current_key_metadata); is_free_tier={free_tier}")
        cap = get("OPENROUTER_DAILY_CAP")
        if free_tier is True and cap == "1000":
            report("WARN", "OpenRouter daily cap", "cap=1000 but account has no credit purchase yet (real cap is 50/day)")
        elif free_tier is False and cap == "50":
            report("WARN", "OpenRouter daily cap", "credits purchased: you can raise OPENROUTER_DAILY_CAP to 1000")
        elif cap:
            report("PASS", "OpenRouter daily cap", f"{cap}/day consistent with account")
        fmdr = getattr(d, "free_model_daily_requests", None)
        report("PASS" if fmdr is not None else "WARN", "OpenRouter free quota visible",
               "free_model_daily_requests reported" if fmdr is not None else "free_model_daily_requests absent")
    except Exception as e:  # noqa: BLE001
        report("FAIL", "OpenRouter key metadata", f"{type(e).__name__}: {e}")


def check_tavily(enabled: bool) -> None:
    key = get("TAVILY_API_KEY")
    if not key:
        report("SKIP", "Tavily basic search", "TAVILY_API_KEY missing")
        return
    if not enabled:
        report("SKIP", "Tavily basic search", "--no-tavily", False)
        return
    from tavily import TavilyClient

    try:
        res = TavilyClient(api_key=key).search(
            "USMLE Step 1 content outline", search_depth="basic", max_results=1,
            include_answer=False, include_domains=["usmle.org"],
        )
        report("PASS", "Tavily basic search", f"{len(res.get('results', []))} result(s), 1 credit used (SDK)")
    except Exception as e:  # noqa: BLE001
        report("FAIL", "Tavily basic search", f"{type(e).__name__}: {e}")


def check_hf() -> None:
    token = get("HF_TOKEN")
    if not token:
        report("SKIP", "hf auth whoami", "HF_TOKEN missing")
        return
    env = {**os.environ, "HF_TOKEN": token}
    hf = shutil.which("hf")
    if hf:
        try:
            p = subprocess.run([hf, "auth", "whoami"], env=env, capture_output=True, text=True, timeout=30)
            report("PASS" if p.returncode == 0 else "FAIL", "hf auth whoami (CLI)", "ok" if p.returncode == 0 else f"exit {p.returncode}: {p.stderr.strip()}")
        except Exception as e:  # noqa: BLE001
            report("FAIL", "hf auth whoami (CLI)", f"{type(e).__name__}: {e}")
    else:
        report("SKIP", "hf auth whoami (CLI)", "hf CLI not installed; using the API below", False)

    from huggingface_hub import HfApi

    try:
        me = HfApi(token=token).whoami()
        name = me.get("name")
        role = (me.get("auth", {}).get("accessToken", {}) or {}).get("role")
        pro = bool(me.get("isPro"))
        report("PASS", "HF whoami (API)", f"user {name}; plan {'PRO' if pro else 'free'} → {'Docker' if pro else 'Gradio/ZeroGPU'} path")
        report("PASS" if role in ("write", "fineGrained") else "FAIL", "HF token role", f"{role}" + ("" if role in ("write", "fineGrained") else " (needs write)"))
        space = get("HF_SPACE_ID")
        if space and name and not space.startswith(f"{name}/"):
            orgs = {o.get("name") for o in me.get("orgs", [])}
            ns = space.split("/")[0]
            report("PASS" if ns in orgs else "WARN", "HF_SPACE_ID namespace", f"{ns} {'is an org you belong to' if ns in orgs else 'is not your user or one of your orgs'}")
    except Exception as e:  # noqa: BLE001
        report("FAIL", "HF whoami (API)", f"{type(e).__name__}: {e}")


def check_optional() -> None:
    yt = get("YOUTUBE_API_KEY")
    if yt:
        try:
            r = httpx.get("https://www.googleapis.com/youtube/v3/videos",
                          params={"part": "id", "id": "jNQXAC9IVRw", "key": yt}, timeout=TIMEOUT)
            report("PASS" if r.status_code == 200 else "FAIL", "YouTube Data API (1 unit)", f"HTTP {r.status_code}", False)
        except Exception as e:  # noqa: BLE001
            report("FAIL", "YouTube Data API (1 unit)", f"{type(e).__name__}: {e}", False)
    r2 = [get(k) for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")]
    if all(r2):
        report("SKIP", "Cloudflare R2", "all 4 set; live bucket check is added with the upload segment (M5)", False)
    elif any(r2):
        report("WARN", "Cloudflare R2", "partially set: need R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET", False)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-tavily", action="store_true", help="skip the Tavily search (saves 1 credit)")
    args = ap.parse_args()

    print("Naadi environment check (values are never printed)")
    check_presence()
    check_formats()
    print("\nLive checks")
    check_postgres()
    check_supabase()
    check_supabase_mgmt()
    check_openrouter()
    check_nvidia()
    check_tavily(not args.no_tavily)
    check_hf()
    check_optional()

    fails = [r for r in RESULTS if r[0] == "FAIL" and r[3]]
    warns = [r for r in RESULTS if r[0] == "WARN"]
    print(f"\nSummary: {sum(r[0] == 'PASS' for r in RESULTS)} pass, {len(fails)} required fail, {len(warns)} warn")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
