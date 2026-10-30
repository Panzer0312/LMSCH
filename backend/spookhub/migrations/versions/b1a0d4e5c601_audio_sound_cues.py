"""audio media and sound cues

Revision ID: b1a0d4e5c601
Revises: a9c4e7d2b153
Create Date: 2026-10-30 12:30:00.000000

media.kind ('video' | 'audio'), cue.kind ('light' | 'sound') and nullable cue.sound JSON.
Existing rows become video media and light cues via the server defaults.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1a0d4e5c601"
down_revision: str | Sequence[str] | None = "a9c4e7d2b153"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("media") as b:
        b.add_column(sa.Column("kind", sa.String(), nullable=False, server_default="video"))
    with op.batch_alter_table("cue") as b:
        b.add_column(sa.Column("kind", sa.String(), nullable=False, server_default="light"))
        b.add_column(sa.Column("sound", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("cue") as b:
        b.drop_column("sound")
        b.drop_column("kind")
    with op.batch_alter_table("media") as b:
        b.drop_column("kind")
