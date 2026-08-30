"""the farm becomes a pack house with a code

One install is one pack house that several suppliers deliver into. The
site-level identity columns are renamed to say so, and a PHC (Pack House
Code) is added alongside them. farm_name -> packhouse_name and
farm_location -> packhouse_location keep their data; the rename needs batch
mode because SQLite cannot rename a column in place.

Revision ID: a793f878b17b
Revises: 6dd8df4e3bf3
Created: 2026-08-30 11:57:47.656610

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401 - autogenerate renders sqlmodel.sql.sqltypes.AutoString


revision = 'a793f878b17b'
down_revision = '6dd8df4e3bf3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('systemsetting') as batch_op:
        batch_op.alter_column('farm_name', new_column_name='packhouse_name')
        batch_op.alter_column('farm_location', new_column_name='packhouse_location')
        batch_op.add_column(sa.Column(
            'packhouse_code', sqlmodel.sql.sqltypes.AutoString(),
            nullable=False, server_default=''))


def downgrade() -> None:
    with op.batch_alter_table('systemsetting') as batch_op:
        batch_op.drop_column('packhouse_code')
        batch_op.alter_column('packhouse_location', new_column_name='farm_location')
        batch_op.alter_column('packhouse_name', new_column_name='farm_name')
