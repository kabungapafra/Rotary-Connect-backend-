"""add leadership transition fields

Revision ID: a1c3f0d6e912
Revises: 345d9c3e30c2
Create Date: 2026-07-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1c3f0d6e912'
down_revision: Union[str, Sequence[str], None] = '345d9c3e30c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Same from-scratch-database guard as the previous migration: on a real
    deployment `clubs`/`members` already exist, so ALTER them; on CI's
    disposable Postgres container, create_all() runs afterward at app
    startup and creates both tables already including these columns.
    """
    bind = op.get_bind()
    tables = sa.inspect(bind).get_table_names()
    if 'clubs' in tables:
        op.add_column(
            'clubs', sa.Column('last_leadership_transition_year', sa.Integer(), nullable=True)
        )
    if 'members' in tables:
        op.add_column(
            'members',
            sa.Column('needs_board_setup', sa.Boolean(), nullable=False, server_default='false'),
        )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    tables = sa.inspect(bind).get_table_names()
    if 'members' in tables:
        op.drop_column('members', 'needs_board_setup')
    if 'clubs' in tables:
        op.drop_column('clubs', 'last_leadership_transition_year')
