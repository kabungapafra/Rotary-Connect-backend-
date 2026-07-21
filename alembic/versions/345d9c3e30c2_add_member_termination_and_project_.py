"""add member termination and project report fields

Revision ID: 345d9c3e30c2
Revises: f1f56fe78e79
Create Date: 2026-07-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '345d9c3e30c2'
down_revision: Union[str, Sequence[str], None] = 'f1f56fe78e79'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('members', sa.Column('terminated_at', sa.Date(), nullable=True))
    op.add_column('projects', sa.Column('area_of_focus', sa.String(length=80), nullable=True))
    # server_default so ALTER TABLE backfills existing rows instead of
    # failing on the new NOT NULL columns; the model's Python-side default
    # only applies to rows inserted after this migration.
    op.add_column(
        'projects',
        sa.Column('hours_volunteered', sa.Integer(), nullable=False, server_default='0'),
    )
    op.add_column(
        'projects',
        sa.Column('beneficiaries_reached', sa.Integer(), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('projects', 'beneficiaries_reached')
    op.drop_column('projects', 'hours_volunteered')
    op.drop_column('projects', 'area_of_focus')
    op.drop_column('members', 'terminated_at')
