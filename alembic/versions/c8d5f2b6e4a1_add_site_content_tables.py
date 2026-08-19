"""add site content tables

Revision ID: c8d5f2b6e4a1
Revises: b7c4e1a9d3f2
Create Date: 2026-08-19 13:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8d5f2b6e4a1'
down_revision: Union[str, Sequence[str], None] = 'b7c4e1a9d3f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Each table is guarded independently rather than as a group: none of
    them have foreign keys, so on a fresh database create_all() may have
    built any subset of them already.
    """
    bind = op.get_bind()
    tables = sa.inspect(bind).get_table_names()

    if 'site_events' not in tables:
        op.create_table(
            'site_events',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('event_date', sa.Date(), nullable=False),
            sa.Column('title', sa.String(length=160), nullable=False),
            sa.Column('meta', sa.String(length=240), nullable=False, server_default=''),
            sa.Column('kind', sa.String(length=40), nullable=False, server_default=''),
            sa.Column('published', sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(op.f('ix_site_events_event_date'), 'site_events', ['event_date'], unique=False)

    if 'site_news' not in tables:
        op.create_table(
            'site_news',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('published_on', sa.Date(), nullable=False),
            sa.Column('title', sa.String(length=160), nullable=False),
            sa.Column('body', sa.Text(), nullable=False, server_default=''),
            sa.Column('published', sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(op.f('ix_site_news_published_on'), 'site_news', ['published_on'], unique=False)

    if 'site_projects' not in tables:
        op.create_table(
            'site_projects',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('tag', sa.String(length=8), nullable=False, server_default=''),
            sa.Column('area', sa.String(length=80), nullable=False, server_default=''),
            sa.Column('title', sa.String(length=160), nullable=False),
            sa.Column('body', sa.Text(), nullable=False, server_default=''),
            sa.Column('progress_percent', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('deadline', sa.Date(), nullable=True),
            sa.Column('photo_caption', sa.String(length=120), nullable=False, server_default=''),
            sa.Column('published', sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.PrimaryKeyConstraint('id'),
        )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    tables = sa.inspect(bind).get_table_names()
    if 'site_projects' in tables:
        op.drop_table('site_projects')
    if 'site_news' in tables:
        op.drop_index(op.f('ix_site_news_published_on'), table_name='site_news')
        op.drop_table('site_news')
    if 'site_events' in tables:
        op.drop_index(op.f('ix_site_events_event_date'), table_name='site_events')
        op.drop_table('site_events')
