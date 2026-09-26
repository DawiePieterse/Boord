"""own-fruit workers point at the own-fruit supplier

Own-fruit workers used to be stored with supplier_id left NULL rather than
pointing at the is_own_farm Supplier row, so every place that grouped or
filtered workers by supplier had to treat NULL as "own fruit" as a special
case (payments, dashboard, reports). This fills in the own-fruit supplier's
id for every such worker, and routers/master_data.py now does the same on
every write, so a worker's supplier_id is always a real supplier.

A database with no own-fruit supplier row yet (a brand new install, before
seed_defaults() has run) has no workers either, so there is nothing to fill;
db.seed_defaults() fills any stragglers when it creates that row.

Revision ID: 5e0b7d3c21aa
Revises: addc7772bd3a
Created: 2026-09-26

"""
from alembic import op


revision = '5e0b7d3c21aa'
down_revision = 'addc7772bd3a'
branch_labels = None
depends_on = None

_OWN_ID = "(SELECT MIN(id) FROM supplier WHERE is_own_farm = 1)"


def upgrade() -> None:
    op.execute(f"UPDATE worker SET supplier_id = {_OWN_ID} "
               f"WHERE supplier_id IS NULL AND {_OWN_ID} IS NOT NULL")


def downgrade() -> None:
    op.execute(f"UPDATE worker SET supplier_id = NULL WHERE supplier_id = {_OWN_ID}")
