"""add last_seen_at to members

Revision ID: a1b2c3d4e5f6
Revises: d728e04e1a8e
Create Date: 2026-08-18 20:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'd728e04e1a8e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add members.last_seen_at, used to show who is currently online.

    Inspector-guarded like the other add-column migrations here: on a
    from-scratch database create_all() already builds members with this
    column, so there would be nothing to add and an unguarded ALTER would
    fail. On production the table predates the column, which is the case
    this migration actually exists for. Nullable, because every existing
    member has never been seen under this scheme yet.
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'members' not in inspector.get_table_names():
        return
    columns = {c['name'] for c in inspector.get_columns('members')}
    if 'last_seen_at' not in columns:
        op.add_column(
            'members',
            sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'members' not in inspector.get_table_names():
        return
    columns = {c['name'] for c in inspector.get_columns('members')}
    if 'last_seen_at' in columns:
        op.drop_column('members', 'last_seen_at')
