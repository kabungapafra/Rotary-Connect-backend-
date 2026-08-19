"""add site project photo column

Revision ID: d9e6a3c7b5f4
Revises: c8d5f2b6e4a1
Create Date: 2026-08-19 14:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd9e6a3c7b5f4'
down_revision: Union[str, Sequence[str], None] = 'c8d5f2b6e4a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Column-level guard, not just a table guard: on a fresh database
    create_all() builds site_projects with this column already present,
    while on production the table exists from the previous migration and
    only the column is missing.
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'site_projects' not in inspector.get_table_names():
        return
    existing = {c['name'] for c in inspector.get_columns('site_projects')}
    if 'photo' not in existing:
        op.add_column('site_projects', sa.Column('photo', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'site_projects' not in inspector.get_table_names():
        return
    existing = {c['name'] for c in inspector.get_columns('site_projects')}
    if 'photo' in existing:
        op.drop_column('site_projects', 'photo')
