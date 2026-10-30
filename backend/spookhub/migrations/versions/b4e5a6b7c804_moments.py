"""moments

Revision ID: b4e5a6b7c804
Revises: b3d4f5a6b703
Create Date: 2026-10-30 15:00:00.000000

Moment table: buttons on /moments, each with a JSON list of variants.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4e5a6b7c804"
down_revision: str | Sequence[str] | None = "b3d4f5a6b703"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "moment",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("icon", sa.String(), nullable=False, server_default=""),
        sa.Column("color", sa.String(), nullable=False, server_default="#7c3aed"),
        sa.Column("sort", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cooldown_s", sa.Float(), nullable=False, server_default="0"),
        sa.Column("busy", sa.String(), nullable=False, server_default="ignore"),
        sa.Column("order", sa.String(), nullable=False, server_default="shuffle"),
        sa.Column("variants", sa.JSON(), nullable=False, server_default="[]"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("moment")
