"""add error_logs table

Revision ID: f1f56fe78e79
Revises: 8d6e0f968d3b
Create Date: 2026-07-15 15:26:36.438195

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1f56fe78e79'
down_revision: Union[str, Sequence[str], None] = '8d6e0f968d3b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Only the new table — autogenerate also picked up the same pre-existing
    # NOT NULL drift noted in the baseline migration; left alone here too,
    # for the same reason (not this migration's business to silently change).
    op.create_table('error_logs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('method', sa.String(length=10), nullable=False),
    sa.Column('path', sa.String(length=255), nullable=False),
    sa.Column('exception_type', sa.String(length=120), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('traceback', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_error_logs_created_at'), 'error_logs', ['created_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_error_logs_created_at'), table_name='error_logs')
    op.drop_table('error_logs')
