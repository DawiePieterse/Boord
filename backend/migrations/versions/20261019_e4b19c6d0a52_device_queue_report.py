"""device queue report

Each field device reports how many crates, lot dispatches and undos it is
still holding unsent, and when the oldest unsent crate was captured, so the
outbox of a tablet that has stopped syncing can be seen from the server (and
by Boord Monitor) before the tablet is lost. All nullable: NULL means the
device has not reported since this release - every existing device.

Revision ID: e4b19c6d0a52
Revises: c7a3e5f81b24
Created: 2026-10-19

"""
from alembic import op
import sqlalchemy as sa


revision = 'e4b19c6d0a52'
down_revision = 'c7a3e5f81b24'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('device') as batch_op:
        batch_op.add_column(sa.Column('queue_pending', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('queue_oldest', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('queue_reported_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('device') as batch_op:
        batch_op.drop_column('queue_reported_at')
        batch_op.drop_column('queue_oldest')
        batch_op.drop_column('queue_pending')
