"""owner view moves to its own app

The read-only Owner View - the link-only dashboard at /owner/?key=... - is
being rebuilt as a separate application, so it leaves Boord whole: the
screen, the /api/owner-view router, and this table. What Boord ships is the
three screens a farm actually works on, Field, Pack House and Admin.

OwnerViewToken held one row: the shared secret embedded in that link. There
is nothing to migrate anywhere, because the token only ever gated a URL that
no longer resolves. Dropping it invalidates every link already handed out,
which is the intended outcome - the new app will issue its own.

The baseline migration still creates this table and must keep doing so. It
is replayed in full on every fresh install, and migrate._baseline_database()
now also replays it to build the throwaway database a pre-Alembic farm is
caught up against. Both need the baseline to describe what farms actually
had; this migration is what removes it afterwards.

The code that was here is not gone. It moved to the Boord Owner app, which
is now its own project outside this repository, and carries the router, the
screen and the model definition verbatim. Until this commit it sat in an
owner-app/ folder here; recover it from that history if the project is ever
mislaid.

Revision ID: 9e39262b1e30
Revises: 203607e346ac
Created: 2026-08-29

"""
from alembic import op
import sqlalchemy as sa


revision = '9e39262b1e30'
down_revision = '203607e346ac'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table('ownerviewtoken')


def downgrade() -> None:
    # Recreates the table, not the secret. Anything that ran against the old
    # one needs a new token seeded - db.seed_defaults() used to do that on
    # first run, and no longer does.
    op.create_table(
        'ownerviewtoken',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('token', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
