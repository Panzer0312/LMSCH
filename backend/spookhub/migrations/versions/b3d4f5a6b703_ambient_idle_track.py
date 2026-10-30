"""ambient sound and idle cue track

Revision ID: b3d4f5a6b703
Revises: b2c3e4f5a602
Create Date: 2026-10-30 14:00:00.000000

cue.track ('main' | 'idle'), profile.ambient (AmbientSpec JSON), profile.fade_ms (idle -> main
fade) and profile.idle_length_s (light profiles' idle loop). Existing cues become main cues.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3d4f5a6b703"
down_revision: str | Sequence[str] | None = "b2c3e4f5a602"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("cue") as b:
        b.add_column(sa.Column("track", sa.String(), nullable=False, server_default="main"))
    with op.batch_alter_table("profile") as b:
        b.add_column(sa.Column("ambient", sa.JSON(), nullable=True))
        b.add_column(sa.Column("fade_ms", sa.Integer(), nullable=False, server_default="400"))
        b.add_column(sa.Column("idle_length_s", sa.Float(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("profile") as b:
        b.drop_column("idle_length_s")
        b.drop_column("fade_ms")
        b.drop_column("ambient")
    with op.batch_alter_table("cue") as b:
        b.drop_column("track")
