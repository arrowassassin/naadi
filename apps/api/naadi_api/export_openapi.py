"""Write the OpenAPI 3.1 document (the contract source of truth) for `pnpm gen:contracts`.

Usage: uv run python -m naadi_api.export_openapi ../../packages/shared/openapi.json
The output is deterministic (sorted keys, no environment-dependent values) so CI can diff it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .db import Database
from .health.service import COMPONENTS, Check, HealthService
from .main import create_app
from .settings import Settings


async def _unused() -> object:
    raise RuntimeError("health checks do not run during OpenAPI export")


def build_openapi() -> dict[str, object]:
    settings = Settings(_env_file=None, app_env="local", app_version="export")
    service = HealthService(
        checks={n: Check(n, _unused, ttl_s=1) for n in COMPONENTS},  # type: ignore[arg-type]
        version="export",
        environment="local",
    )
    app = create_app(settings, health=service, db=Database(None))
    return app.openapi()


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m naadi_api.export_openapi <output.json>", file=sys.stderr)
        return 2
    out = Path(argv[1])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(build_openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
