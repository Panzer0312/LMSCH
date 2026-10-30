"""scenes

Revision ID: b2c3e4f5a602
Revises: b1a0d4e5c601
Create Date: 2026-10-30 12:00:00.000000

Profile gets a nullable scene JSON (SceneSpec for type=scene). The profiletype enum is stored
as VARCHAR without a CHECK constraint, so the new 'scene' value needs no table rebuild.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2c3e4f5a602"
down_revision: str | Sequence[str] | None = "b1a0d4e5c601"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("profile") as b:
        b.add_column(sa.Column("scene", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    # Old profiletype enum has no 'scene'; loading such a row would fail validation.
    # Enum stores member names, which equal the lowercase values.
    op.execute(
        sa.text(
            "UPDATE setting SET value = json_set(value, '$.active_profile_id', NULL) "
            "WHERE key = 'runtime' AND json_extract(value, '$.active_profile_id') IN "
            "(SELECT id FROM profile WHERE type = 'scene')"
        )
    )
    # Cue.profile_id is an FK; drop dependents first so no orphans remain.
    op.execute(
        sa.text(
            "DELETE FROM cue WHERE profile_id IN (SELECT id FROM profile WHERE type = 'scene')"
        )
    )
    op.execute(sa.text("DELETE FROM profile WHERE type = 'scene'"))
    with op.batch_alter_table("profile") as b:
        b.drop_column("scene")
