"""worker id_number returns, filled from whatsapp_number

c7e1a4f09b26 dropped the SA ID number for POPIA. With nowhere else to put it,
the office typed each worker's ID or passport number into the WhatsApp Number
field instead, so on a live farm whatsapp_number mostly holds identity numbers
and not phone numbers. The farm wants the ID back as its own field: an SA ID
number, or a passport number for a foreign worker. It stays admin-only, like
whatsapp_number (master_data._PUBLIC_WORKER_FIELDS is a whitelist).

The move: every whatsapp_number that does not look like an SA cell number is
copied to id_number and whatsapp_number is cleared. A value that does look like
a cell number (0821234567, +27 82 123 4567) is left where it is - it is a real
WhatsApp number someone typed into the right field, and moving it would file a
phone number as an ID. An SA ID is 13 digits and a passport has letters, so
neither can be mistaken for a cell number.

The downgrade copies id_number back into an empty whatsapp_number before
dropping the column, so rolling back loses nothing that was moved.

Revision ID: 3f8a2c6d9e15
Revises: 3f6a9c2e1d85
Created: 2026-10-27

"""
import re

from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401 - autogenerate renders sqlmodel.sql.sqltypes.AutoString


revision = '3f8a2c6d9e15'
down_revision = '3f6a9c2e1d85'
branch_labels = None
depends_on = None


# 0 or +27/27, then a 6/7/8 mobile prefix and 8 more digits, after removing
# spaces, dashes and brackets.
_SA_CELL = re.compile(r"^(0|\+?27)[678]\d{8}$")


def looks_like_sa_cell(value):
    return bool(_SA_CELL.match(re.sub(r"[\s\-()]", "", value or "")))


def upgrade() -> None:
    with op.batch_alter_table('worker') as batch_op:
        batch_op.add_column(sa.Column(
            'id_number', sqlmodel.sql.sqltypes.AutoString(), nullable=False,
            server_default=''))

    con = op.get_bind()
    rows = con.execute(sa.text(
        "SELECT id, whatsapp_number FROM worker WHERE TRIM(whatsapp_number) != ''")).fetchall()
    for worker_id, value in rows:
        if not looks_like_sa_cell(value):
            con.execute(sa.text(
                "UPDATE worker SET id_number = :v, whatsapp_number = '' WHERE id = :id"),
                {"v": value.strip(), "id": worker_id})


def downgrade() -> None:
    op.execute("UPDATE worker SET whatsapp_number = id_number "
               "WHERE whatsapp_number = '' AND id_number != ''")
    with op.batch_alter_table('worker') as batch_op:
        batch_op.drop_column('id_number')
