"""add event_date to events

Revision ID: b804c920202c
Revises: e3f8a1c6d924
Create Date: 2026-07-26 19:11:23.182007

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b804c920202c'
down_revision: Union[str, Sequence[str], None] = 'e3f8a1c6d924'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Same table-exists guard as prior migrations: a from-scratch database
    (CI's disposable Postgres) gets `events` from create_all() at app
    startup, straight off the current model — event_date already there,
    nothing to add. Nullable with no default: existing rows (all recurring
    weekly events) simply get NULL, exactly matching what NULL means for
    this column.
    """
    bind = op.get_bind()
    tables = sa.inspect(bind).get_table_names()
    if 'events' in tables:
        columns = {c['name'] for c in sa.inspect(bind).get_columns('events')}
        if 'event_date' not in columns:
            op.add_column('events', sa.Column('event_date', sa.Date(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    tables = sa.inspect(bind).get_table_names()
    if 'events' in tables:
        columns = {c['name'] for c in sa.inspect(bind).get_columns('events')}
        if 'event_date' in columns:
            op.drop_column('events', 'event_date')
