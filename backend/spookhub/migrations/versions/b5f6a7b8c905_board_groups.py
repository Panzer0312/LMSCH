"""board groups

Revision ID: b5f6a7b8c905
Revises: b4e5a6b7c804
Create Date: 2026-10-31 12:00:00.000000

board_group (rooms) and board_group_member. Selections and cue targets carry group ids in
their existing JSON columns, so no other table changes.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b5f6a7b8c905"
down_revision: str | Sequence[str] | None = "b4e5a6b7c804"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "board_group",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("color", sa.String(), nullable=False, server_default="#7c3aed"),
        sa.Column("sort", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "board_group_member",
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("mac", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["board_group.id"]),
        sa.PrimaryKeyConstraint("group_id", "mac"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("board_group_member")
    op.drop_table("board_group")
