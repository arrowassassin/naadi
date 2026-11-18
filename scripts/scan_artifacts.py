# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Fail if the value of a secret environment variable appears in files about to be uploaded as CI artifacts.

    python scripts/scan_artifacts.py [--env HF_TOKEN] PATH [PATH ...]

Every regular file under each PATH is read as bytes and searched for the secret's value in the forms a
test tool can write it: raw, URL-encoded, and base64 at each of the three byte alignments (HAR bodies,
trace snapshots). ZIP archives (Playwright traces, nested zips) and gzip files are opened and their
members scanned the same way. Missing paths are fine (nothing to upload). The secret's value, or any part
of it, is never printed: only the file, the member and the encoding that matched.

Exit codes: 0 clean, 1 secret found or a file could not be read, 2 the variable is unset or too short.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import io
import os
import sys
import urllib.parse
import zipfile
from collections.abc import Iterator
from pathlib import Path

MIN_SECRET_LEN = 8
MAX_DEPTH = 4  # zip inside zip inside ...; Playwright nests at most one level


def needles(secret: str) -> dict[str, bytes]:
    """Byte strings that occur in any encoding of `secret` this scanner recognises."""
    raw = secret.encode()
    out = {"raw": raw}
    quoted = urllib.parse.quote(secret, safe="").encode()
    if quoted != raw:
        out["url-encoded"] = quoted
    for offset in range(3):
        enc = base64.b64encode(b"\0" * offset + raw)
        # Keep only the characters whose 6 bits all come from the secret: the ones that also cover the
        # preceding bytes or whatever follows the secret differ from stream to stream.
        start = -(-8 * offset // 6)
        end = (8 * (offset + len(raw))) // 6
        core = enc[start:end]
        if len(core) >= MIN_SECRET_LEN:
            out[f"base64+{offset}"] = core
            out[f"base64url+{offset}"] = core.replace(b"+", b"-").replace(b"/", b"_")
    return out


def _members(name: str, data: bytes, depth: int) -> Iterator[tuple[str, bytes]]:
    yield name, data
    if depth >= MAX_DEPTH:
        return
    if data[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    yield from _members(f"{name}!{info.filename}", zf.read(info), depth + 1)
        except (zipfile.BadZipFile, OSError, EOFError) as exc:
            raise ValueError(f"{name}: unreadable zip ({type(exc).__name__})") from None
    elif data[:2] == b"\x1f\x8b":
        try:
            yield from _members(f"{name}!gunzip", gzip.decompress(data), depth + 1)
        except (OSError, EOFError) as exc:
            raise ValueError(f"{name}: unreadable gzip ({type(exc).__name__})") from None


def _files(paths: list[Path]) -> Iterator[Path]:
    for p in paths:
        if p.is_file():
            yield p
        elif p.is_dir():
            yield from sorted(f for f in p.rglob("*") if f.is_file() and not f.is_symlink())


def scan(paths: list[Path], secret: str) -> tuple[int, list[str]]:
    """Returns (files scanned, findings). Findings name the place and the encoding, never the value."""
    forms = needles(secret)
    findings: list[str] = []
    count = 0
    for f in _files(paths):
        count += 1
        try:
            data = f.read_bytes()
            for member, blob in _members(str(f), data, 0):
                findings.extend(f"{member} ({kind})" for kind, needle in forms.items() if needle in blob)
        except (OSError, ValueError) as exc:
            # A file we cannot inspect must not be uploaded unchecked.
            findings.append(f"{f}: could not be scanned ({exc if isinstance(exc, ValueError) else type(exc).__name__})")
    return count, findings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--env", default="HF_TOKEN", help="name of the environment variable holding the secret")
    ap.add_argument("paths", nargs="+", type=Path)
    args = ap.parse_args(argv)
    secret = os.environ.get(args.env, "")
    if len(secret) < MIN_SECRET_LEN:
        print(f"scan_artifacts: {args.env} is not set (or too short to scan for); refusing to pass", file=sys.stderr)
        return 2
    count, findings = scan(args.paths, secret)
    if findings:
        for line in findings:
            print(f"::error::the value of {args.env} appears in {line}", file=sys.stderr)
        print(f"scan_artifacts: {len(findings)} finding(s) in {count} file(s); do not upload", file=sys.stderr)
        return 1
    print(f"scan_artifacts: {count} file(s) scanned, no trace of {args.env}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
