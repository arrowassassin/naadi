"""Command line for the local search index.

    python -m naadi_api.embeddings prefetch            # download the models into FASTEMBED_CACHE_PATH (build time)
    python -m naadi_api.embeddings reindex [--kinds sections,questions,pathway,notes,ink] [--owner UUID]
                                           [--refs ID,ID] [--threads N]

`reindex` is idempotent: unchanged chunks are skipped, removed content is deleted. It needs DATABASE_URL and
the cached models; it never prints text from the corpus.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from typing import cast

from ..db import Database
from ..logs import configure_logging
from ..settings import get_settings
from .engine import ONNX_THREADS, Engine, ModelsNotCached, spec_from_config
from .index import reconcile
from .sources import KINDS, Kind


def prefetch() -> int:
    settings = get_settings()
    engine = Engine(spec_from_config(settings.config_dir), allow_download=True)
    engine.warm()
    print("embedding models cached")
    return 0


async def reindex(kinds: list[Kind], owner: uuid.UUID | None, threads: int, refs: list[str] | None) -> int:
    settings = get_settings()
    db = Database(settings.database_url, max_size=2)
    if not db.configured:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    await db.open()
    try:
        engine = Engine(spec_from_config(settings.config_dir), threads=threads)
        try:
            report = await reconcile(db, engine, kinds=kinds, owner=owner, ref_ids=refs)
        except ModelsNotCached as exc:
            print(str(exc), file=sys.stderr)
            return 2
    finally:
        await db.close()
    print(json.dumps(report.to_dict(), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="naadi_api.embeddings")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prefetch", help="download the embedding and reranker models into the cache")
    r = sub.add_parser("reindex", help="bring public.embeddings in line with the content")
    r.add_argument("--kinds", default=",".join(KINDS), help=f"comma list of {', '.join(KINDS)}")
    r.add_argument("--owner", type=uuid.UUID, default=None, help="only this user's private rows")
    r.add_argument("--threads", type=int, default=ONNX_THREADS, help="ONNX threads (a one-off run can use every core)")
    r.add_argument("--refs", default=None, help="comma list of ref ids (section ids, question ids, ...): only these")
    args = p.parse_args(argv)
    configure_logging("WARNING")
    if args.cmd == "prefetch":
        return prefetch()
    kinds = [k.strip() for k in args.kinds.split(",") if k.strip()]
    bad = [k for k in kinds if k not in KINDS]
    if bad:
        p.error(f"unknown kinds: {', '.join(bad)}")
    refs = [x.strip() for x in args.refs.split(",") if x.strip()] if args.refs else None
    return asyncio.run(reindex(cast("list[Kind]", kinds), args.owner, args.threads, refs))


if __name__ == "__main__":
    sys.exit(main())
