"""weather history records the location it was fetched for

WeatherHistory held one farm's hourly weather with nothing saying WHICH
farm's. That was invisible while nothing could change - but a farm that
corrects its GPS in Settings goes on appending the new location's hours to
the old location's, in one table, indistinguishable, and the Risk indicator
then scores a season against a blend of two places.

Two nullable columns, plus a stamp of what is already there:

- A database with a location in SystemSetting gets every existing row
  stamped with it. That is the best evidence there is, and it is right for
  every farm that has not moved its pin: weather.farm_coords() is the only
  thing that has ever been able to fetch these rows, and it reads exactly
  that setting. A farm that HAD already moved its pin has a mixed table
  that no migration can unmix - stamping makes it look consistent, which is
  no worse than the undifferentiated state it is in now, and the next
  backfill replaces the modern range anyway.
- A database with NO location keeps NULL, and NULL deliberately counts as
  "somewhere else" (see the model, and weather.different_location). Those
  rows can only have come from the hardcoded fallback coordinates
  farm_coords() used to carry, which means they are literally another
  farm's weather; the first backfill after a location is set clears them.

Plain ADD COLUMN, not a batch_alter_table. Batch mode rebuilds the table by
copying every row into a new one, and on an established farm this is ~350k
rows and most of a 42 MB database - all to add two nullable columns, which
is the one alteration SQLite has always been able to do in place.

Revision ID: 203607e346ac
Revises: 8bc3a7dc4b1e
Created: 2026-08-29

"""
from alembic import op
import sqlalchemy as sa


revision = '203607e346ac'
down_revision = '8bc3a7dc4b1e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('weatherhistory', sa.Column('lat', sa.Float(), nullable=True))
    op.add_column('weatherhistory', sa.Column('lon', sa.Float(), nullable=True))

    bind = op.get_bind()
    configured = bind.execute(sa.text(
        "SELECT gps_lat, gps_lon FROM systemsetting "
        "WHERE gps_lat IS NOT NULL AND gps_lon IS NOT NULL LIMIT 1"
    )).first()
    if configured is not None:
        bind.execute(sa.text("UPDATE weatherhistory SET lat = :lat, lon = :lon"),
                     {"lat": configured[0], "lon": configured[1]})


def downgrade() -> None:
    # Batch mode here, unlike upgrade(): dropping a column needs SQLite 3.35+
    # and a farm server runs whatever SQLite its Python was built against.
    with op.batch_alter_table('weatherhistory') as batch_op:
        batch_op.drop_column('lon')
        batch_op.drop_column('lat')
