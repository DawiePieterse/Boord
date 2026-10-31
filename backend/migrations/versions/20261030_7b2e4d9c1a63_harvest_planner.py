"""harvest planner

The Admin Planner's two tables: a plan per week (which blocks, how much,
which days, the cartons per receiver) and, per Packhouse pack, the cartons
to a pallet and Packhouse's units to a carton. New tables only, so nothing the sibling apps read changes.

Revision ID: 7b2e4d9c1a63
Revises: 3f8a2c6d9e15
Created: 2026-10-30

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401 - autogenerate renders sqlmodel.sql.sqltypes.AutoString


revision = '7b2e4d9c1a63'
down_revision = '3f8a2c6d9e15'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'harvestplan',
        sa.Column('week_start', sa.Date(), nullable=False),
        sa.Column('season_year', sa.Integer(), nullable=False),
        sa.Column('blocks', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('deliveries', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('loss_pct', sa.Float(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('week_start'),
    )
    op.create_index(op.f('ix_harvestplan_season_year'), 'harvestplan', ['season_year'], unique=False)
    op.create_table(
        'plannerpack',
        sa.Column('pack', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('cartons_per_pallet', sa.Integer(), nullable=False),
        sa.Column('units_per_carton', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('pack'),
    )


def downgrade() -> None:
    op.drop_table('plannerpack')
    op.drop_index(op.f('ix_harvestplan_season_year'), table_name='harvestplan')
    op.drop_table('harvestplan')
