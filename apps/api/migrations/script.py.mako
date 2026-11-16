"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

Every new table needs RLS in this same migration (tests/db/test_rls_coverage.py fails otherwise).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "raise NotImplementedError('write the upgrade')"}


def downgrade() -> None:
    ${downgrades if downgrades else "raise NotImplementedError('write a real downgrade')"}
