"""supplier document details

What Boord Packhouse prints for a supplier on the delivery notes and export
papers it now makes per supplier: the name on the documents, the registration
line, the letterhead's contact lines, the postal address and the prefix of its
delivery note numbers (models.Supplier). New columns only, each blank until
the office fills it in, so nothing the sibling apps read changes: Packhouse
reads them when they are there and prints as before when they are not.

Revision ID: c81d5e3a9f42
Revises: 6e2a9c4f8b17
Created: 2026-11-20

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401 - autogenerate renders sqlmodel.sql.sqltypes.AutoString


revision = 'c81d5e3a9f42'
down_revision = '6e2a9c4f8b17'
branch_labels = None
depends_on = None

COLUMNS = ('document_name', 'reg_number', 'document_contact', 'document_address', 'note_prefix')


def upgrade() -> None:
    for name in COLUMNS:
        op.add_column('supplier', sa.Column(
            name, sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''))


def downgrade() -> None:
    with op.batch_alter_table('supplier') as batch_op:
        for name in reversed(COLUMNS):
            batch_op.drop_column(name)
