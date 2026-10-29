"""board segments

Revision ID: e8a4c2f61b37
Revises: d5e2b83c1a96
Create Date: 2026-10-29 18:00:00.000000

Cue targets are JSON; a missing "segs" key already means whole board, so existing cues need
no data change.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e8a4c2f61b37"
down_revision: str | Sequence[str] | None = "d5e2b83c1a96"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("board") as b:
        b.add_column(sa.Column("segments", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("board") as b:
        b.drop_column("segments")
