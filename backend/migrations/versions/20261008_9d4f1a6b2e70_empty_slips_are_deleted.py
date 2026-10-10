"""empty slips are deleted

When the last crate on a slip is deleted (admin) or undone (field), the slip
now goes too - routers/lots.py delete_lot_if_empty. Before this, v3.13 left
those slips in the Received list at 0 crates / 0 kg.

Two things here:

1. deletedharvestrecord gains slip_number. lot_id cannot outlive the lot, and
   the slip number is what stops a field device's stale dispatch retry from
   re-creating a slip the admin emptied (routers/lots.py upsert_lot).

2. The slips v3.13 already left empty are deleted, with their receiving and
   pre-pack rows. Only slips that lost crates to a delete and have none left:
   a lot logged by hand at receiving never had any crates and is not touched.

Revision ID: 9d4f1a6b2e70
Revises: 5b2e9d71c3a4
Created: 2026-10-08

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401 - autogenerate renders sqlmodel.sql.sqltypes.AutoString


revision = '9d4f1a6b2e70'
down_revision = '5b2e9d71c3a4'
branch_labels = None
depends_on = None

_EMPTIED_LOTS = """
    SELECT DISTINCT d.lot_id FROM deletedharvestrecord d
    WHERE d.lot_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM harvestrecord h WHERE h.lot_id = d.lot_id)
"""


def upgrade() -> None:
    with op.batch_alter_table('deletedharvestrecord') as batch_op:
        batch_op.add_column(sa.Column('slip_number', sqlmodel.sql.sqltypes.AutoString(), nullable=True))

    op.execute("""
        UPDATE deletedharvestrecord
        SET slip_number = (SELECT slip_number FROM lot WHERE lot.id = deletedharvestrecord.lot_id)
        WHERE lot_id IS NOT NULL
    """)
    op.execute(f"DELETE FROM receivingrecord WHERE lot_id IN ({_EMPTIED_LOTS})")
    op.execute(f"DELETE FROM prepackrecord WHERE lot_id IN ({_EMPTIED_LOTS})")
    op.execute(f"DELETE FROM lot WHERE id IN ({_EMPTIED_LOTS})")
    op.execute("""
        UPDATE deletedharvestrecord SET lot_id = NULL
        WHERE lot_id IS NOT NULL AND lot_id NOT IN (SELECT id FROM lot)
    """)


def downgrade() -> None:
    # The deleted slips are not restored - they had no crates to restore.
    with op.batch_alter_table('deletedharvestrecord') as batch_op:
        batch_op.drop_column('slip_number')
