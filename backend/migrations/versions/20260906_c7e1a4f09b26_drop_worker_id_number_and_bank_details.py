"""drop worker id_number and bank details (POPIA)

The worker table carried three fields Boord never needed to run a harvest: the
SA ID number, the bank name and the account number. They were captured for a
payroll export and nothing else, and their presence turned every farm backup
and every worker export into a file holding regulated personal information.

To comply with POPIA they are gone. This drops the columns, so a farm upgrading
past this revision loses whatever was in them - that is the point, the same
shape as 4c81f2a90d17 dropping the admin password. The payroll exports
(routers/payments.py, routers/reports.py, routers/master_data.py) drop the
Bank / Account / ID Number columns to match; wages still work out from kg and
rate, which is all they ever depended on.

The baseline migration still creates these columns and must keep doing so: it
describes the schema farms actually received, and migrate._baseline_database()
replays it to build the throwaway database a pre-Alembic farm is caught up
against. This migration is what removes them afterwards.

What did NOT change: whatsapp_number and the worker photo are still personal
data, so master_data.list_workers still keeps the full worker record back from
callers that are not the admin, decided from the request's address.

Revision ID: c7e1a4f09b26
Revises: 4c81f2a90d17
Created: 2026-09-06

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401 - autogenerate renders sqlmodel.sql.sqltypes.AutoString


revision = 'c7e1a4f09b26'
down_revision = '4c81f2a90d17'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('worker') as batch_op:
        batch_op.drop_column('id_number')
        batch_op.drop_column('bank')
        batch_op.drop_column('account')


def downgrade() -> None:
    # Recreates the columns, empty. The data they held is not kept anywhere and
    # is not restored - a database rolled back to here has three blank columns.
    with op.batch_alter_table('worker') as batch_op:
        batch_op.add_column(sa.Column(
            'account', sqlmodel.sql.sqltypes.AutoString(), nullable=False,
            server_default=''))
        batch_op.add_column(sa.Column(
            'bank', sqlmodel.sql.sqltypes.AutoString(), nullable=False,
            server_default=''))
        batch_op.add_column(sa.Column(
            'id_number', sqlmodel.sql.sqltypes.AutoString(), nullable=False,
            server_default=''))
