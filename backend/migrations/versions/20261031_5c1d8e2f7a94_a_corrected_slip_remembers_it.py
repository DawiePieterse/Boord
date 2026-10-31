"""a corrected slip remembers it

The office can now correct a received slip itself - its block, driver,
supplier, times, the receiving numbers - not only its crates. A field
tablet that queued its dispatch offline replays the same POST later with
the values it had at dispatch, so the lot needs the same stamp a corrected
crate carries (HarvestRecord.edited_at): upsert_lot keeps the office's
values on a lot that has one.

Two nullable columns, added in place: plain ADD COLUMN, as for
weatherhistory's lat/lon (203607e346ac), because batch mode copies every
row of the table to add a column SQLite can add directly.

Revision ID: 5c1d8e2f7a94
Revises: 7b2e4d9c1a63
Created: 2026-10-31

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401 - autogenerate renders sqlmodel.sql.sqltypes.AutoString


revision = '5c1d8e2f7a94'
down_revision = '7b2e4d9c1a63'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('lot', sa.Column('edited_at', sa.DateTime(), nullable=True))
    op.add_column('lot', sa.Column('edited_by', sqlmodel.sql.sqltypes.AutoString(), nullable=True))


def downgrade() -> None:
    # Batch mode here, unlike upgrade(): dropping a column needs SQLite 3.35+
    # and a farm server runs whatever SQLite its Python was built against.
    with op.batch_alter_table('lot') as batch_op:
        batch_op.drop_column('edited_by')
        batch_op.drop_column('edited_at')
