"""history and weather move to the owner app

The three tables behind everything Boord no longer shows. WeatherHistory was
the hourly record back to 1987; HistoricalHarvest and HistoricalAnnualYield
were the pre-app harvest seasons. Between them they fed the Analysis, Weather
and Risk tabs - which left with the Owner View - and the Historical Harvest
Data report, which is being rebuilt in that app rather than moved.

What Boord keeps is the live half of weather, which never used these tables:
the header readout and the conditions stamped onto each crate at dispatch
both call Open-Meteo directly through weather.fetch_weather_cached().

This is the big one for file size. WeatherHistory plus its unique index were
42.0 MB of a 42.4 MB database on an established farm; every other table
together came to ~0.3 MB. Measured on a real farm database: 40 MB before,
104 KB after.

Nothing here is recoverable from the app afterwards. The harvest history came
from workbooks in data\\imports\\ and can be re-imported into the Owner app;
the weather is re-downloadable from Open-Meteo. downgrade() rebuilds the
tables empty, which is the most any migration can honestly promise.

Revision ID: 748269cfa3ea
Revises: 9e39262b1e30
Created: 2026-08-29

"""
from alembic import op
import sqlalchemy as sa


revision = '748269cfa3ea'
down_revision = '9e39262b1e30'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('weatherhistory', schema=None) as batch_op:
        batch_op.drop_index('ix_weatherhistory_timestamp')
    op.drop_table('weatherhistory')
    op.drop_table('historicalharvest')
    op.drop_table('historicalannualyield')

    # DROP TABLE hands SQLite's pages back to the file as free space, not to
    # the filesystem - without this the .db stays 40 MB, all of it empty. The
    # farm would keep backing up, copying and restoring that forever with no
    # way to tell why.
    #
    # VACUUM rewrites the database, so it wants room for a second copy and it
    # is not something to do casually. Both are fine here: migrate.py has just
    # taken a full pre-migration snapshot and refuses to proceed without one,
    # and this runs once, during an update, with the server not yet serving.
    #
    # Verified rather than assumed - VACUUM is normally illegal inside a
    # transaction, and it does run here. If a future SQLAlchemy or alembic
    # changes that, this raises loudly during the update instead of silently
    # skipping, which is the right way round.
    op.get_bind().execute(sa.text('VACUUM'))


def downgrade() -> None:
    op.create_table(
        'historicalannualyield',
        sa.Column('id', sa.INTEGER(), nullable=False),
        sa.Column('block_id', sa.VARCHAR(), nullable=True),
        sa.Column('season_year', sa.INTEGER(), nullable=False),
        sa.Column('kg', sa.FLOAT(), nullable=False),
        sa.Column('estimated', sa.BOOLEAN(), nullable=False),
        sa.ForeignKeyConstraint(['block_id'], ['block.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'historicalharvest',
        sa.Column('id', sa.INTEGER(), nullable=False),
        sa.Column('block_id', sa.VARCHAR(), nullable=True),
        sa.Column('harvest_date', sa.DATE(), nullable=False),
        sa.Column('season_year', sa.INTEGER(), nullable=False),
        sa.Column('kg', sa.FLOAT(), nullable=False),
        sa.Column('estimated', sa.BOOLEAN(), nullable=False),
        sa.ForeignKeyConstraint(['block_id'], ['block.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'weatherhistory',
        sa.Column('id', sa.INTEGER(), nullable=False),
        sa.Column('timestamp', sa.DATETIME(), nullable=False),
        sa.Column('temp_c', sa.FLOAT(), nullable=True),
        sa.Column('humidity_pct', sa.FLOAT(), nullable=True),
        sa.Column('dew_point_c', sa.FLOAT(), nullable=True),
        sa.Column('precipitation_mm', sa.FLOAT(), nullable=True),
        sa.Column('weather_code', sa.INTEGER(), nullable=True),
        sa.Column('condition', sa.VARCHAR(), nullable=False),
        sa.Column('wind_speed_kmh', sa.FLOAT(), nullable=True),
        sa.Column('soil_temp_6cm_c', sa.FLOAT(), nullable=True),
        sa.Column('uv_index', sa.FLOAT(), nullable=True),
        sa.Column('sunshine_duration_s', sa.FLOAT(), nullable=True),
        sa.Column('lat', sa.FLOAT(), nullable=True),
        sa.Column('lon', sa.FLOAT(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('weatherhistory', schema=None) as batch_op:
        batch_op.create_index('ix_weatherhistory_timestamp', ['timestamp'], unique=1)
