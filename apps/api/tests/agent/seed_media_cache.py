"""Seed `search_cache` with the recorded search results the self-test's media block uses (config/models.yaml
search.probe), so the e2e renders the same Commons image, PMC figure and YouTube video every run without a
search call. The images themselves load from Wikimedia and NCBI's PMC bucket; the video plays in YouTube's
IFrame player.

    uv run python -m tests.agent.seed_media_cache --database-url postgresql://...  (local test database only)
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import psycopg

from naadi_api.llm_config import load_yaml_sections
from naadi_api.search import (
    latest_pmc_version,
    normalize_query,
    parse_commons,
    parse_pmc_figures,
    parse_youtube_oembed,
    youtube_id,
)

from ..db.rls import is_local

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "search"
CONFIG_DIR = Path(__file__).resolve().parents[4] / "config"


def rows() -> dict[str, list[dict[str, object]]]:
    probe = load_yaml_sections(CONFIG_DIR)["search"]["probe"]
    commons = parse_commons(json.loads((FIX / "commons.json").read_text()), 1)
    meta = json.loads((FIX / "pmc_oa.json").read_text())
    if meta["pmcid"] != probe["pmcid"]:
        raise SystemExit("search.probe.pmcid does not match the recorded PMC fixture")
    version = latest_pmc_version((FIX / "pmc_oa_list.xml").read_text(), meta["pmcid"])
    assert version is not None
    figs = parse_pmc_figures(meta, (FIX / "pmc_oa_figs.xml").read_text(), version, 1)
    vid = youtube_id(str(probe["video_url"]))
    assert vid is not None
    video = parse_youtube_oembed(json.loads((FIX / "youtube_oembed.json").read_text()), vid)
    return {
        f"commons_images:{normalize_query(str(probe['images_query']))}": [r.to_dict() for r in commons],
        f"pmc_figures:{normalize_query(str(probe['pmcid']))}": [r.to_dict() for r in figs],
        f"youtube_oembed:{normalize_query(vid)}": [video.to_dict()],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--database-url", required=True)
    url = ap.parse_args().database_url
    if not is_local(url):
        raise SystemExit("refusing to seed a non-local database")
    with psycopg.connect(url, autocommit=True) as conn:
        for key, value in rows().items():
            conn.execute(
                "insert into public.search_cache (query_norm, results, credits, created_at)"
                " values (%s, %s::jsonb, 0, now()) on conflict (query_norm) do update"
                " set results = excluded.results, created_at = excluded.created_at",
                (key, json.dumps(value)),
            )
    print(json.dumps({"seeded": sorted(rows())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
