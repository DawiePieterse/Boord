"""farms can point at a real on-farm weather station

Weather has only ever come from Open-Meteo's regional forecast, keyed off
the farm's GPS pin - a nearby estimate, not what's actually happening over
the trees. A farm that owns a real weather station (iweathar.co.za) can now
point Boord at its station id instead, and every weather read (header,
crate stamps, dispatch capture) prefers it over the forecast.

Plain ADD COLUMN - see 203607e346ac for why this table always gets that
instead of batch_alter_table.

Revision ID: addc7772bd3a
Revises: c7e1a4f09b26
Created: 2026-09-22

"""
from alembic import op
import sqlalchemy as sa


revision = 'addc7772bd3a'
down_revision = 'c7e1a4f09b26'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('systemsetting', sa.Column('weather_station_id', sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('systemsetting') as batch_op:
        batch_op.drop_column('weather_station_id')
