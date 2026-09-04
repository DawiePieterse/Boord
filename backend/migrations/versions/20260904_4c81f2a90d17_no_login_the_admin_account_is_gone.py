"""no login - the admin account is gone

Boord had one admin account, and one person who used it. The username, the
bcrypt hash and the first-login password dance were machinery guarding a door
that is now guarded by the network instead: the Admin app answers the server's
own console and the tailnet, and nothing else (backend/security.py).

So AdminUser goes, and this drops the table holding it. There is nothing to
migrate anywhere - the row held a username of "admin" and a hash of a password
that no longer opens anything. Farms upgrading past this revision lose the
password they set, which is the point.

The baseline migration still creates this table and must keep doing so. It is
replayed in full on every fresh install, and migrate._baseline_database()
replays it to build the throwaway database a pre-Alembic farm is caught up
against. Both need the baseline to describe what farms actually had; this
migration is what removes it afterwards - the same shape as 9e39262b1e30,
which retired ownerviewtoken.

What did NOT go with it: the Field and Pack House screens are still served to
the whole farm wifi, so master_data.list_workers still keeps ID numbers and
bank details back from callers that are not the admin. It decides that from
the request's address now rather than from a token.

Revision ID: 4c81f2a90d17
Revises: fd179ddb1128
Created: 2026-09-04

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel


revision = '4c81f2a90d17'
down_revision = 'fd179ddb1128'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table('adminuser')


def downgrade() -> None:
    # Recreates the table, not the account. Nothing seeds one any more, so a
    # database rolled back to here has an adminuser table with no rows in it -
    # and the code that would have signed anybody in is gone too.
    op.create_table(
        'adminuser',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('username', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('password_hash', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('must_change_password', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('username'),
    )
