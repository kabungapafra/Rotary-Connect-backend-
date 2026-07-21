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
    """Upgrade schema.

    On every real deployment (local dev, production) `members`/`projects`
    already exist — like the baseline migration, this repo's tables were
    historically created by create_all(), not Alembic — so ALTER them
    normally. The one place both are still missing at this point in the
    migration chain is a from-scratch database (CI's disposable Postgres
    container): there, create_all() runs afterward at app startup and
    creates both tables already including these columns, straight off the
    current models — nothing to ALTER, so skip rather than fail on a
    table that doesn't exist yet.
    """
    bind = op.get_bind()
    tables = sa.inspect(bind).get_table_names()
    if 'members' in tables:
        op.add_column('members', sa.Column('terminated_at', sa.Date(), nullable=True))
    if 'projects' in tables:
        op.add_column('projects', sa.Column('area_of_focus', sa.String(length=80), nullable=True))
        # server_default so ALTER TABLE backfills existing rows instead of
        # failing on the new NOT NULL columns; the model's Python-side
        # default only applies to rows inserted after this migration.
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
    bind = op.get_bind()
    tables = sa.inspect(bind).get_table_names()
    if 'projects' in tables:
        op.drop_column('projects', 'beneficiaries_reached')
        op.drop_column('projects', 'hours_volunteered')
        op.drop_column('projects', 'area_of_focus')
    if 'members' in tables:
        op.drop_column('members', 'terminated_at')
