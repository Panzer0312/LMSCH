"""display profiles

Revision ID: f3b8d1c5a274
Revises: e8a4c2f61b37
Create Date: 2026-10-29 20:00:00.000000

New displayprofile table; media and profile get a nullable display_profile_id (plain id, the
API clears references when a display profile is deleted). The global default lives in the
runtime settings JSON and needs no schema change.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3b8d1c5a274"
down_revision: str | Sequence[str] | None = "e8a4c2f61b37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "displayprofile",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False, unique=True),
        sa.Column("geometry", sa.JSON(), nullable=False, server_default="{}"),
    )
    with op.batch_alter_table("media") as b:
        b.add_column(sa.Column("display_profile_id", sa.Integer(), nullable=True))
    with op.batch_alter_table("profile") as b:
        b.add_column(sa.Column("display_profile_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("profile") as b:
        b.drop_column("display_profile_id")
    with op.batch_alter_table("media") as b:
        b.drop_column("display_profile_id")
    op.drop_table("displayprofile")
