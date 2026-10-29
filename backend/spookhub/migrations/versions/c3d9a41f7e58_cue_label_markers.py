"""cue label/color, profile markers

Revision ID: c3d9a41f7e58
Revises: b7c1e5a90d42
Create Date: 2026-10-29 13:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d9a41f7e58"
down_revision: str | Sequence[str] | None = "b7c1e5a90d42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("cue") as b:
        b.add_column(sa.Column("label", sa.String(), nullable=True))
        b.add_column(sa.Column("color", sa.String(), nullable=True))
    with op.batch_alter_table("profile") as b:
        b.add_column(sa.Column("markers", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("profile") as b:
        b.drop_column("markers")
    with op.batch_alter_table("cue") as b:
        b.drop_column("color")
        b.drop_column("label")
