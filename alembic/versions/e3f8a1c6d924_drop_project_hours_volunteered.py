"""drop projects.hours_volunteered

Revision ID: e3f8a1c6d924
Revises: b7c9d2e41a05
Create Date: 2026-07-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e3f8a1c6d924'
down_revision: Union[str, Sequence[str], None] = 'b7c9d2e41a05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Same table-exists guard as prior migrations: a from-scratch database
    (CI's disposable Postgres) gets `projects` from create_all() at app
    startup, straight off the current model — no hours_volunteered column
    to drop there. project_updates is a brand-new table, so create_all()
    handles it with no migration needed.
    """
    bind = op.get_bind()
    tables = sa.inspect(bind).get_table_names()
    if 'projects' in tables:
        columns = {c['name'] for c in sa.inspect(bind).get_columns('projects')}
        if 'hours_volunteered' in columns:
            op.drop_column('projects', 'hours_volunteered')


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    tables = sa.inspect(bind).get_table_names()
    if 'projects' in tables:
        op.add_column(
            'projects',
            sa.Column('hours_volunteered', sa.Integer(), nullable=False, server_default='0'),
        )
