"""Fail if CI artifacts about to be uploaded contain a token, key or cookie.

    uv run python -m tests.auth.scan_artifacts test-results/screens/01 test-results/screens/03 ...

Looks for JWT-shaped strings (Supabase access/anon/service keys, the CI auth container's minted keys),
Supabase session storage, HF/OpenRouter/Tavily key prefixes, and the literal values of the secret env
vars present in this job. Prints only file paths and the kind of finding, never the match itself.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

PATTERNS: dict[str, re.Pattern[bytes]] = {
    "jwt": re.compile(rb"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    "supabase-session": re.compile(rb"refresh_token\W{1,4}[A-Za-z0-9_-]{8,}"),
    "api-key": re.compile(rb"\b(?:hf_|sk-or-v1-|tvly-|sbp_|nvapi-)[A-Za-z0-9_-]{12,}"),
}
SECRET_ENV = (
    "SUPABASE_ANON_KEY",
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_JWT_SECRET",
    "HF_TOKEN",
    "OPENROUTER_API_KEY",
    "TAVILY_API_KEY",
    "DATABASE_URL",
)


def scan(paths: list[Path]) -> list[str]:
    literals = [v.encode() for n in SECRET_ENV if (v := os.environ.get(n, "")) and len(v) >= 12]
    findings: list[str] = []
    for root in paths:
        files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
        for f in files:
            data = f.read_bytes()
            kinds = [k for k, rx in PATTERNS.items() if rx.search(data)]
            if any(lit in data for lit in literals):
                kinds.append("secret-env-value")
            if kinds:
                findings.append(f"{f}: {', '.join(kinds)}")
    return findings


def main(argv: list[str]) -> int:
    paths = [Path(a) for a in argv if Path(a).exists()]
    findings = scan(paths)
    for line in findings:
        print(f"::error::artifact would leak a credential: {line}")
    print(f"scanned {len(paths)} path(s): {'FAIL' if findings else 'clean'}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
