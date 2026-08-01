"""add clubs sms_enabled

Revision ID: ef47ae53c1f6
Revises: b804c920202c
Create Date: 2026-08-01 20:00:04.206219

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ef47ae53c1f6'
down_revision: Union[str, Sequence[str], None] = 'b804c920202c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Same guard as the other add-column migrations: on a from-scratch
    database (CI's disposable Postgres) create_all() at app startup builds
    clubs straight off the model, sms_enabled included — nothing to ALTER
    there. On production, clubs already exists without this column.
    """
    bind = op.get_bind()
    tables = sa.inspect(bind).get_table_names()
    if 'clubs' in tables:
        columns = {c['name'] for c in sa.inspect(bind).get_columns('clubs')}
        if 'sms_enabled' not in columns:
            op.add_column(
                'clubs',
                sa.Column('sms_enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
            )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    tables = sa.inspect(bind).get_table_names()
    if 'clubs' in tables:
        columns = {c['name'] for c in sa.inspect(bind).get_columns('clubs')}
        if 'sms_enabled' in columns:
            op.drop_column('clubs', 'sms_enabled')
