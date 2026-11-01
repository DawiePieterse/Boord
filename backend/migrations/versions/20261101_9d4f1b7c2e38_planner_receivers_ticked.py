"""planner receivers ticked

The Planner's receivers get a tick per week, like its blocks: only the
receivers ticked count in the week's totals and reach the sheet and the
PDF. harvestplan.receivers_on is a JSON list of their ids. Null on the
plans already saved, which build_week reads as every receiver with
cartons planned, so an old week looks as it did.

One nullable column, added in place: plain ADD COLUMN, as for lot's
edited_at (5c1d8e2f7a94). harvestplan is Boord's own; no sibling reads it.

Revision ID: 9d4f1b7c2e38
Revises: 5c1d8e2f7a94
Created: 2026-11-01

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401 - autogenerate renders sqlmodel.sql.sqltypes.AutoString


revision = '9d4f1b7c2e38'
down_revision = '5c1d8e2f7a94'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('harvestplan', sa.Column('receivers_on', sqlmodel.sql.sqltypes.AutoString(), nullable=True))


def downgrade() -> None:
    # Batch mode here, unlike upgrade(): dropping a column needs SQLite 3.35+
    # and a farm server runs whatever SQLite its Python was built against.
    with op.batch_alter_table('harvestplan') as batch_op:
        batch_op.drop_column('receivers_on')
