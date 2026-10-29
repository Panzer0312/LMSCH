"""cue templates, board fxdata

Revision ID: d5e2b83c1a96
Revises: c3d9a41f7e58
Create Date: 2026-10-29 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5e2b83c1a96"
down_revision: str | Sequence[str] | None = "c3d9a41f7e58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "cuetemplate",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("cues", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    with op.batch_alter_table("board") as b:
        b.add_column(sa.Column("fxdata", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("board") as b:
        b.drop_column("fxdata")
    op.drop_table("cuetemplate")
