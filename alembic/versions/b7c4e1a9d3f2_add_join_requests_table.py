"""add join requests table

Revision ID: b7c4e1a9d3f2
Revises: a1b2c3d4e5f6
Create Date: 2026-08-19 13:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7c4e1a9d3f2'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    No foreign keys on this table, so unlike add_club_visit_reports_table
    the only guard needed is the table's own absence: on a fresh database
    create_all() at startup builds it and this becomes a no-op; on
    production it doesn't exist yet and this is what creates it.
    """
    bind = op.get_bind()
    tables = sa.inspect(bind).get_table_names()
    if 'join_requests' not in tables:
        op.create_table(
            'join_requests',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('club_name', sa.String(length=160), nullable=False),
            sa.Column('club_type', sa.String(length=20), nullable=False, server_default='rotary'),
            sa.Column('district', sa.String(length=20), nullable=False, server_default=''),
            sa.Column('location', sa.String(length=160), nullable=False, server_default=''),
            sa.Column('charter_date', sa.Date(), nullable=True),
            sa.Column('members_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('logo', sa.Text(), nullable=True),
            sa.Column('contact_name', sa.String(length=120), nullable=False),
            sa.Column('contact_role', sa.String(length=80), nullable=False, server_default=''),
            sa.Column('phone', sa.String(length=20), nullable=False),
            sa.Column('email', sa.String(length=160), nullable=False, server_default=''),
            sa.Column('dob', sa.String(length=20), nullable=False, server_default=''),
            sa.Column('heard_about', sa.String(length=80), nullable=False, server_default=''),
            sa.Column('problems', sa.Text(), nullable=False, server_default=''),
            sa.Column('notes', sa.Text(), nullable=False, server_default=''),
            sa.Column('status', sa.String(length=20), nullable=False, server_default='new'),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(
            op.f('ix_join_requests_status'), 'join_requests', ['status'], unique=False
        )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    tables = sa.inspect(bind).get_table_names()
    if 'join_requests' in tables:
        op.drop_index(op.f('ix_join_requests_status'), table_name='join_requests')
        op.drop_table('join_requests')
