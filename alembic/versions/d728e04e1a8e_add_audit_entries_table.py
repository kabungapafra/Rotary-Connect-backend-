"""add audit_entries table

Revision ID: d728e04e1a8e
Revises: f61c2bcf7069
Create Date: 2026-08-16 11:04:00.000000

Records who took which administrative action. No FKs on club_id/actor_id
by design (see models.AuditEntry): the record has to outlive the club or
admin it describes, which an FK would either block or cascade away.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd728e04e1a8e'
down_revision: Union[str, Sequence[str], None] = 'f61c2bcf7069'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Guarded like every other migration here: CI's from-scratch database
    gets this table from create_all() at app startup instead, so creating
    it unconditionally would fail the build on a table that already exists
    by the time anything reads it.
    """
    bind = op.get_bind()
    if 'audit_entries' in set(sa.inspect(bind).get_table_names()):
        return
    op.create_table(
        'audit_entries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('actor_id', sa.Integer(), nullable=True),
        sa.Column('actor_email', sa.String(length=120), nullable=False),
        sa.Column('action', sa.String(length=60), nullable=False),
        sa.Column('club_id', sa.Integer(), nullable=True),
        sa.Column('club_name', sa.String(length=160), nullable=False),
        sa.Column('subject', sa.String(length=200), nullable=False),
        sa.Column('detail', sa.String(length=400), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_audit_entries_action'), 'audit_entries', ['action'], unique=False
    )
    op.create_index(
        op.f('ix_audit_entries_club_id'), 'audit_entries', ['club_id'], unique=False
    )
    op.create_index(
        op.f('ix_audit_entries_created_at'),
        'audit_entries',
        ['created_at'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    if 'audit_entries' not in set(sa.inspect(bind).get_table_names()):
        return
    op.drop_index(op.f('ix_audit_entries_created_at'), table_name='audit_entries')
    op.drop_index(op.f('ix_audit_entries_club_id'), table_name='audit_entries')
    op.drop_index(op.f('ix_audit_entries_action'), table_name='audit_entries')
    op.drop_table('audit_entries')
