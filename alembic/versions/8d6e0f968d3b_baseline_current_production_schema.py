"""baseline: mark current schema as the Alembic starting point

Revision ID: 8d6e0f968d3b
Revises:
Create Date: 2026-07-15 13:23:48.480383

This project had no migration tool before this: schema changes were
create_all() plus hand-written idempotent `ALTER TABLE ... ADD COLUMN IF
NOT EXISTS` in main.py's startup — fine for additive columns, unsupported
for anything else (renames, drops, backfills). This revision is a
deliberate no-op: it exists only so the already-live production database
can be `alembic stamp head`-ed onto it without running any DDL, since every
table it would create already exists. Every schema change from here on
should be a real migration, not another line in main.py.

(Autogenerate also flagged 4 columns — clubs.club_type,
event_rsvps.attendee_type/club_name, minutes.body — as NOT NULL in the
ORM models but nullable in the actual DB, since the ALTER TABLE ADD COLUMN
lines that created them never added a NOT NULL constraint. Deliberately
left alone here rather than silently tightened as a side effect of adding
migration tooling — worth its own migration if you want it fixed.)
"""
from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = '8d6e0f968d3b'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
