#!/usr/bin/env python3
"""Run the weekly YouTube mapper by hand: a thin CLI over `naadi_api.youtube.mapper` (the logic lives there).

The `youtube_map` graph runs the same code every Sunday 05:17 UTC. By hand, against the database in DATABASE_URL:

    uv --directory apps/api run python ../../tools/youtube_mapper.py            # refresh + discover
    uv --directory apps/api run python ../../tools/youtube_mapper.py --refresh-only

Environment (through `naadi_api.settings`, never printed): YOUTUBE_API_KEY, DATABASE_URL.
Prints counts and quota units only. It never downloads anything: videos are embedded by the official player.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from naadi_api.config_api.schemas import YoutubeRules
from naadi_api.config_api.service import AppConfigService
from naadi_api.db import Database
from naadi_api.embeddings.engine import get_engine
from naadi_api.settings import get_settings
from naadi_api.youtube import mapper
from naadi_api.youtube.client import GoogleYouTube, Quota


async def main(refresh_only: bool) -> int:
    s = get_settings()
    if s.youtube_api_key is None:
        print("YOUTUBE_API_KEY is not set", file=sys.stderr)
        return 2
    db = Database(s.database_url)
    await db.open()
    try:
        rules = YoutubeRules.model_validate(await AppConfigService(db, s.config_dir).get("youtube"))
        api = GoogleYouTube(s.youtube_api_key.get_secret_value(), Quota(rules.quota_units_per_run))
        channels = [] if refresh_only else mapper.load_channels(s.config_dir)
        rep = await mapper.run(db, api, get_engine(s.config_dir), rules, channels)
    finally:
        await db.close()
    for k, v in rep.out().model_dump().items():
        print(f"{k}: {v}")
    return 1 if rep.errors else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh-only", action="store_true", help="only re-fetch stored metadata (30-day policy)")
    raise SystemExit(asyncio.run(main(ap.parse_args().refresh_only)))
