"""eventlog

Revision ID: b7c1e5a90d42
Revises: ec261dde1abd
Create Date: 2026-10-29 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7c1e5a90d42"
down_revision: str | Sequence[str] | None = "ec261dde1abd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "eventlog",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ts", sa.Float(), nullable=False),
        sa.Column("level", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("message", sa.String(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=True),
    )
    op.create_index("ix_eventlog_ts", "eventlog", ["ts"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_eventlog_ts", table_name="eventlog")
    op.drop_table("eventlog")
