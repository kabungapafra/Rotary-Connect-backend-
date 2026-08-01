"""add clubs per message type sms toggles

Revision ID: 6f7956bfa288
Revises: f08875670b5b
Create Date: 2026-08-01 21:30:18.791452

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6f7956bfa288'
down_revision: Union[str, Sequence[str], None] = 'f08875670b5b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_COLUMNS = [
    'sms_birthday_enabled',
    'sms_guest_thank_you_enabled',
    'sms_event_reminder_enabled',
    'sms_event_thank_you_enabled',
    'sms_new_member_enabled',
    'sms_new_president_enabled',
    'sms_admin_pin_reset_enabled',
    'sms_self_service_pin_reset_enabled',
]


def upgrade() -> None:
    """Upgrade schema.

    Same guard as sms_enabled before it (ef47ae53c1f6): on a from-scratch
    database (CI's disposable Postgres) clubs doesn't exist yet at this
    point in the chain — create_all() at app startup builds it straight
    off the model, these columns included. On production, clubs already
    exists without them.
    """
    bind = op.get_bind()
    tables = sa.inspect(bind).get_table_names()
    if 'clubs' in tables:
        existing = {c['name'] for c in sa.inspect(bind).get_columns('clubs')}
        for name in _COLUMNS:
            if name not in existing:
                op.add_column(
                    'clubs',
                    sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.true()),
                )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    tables = sa.inspect(bind).get_table_names()
    if 'clubs' in tables:
        existing = {c['name'] for c in sa.inspect(bind).get_columns('clubs')}
        for name in _COLUMNS:
            if name in existing:
                op.drop_column('clubs', name)
