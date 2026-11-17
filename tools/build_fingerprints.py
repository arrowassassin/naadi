#!/usr/bin/env python3
"""Build config/originality_fingerprints.txt from a local folder of forbidden-corpus snippets (S23-0).

    python tools/build_fingerprints.py <folder-of-.txt-files> [--salt HEX]

Reads every *.txt file under the folder (snippets the builder may lawfully read, e.g. publicly shown excerpts of
commercial review books and question banks), splits each into 8-word shingles exactly as
naadi_api/content/validate.py does, and writes ONLY salted SHA-256 digests. The snippets themselves are never
written, committed or sent anywhere: keep the folder outside the repository. Re-running with the same salt
merges new digests into the file. Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "config" / "originality_fingerprints.txt"
SHINGLE = 8
_TAG = re.compile(r"<[^>]+>")
_WORD = re.compile(r"[a-z0-9]+")


def shingles(text: str) -> set[str]:
    w = _WORD.findall(html.unescape(_TAG.sub(" ", text)).lower())
    return {" ".join(w[i : i + SHINGLE]) for i in range(len(w) - SHINGLE + 1)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("corpus", type=Path)
    p.add_argument("--salt", help="hex salt (default: the file's salt, else a new random one)")
    args = p.parse_args(argv)
    if ROOT in args.corpus.resolve().parents:
        print("keep the corpus outside the repository", file=sys.stderr)
        return 2
    header: list[str] = []
    digests: set[str] = set()
    salt = args.salt
    if OUT.is_file():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            if line.startswith("# salt:"):
                salt = salt or line.split(":", 1)[1].strip()
            elif line.startswith("#"):
                header.append(line)
            elif line.strip():
                digests.add(line.strip().lower())
    salt = salt or secrets.token_hex(8)
    files = sorted(args.corpus.rglob("*.txt"))
    for f in files:
        for s in shingles(f.read_text(encoding="utf-8", errors="ignore")):
            digests.add(hashlib.sha256(f"{salt}\x00{s}".encode()).hexdigest())
    OUT.write_text("\n".join([*header, f"# salt: {salt}", *sorted(digests)]) + "\n", encoding="utf-8")
    print(f"{len(files)} files -> {len(digests)} digests in {OUT.relative_to(ROOT)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
