"""media rotation

Revision ID: a9c4e7d2b153
Revises: f3b8d1c5a274
Create Date: 2026-10-29 22:00:00.000000

Nullable media.rotation (clockwise container rotation). Existing rows stay NULL; the hub
re-probes them at startup instead of guessing 0 here (migrations do not run ffprobe).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9c4e7d2b153"
down_revision: str | Sequence[str] | None = "f3b8d1c5a274"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("media") as b:
        b.add_column(sa.Column("rotation", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("media") as b:
        b.drop_column("rotation")
