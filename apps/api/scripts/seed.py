"""Import Naadi's seed content into Postgres + Storage (idempotent; upsert by id).

    cd apps/api && uv run python scripts/seed.py --data ../../data --config ../../config [--dry-run]

See naadi_api/seed/cli.py for every option and exit code.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from naadi_api.seed.cli import main

if __name__ == "__main__":
    sys.exit(main())
