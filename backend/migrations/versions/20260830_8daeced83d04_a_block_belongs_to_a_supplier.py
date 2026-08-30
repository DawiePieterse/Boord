"""a block belongs to a supplier

Every block now names the supplier whose orchard it is. Nullable - an
established pack house upgrades with it unset and assigns blocks afterwards,
and a block that is genuinely the pack house's own can stay NULL. Batch mode
because SQLite adds a foreign key only by rebuilding the table.

Revision ID: 8daeced83d04
Revises: f21a7bca0b5a
Created: 2026-08-30 11:57:48.508800

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401 - autogenerate renders sqlmodel.sql.sqltypes.AutoString


revision = '8daeced83d04'
down_revision = 'f21a7bca0b5a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('block') as batch_op:
        batch_op.add_column(sa.Column('supplier_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_block_supplier_id_supplier', 'supplier', ['supplier_id'], ['id'])


def downgrade() -> None:
    with op.batch_alter_table('block') as batch_op:
        batch_op.drop_constraint('fk_block_supplier_id_supplier', type_='foreignkey')
        batch_op.drop_column('supplier_id')
