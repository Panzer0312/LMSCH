"""board offset

Revision ID: ec261dde1abd
Revises: 0240fc1777b6
Create Date: 2026-10-29 01:19:59.511085

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'ec261dde1abd'
down_revision: Union[str, Sequence[str], None] = '0240fc1777b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "board",
        sa.Column("offset_ms", sa.Float(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("board") as batch:
        batch.drop_column("offset_ms")
