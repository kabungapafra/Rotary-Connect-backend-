"""add guest_visits.member_club

Revision ID: b7c9d2e41a05
Revises: a1c3f0d6e912
Create Date: 2026-07-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7c9d2e41a05'
down_revision: Union[str, Sequence[str], None] = 'a1c3f0d6e912'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Same table-exists guard as the previous migrations: on a from-scratch
    database (CI's disposable Postgres) create_all() at app startup builds
    guest_visits straight off the model, member_club included — nothing to
    ALTER there.
    """
    bind = op.get_bind()
    tables = sa.inspect(bind).get_table_names()
    if 'guest_visits' in tables:
        # server_default so existing rows backfill to '' instead of NULL,
        # matching the model's non-nullable default.
        op.add_column(
            'guest_visits',
            sa.Column('member_club', sa.String(length=160), nullable=False, server_default=''),
        )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    tables = sa.inspect(bind).get_table_names()
    if 'guest_visits' in tables:
        op.drop_column('guest_visits', 'member_club')
