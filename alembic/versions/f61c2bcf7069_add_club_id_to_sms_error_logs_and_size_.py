"""add club_id to sms/error logs and size_bytes to stored objects

Revision ID: f61c2bcf7069
Revises: 6f7956bfa288
Create Date: 2026-08-16 10:18:11.325333

All four columns are nullable and there is no SQL backfill: sms_logs and
error_logs rows written before this never recorded which club they came
from, so they cannot be attributed retroactively and per-club counts start
from here. size_bytes is filled in separately by
storage.backfill_object_sizes() on startup, which asks R2 for each object's
length — not something SQL can do.

Foreign keys are named explicitly; autogenerate emits None, which makes the
downgrade's drop_constraint(None, ...) fail.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f61c2bcf7069'
down_revision: Union[str, Sequence[str], None] = '6f7956bfa288'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(bind, table: str) -> set[str]:
    # Re-inspect per call rather than reusing one Inspector: it caches, and
    # these checks run either side of DDL that changes what it would report.
    return {c['name'] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    """Upgrade schema.

    Same guard as the other add-column migrations: on a from-scratch
    database (CI's disposable Postgres) create_all() at app startup builds
    these tables straight off the models, new columns included — nothing to
    ALTER, and the tables don't even exist yet when migrations run. On
    production the tables exist without these columns.
    """
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())

    for table in ('gallery_photos', 'club_documents'):
        if table in tables and 'size_bytes' not in _columns(bind, table):
            op.add_column(table, sa.Column('size_bytes', sa.Integer(), nullable=True))

    for table in ('sms_logs', 'error_logs'):
        if table not in tables or 'club_id' in _columns(bind, table):
            continue
        op.add_column(table, sa.Column('club_id', sa.Integer(), nullable=True))
        op.create_index(
            op.f(f'ix_{table}_club_id'), table, ['club_id'], unique=False
        )
        # error_logs is created by a migration but clubs is not — it is one
        # of the tables that only ever came from create_all(). So on a
        # from-scratch database error_logs can exist here while its FK
        # target does not; create_all() builds the constraint from the
        # model moments later anyway.
        if 'clubs' in tables:
            op.create_foreign_key(
                f'{table}_club_id_fkey', table, 'clubs', ['club_id'], ['id']
            )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())

    for table in ('sms_logs', 'error_logs'):
        if table not in tables or 'club_id' not in _columns(bind, table):
            continue
        # The constraint is conditional on upgrade, so it may not be there.
        existing_fks = {
            fk['name'] for fk in sa.inspect(bind).get_foreign_keys(table)
        }
        if f'{table}_club_id_fkey' in existing_fks:
            op.drop_constraint(f'{table}_club_id_fkey', table, type_='foreignkey')
        op.drop_index(op.f(f'ix_{table}_club_id'), table_name=table)
        op.drop_column(table, 'club_id')

    for table in ('gallery_photos', 'club_documents'):
        if table in tables and 'size_bytes' in _columns(bind, table):
            op.drop_column(table, 'size_bytes')
