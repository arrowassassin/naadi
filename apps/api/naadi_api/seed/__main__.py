"""`python -m naadi_api.seed ...` (same options as scripts/seed.py; used as a one-off container command)."""

import sys

from .cli import main

sys.exit(main())
