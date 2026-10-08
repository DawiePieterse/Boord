"""deleted crates leave a tombstone

Crates can now be deleted - by the admin from a received lot, and by the field
device's "Undo last crate" before the slip is sent. A field device can replay a
crate it believes never landed, so the delete has to be remembered or the next
sync would quietly put the crate (and its wage) back. routers/sync.py skips any
uuid in this table.

Revision ID: 5b2e9d71c3a4
Revises: 5e0b7d3c21aa
Created: 2026-10-08

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401 - autogenerate renders sqlmodel.sql.sqltypes.AutoString


revision = '5b2e9d71c3a4'
down_revision = '5e0b7d3c21aa'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'deletedharvestrecord',
        sa.Column('uuid', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('lot_id', sa.Integer(), nullable=True),
        sa.Column('deleted_at', sa.DateTime(), nullable=False),
        sa.Column('deleted_by', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.ForeignKeyConstraint(['lot_id'], ['lot.id']),
        sa.PrimaryKeyConstraint('uuid'),
    )


def downgrade() -> None:
    op.drop_table('deletedharvestrecord')
