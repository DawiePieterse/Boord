"""transport crates

Light crates are combined in the field to ~20kg for transport, so the crates
on a truck can be fewer than the crates picked. total_crates stays the
picking count (worker pay); lot.transport_crates is what was sent and what
the pack house expects. NULL = same as total_crates (every existing slip).

Revision ID: c7a3e5f81b24
Revises: 9d4f1a6b2e70
Created: 2026-10-19

"""
from alembic import op
import sqlalchemy as sa


revision = 'c7a3e5f81b24'
down_revision = '9d4f1a6b2e70'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('lot') as batch_op:
        batch_op.add_column(sa.Column('transport_crates', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('lot') as batch_op:
        batch_op.drop_column('transport_crates')
