"""season is a recurring anchor date, not a calendar year

A season used to be a single integer (current_harvest_year) with Jan-Dec
assumed. A litchi season crosses the new year, so the season is now a
recurring anchor - a month and a day - and the app derives which season is
current, labelling it by the year it starts in. current_harvest_year stays
as that derived label so report headers and older readers keep working.

Two plain ADD COLUMNs with a server_default of 1, which reproduces the old
1 January boundary exactly for every database that upgrades.

Revision ID: 6dd8df4e3bf3
Revises: 748269cfa3ea
Created: 2026-08-30 11:57:42.547216

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401 - autogenerate renders sqlmodel.sql.sqltypes.AutoString


revision = '6dd8df4e3bf3'
down_revision = '748269cfa3ea'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('systemsetting', sa.Column(
        'season_start_month', sa.Integer(), nullable=False, server_default='1'))
    op.add_column('systemsetting', sa.Column(
        'season_start_day', sa.Integer(), nullable=False, server_default='1'))


def downgrade() -> None:
    with op.batch_alter_table('systemsetting') as batch_op:
        batch_op.drop_column('season_start_day')
        batch_op.drop_column('season_start_month')
