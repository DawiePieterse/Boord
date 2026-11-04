"""market daily sales

Where each delivery note stands at the market, from the agent's emailed
daily sales report (models.MarketDailySale), so the Markets tab shows sold
and unsold before the tax invoice arrives. A new table only, so nothing the
sibling apps read changes.

Revision ID: 6e2a9c4f8b17
Revises: a3e7c5d1f20b
Created: 2026-11-04

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401 - autogenerate renders sqlmodel.sql.sqltypes.AutoString


revision = '6e2a9c4f8b17'
down_revision = 'a3e7c5d1f20b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'marketdailysale',
        sa.Column('agent', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('note_no', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('note_label', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('report_date', sa.Date(), nullable=False),
        sa.Column('received_on', sa.Date(), nullable=True),
        sa.Column('market_ref', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('lines', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('received', sa.Integer(), nullable=False),
        sa.Column('sold', sa.Integer(), nullable=False),
        sa.Column('unsold', sa.Integer(), nullable=False),
        sa.Column('gross', sa.Float(), nullable=False),
        sa.Column('file', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('email_from', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('imported_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('agent', 'note_no'),
    )


def downgrade() -> None:
    op.drop_table('marketdailysale')
