"""a field device is allocated to a supplier

A field harvest device now names the supplier it picks for; lots dispatched
from it are attributed there instead of always to the pack house's own
fruit. Nullable - an unset device still falls back to the own-fruit
supplier. Batch mode for the foreign key, as with block.supplier_id.

Revision ID: fd179ddb1128
Revises: 8daeced83d04
Created: 2026-08-30 11:57:48.939819

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401 - autogenerate renders sqlmodel.sql.sqltypes.AutoString


revision = 'fd179ddb1128'
down_revision = '8daeced83d04'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('device') as batch_op:
        batch_op.add_column(sa.Column('supplier_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_device_supplier_id_supplier', 'supplier', ['supplier_id'], ['id'])


def downgrade() -> None:
    with op.batch_alter_table('device') as batch_op:
        batch_op.drop_constraint('fk_device_supplier_id_supplier', type_='foreignkey')
        batch_op.drop_column('supplier_id')
