"""suppliers carry a PUC and a GlobalGAP number

Traceability fields every supplier needs: PUC (Product Unit Code) and the
GlobalG.A.P. Number. Both text, both default to empty, so an established
pack house upgrades with them blank and fills them in per supplier.

Revision ID: f21a7bca0b5a
Revises: a793f878b17b
Created: 2026-08-30 11:57:48.081429

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401 - autogenerate renders sqlmodel.sql.sqltypes.AutoString


revision = 'f21a7bca0b5a'
down_revision = 'a793f878b17b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('supplier', sa.Column(
        'puc', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''))
    op.add_column('supplier', sa.Column(
        'global_gap_number', sqlmodel.sql.sqltypes.AutoString(), nullable=False,
        server_default=''))


def downgrade() -> None:
    with op.batch_alter_table('supplier') as batch_op:
        batch_op.drop_column('global_gap_number')
        batch_op.drop_column('puc')
