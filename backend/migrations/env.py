"""Alembic environment for Boord.

Two things here are not the generated default, and both matter on this app:

- The database URL comes from db.DATABASE_URL, not from alembic.ini. One
  place knows where a farm's database is.
- render_as_batch=True. SQLite cannot ALTER or DROP a column in place, so
  without batch mode a migration that renames a column, changes a type or
  adds a constraint is simply not expressible - which is the whole reason
  this app moved off db._legacy_schema_catch_up()'s ADD COLUMN-only path.
  Batch mode makes Alembic build a new table, copy the rows across and swap
  it in. Write migrations with `with op.batch_alter_table(...) as batch_op:`
  and they will work on the farm.
"""
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlmodel import SQLModel

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import models  # noqa: E402,F401 - importing it is what registers every table on the metadata
from db import DATABASE_URL, engine as default_engine  # noqa: E402

config = context.config

# migrate.py sets this to False. It runs inside the server process, where
# fileConfig() would tear down uvicorn's logging and replace it with
# alembic.ini's - so the app would keep running but stop saying anything.
# The `alembic` command line leaves it unset and gets the config it expects.
if config.attributes.get("configure_logger", True) and config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of touching a database (`alembic --sql`).

    Only ever used to read what a migration would do before shipping it; no
    farm runs migrations this way.
    """
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # `connectable` is how migrate.py and the self-test point this at a
    # database other than the live one - a throwaway copy - without any of
    # the environment-variable juggling that usually implies.
    connectable = config.attributes.get("connectable") or default_engine
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
