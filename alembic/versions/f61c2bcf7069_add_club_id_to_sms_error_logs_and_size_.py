"""add club_id to sms/error logs and size_bytes to stored objects

Revision ID: f61c2bcf7069
Revises: 6f7956bfa288
Create Date: 2026-08-16 10:18:11.325333

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f61c2bcf7069'
down_revision: Union[str, Sequence[str], None] = '6f7956bfa288'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    All four columns are nullable with no backfill in SQL: sms_logs and
    error_logs rows written before this never recorded which club they
    belonged to, so they cannot be attributed retroactively and per-club
    counts start from here. size_bytes is filled in separately by
    storage.backfill_object_sizes() on startup, which has to ask R2 for
    each object's length — not something SQL can do.

    Foreign keys are named explicitly; autogenerate emits None, which
    makes the downgrade's drop_constraint(None, ...) fail.
    """
    op.add_column('club_documents', sa.Column('size_bytes', sa.Integer(), nullable=True))
    op.add_column('error_logs', sa.Column('club_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_error_logs_club_id'), 'error_logs', ['club_id'], unique=False)
    op.create_foreign_key(
        'error_logs_club_id_fkey', 'error_logs', 'clubs', ['club_id'], ['id']
    )
    op.add_column('gallery_photos', sa.Column('size_bytes', sa.Integer(), nullable=True))
    op.add_column('sms_logs', sa.Column('club_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_sms_logs_club_id'), 'sms_logs', ['club_id'], unique=False)
    op.create_foreign_key(
        'sms_logs_club_id_fkey', 'sms_logs', 'clubs', ['club_id'], ['id']
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('sms_logs_club_id_fkey', 'sms_logs', type_='foreignkey')
    op.drop_index(op.f('ix_sms_logs_club_id'), table_name='sms_logs')
    op.drop_column('sms_logs', 'club_id')
    op.drop_column('gallery_photos', 'size_bytes')
    op.drop_constraint('error_logs_club_id_fkey', 'error_logs', type_='foreignkey')
    op.drop_index(op.f('ix_error_logs_club_id'), table_name='error_logs')
    op.drop_column('error_logs', 'club_id')
    op.drop_column('club_documents', 'size_bytes')
