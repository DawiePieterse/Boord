"""pre-orders

Cartons a receiver books ahead (models.PreOrder), typed in Settings and
filling the Planner's week of their pack day. A new table only, so nothing
the sibling apps read changes.

Revision ID: a3e7c5d1f20b
Revises: 9d4f1b7c2e38
Created: 2026-11-01

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401 - autogenerate renders sqlmodel.sql.sqltypes.AutoString


revision = 'a3e7c5d1f20b'
down_revision = '9d4f1b7c2e38'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'preorder',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('pack_day', sa.Date(), nullable=False),
        sa.Column('receiver_id', sa.Integer(), nullable=False),
        sa.Column('target_receiver_id', sa.Integer(), nullable=True),
        sa.Column('pack', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('cartons', sa.Integer(), nullable=False),
        sa.Column('note', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_preorder_pack_day'), 'preorder', ['pack_day'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_preorder_pack_day'), table_name='preorder')
    op.drop_table('preorder')
