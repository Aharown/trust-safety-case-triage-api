"""convert case queue column to enum

Revision ID: ea31d03cdabc
Revises: 1368496830e2
Create Date: 2026-09-25 17:58:41.082336

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ea31d03cdabc'
down_revision: Union[str, Sequence[str], None] = '1368496830e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    queue_enum = sa.Enum(
        "fraud", "prohibited_items", "community_guidelines",
        "general", "manual_triage", name="queue",
    )
    queue_enum.create(op.get_bind(), checkfirst=True)
    op.alter_column(
        "cases", "queue",
        type_=queue_enum,
        existing_type=sa.String(),
        existing_nullable=True,
        postgresql_using="queue::queue",
    )

def downgrade():
    op.alter_column(
        "cases", "queue",
        type_=sa.String(),
        existing_type=sa.Enum(name="queue"),
        postgresql_using="queue::text",
    )
    sa.Enum(name="queue").drop(op.get_bind(), checkfirst=True)
