"""market statements and invoices

What each market paid for a Boord Packhouse delivery note, and what its
transport cost: typed in by the office (Admin -> Reports -> Markets &
Transport), or read from the market agents' emailed invoices (market_mail.py),
with the pack each market product description stands for. New tables only,
so nothing the sibling apps read changes.

Revision ID: 3f6a9c2e1d85
Revises: e4b19c6d0a52
Created: 2026-10-27

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401 - autogenerate renders sqlmodel.sql.sqltypes.AutoString


revision = '3f6a9c2e1d85'
down_revision = 'e4b19c6d0a52'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'marketstatement',
        sa.Column('note_uuid', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('lines', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('commission', sa.Float(), nullable=False),
        sa.Column('deductions', sa.Float(), nullable=False),
        sa.Column('bank_cost', sa.Float(), nullable=False),
        sa.Column('waybill_no', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('invoice_no', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('transport_cost', sa.Float(), nullable=False),
        sa.Column('notes', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('note_uuid'),
    )
    op.create_table(
        'marketinvoice',
        sa.Column('invoice_no', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('note_no', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('invoice_date', sa.Date(), nullable=False),
        sa.Column('payment', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('agent', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('lines', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('gross', sa.Float(), nullable=False),
        sa.Column('commission', sa.Float(), nullable=False),
        sa.Column('deductions', sa.Float(), nullable=False),
        sa.Column('bank_cost', sa.Float(), nullable=False),
        sa.Column('net', sa.Float(), nullable=False),
        sa.Column('file', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('email_from', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('imported_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('invoice_no'),
    )
    op.create_table(
        'marketproduct',
        sa.Column('product', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('pack', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.PrimaryKeyConstraint('product'),
    )


def downgrade() -> None:
    op.drop_table('marketproduct')
    op.drop_table('marketinvoice')
    op.drop_table('marketstatement')
