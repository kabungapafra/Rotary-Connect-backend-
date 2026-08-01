"""add club visit reports table

Revision ID: f08875670b5b
Revises: ef47ae53c1f6
Create Date: 2026-08-01 20:00:04.427294

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f08875670b5b'
down_revision: Union[str, Sequence[str], None] = 'ef47ae53c1f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Guarded: on a from-scratch database (CI, or a production deploy that
    already booted once since this table was added to the model)
    create_all() at app startup may already have built this table straight
    off the model — nothing to create again there.
    """
    bind = op.get_bind()
    tables = sa.inspect(bind).get_table_names()
    if 'club_visit_reports' not in tables:
        op.create_table(
            'club_visit_reports',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('club_id', sa.Integer(), nullable=False),
            sa.Column('member_id', sa.Integer(), nullable=False),
            sa.Column('visited_club_name', sa.String(length=160), nullable=False),
            sa.Column('meeting_date', sa.Date(), nullable=False),
            sa.Column('meeting_type', sa.String(length=40), nullable=False, server_default='Club meeting'),
            sa.Column('notes', sa.String(length=500), nullable=False, server_default=''),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.ForeignKeyConstraint(['club_id'], ['clubs.id']),
            sa.ForeignKeyConstraint(['member_id'], ['members.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(
            op.f('ix_club_visit_reports_club_id'), 'club_visit_reports', ['club_id'], unique=False
        )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    tables = sa.inspect(bind).get_table_names()
    if 'club_visit_reports' in tables:
        op.drop_index(op.f('ix_club_visit_reports_club_id'), table_name='club_visit_reports')
        op.drop_table('club_visit_reports')
