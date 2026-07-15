"""Add big event discovery tables

Revision ID: f4b8a2c1d9e0
Revises: e7a1f4c3b9d2
Create Date: 2026-07-15 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f4b8a2c1d9e0"
down_revision: Union[str, None] = "e7a1f4c3b9d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "big_event",
        sa.Column("id", sa.String(), nullable=False, primary_key=True, unique=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("start", sa.String(), nullable=False),
        sa.Column("end", sa.String(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("organiser", sa.Text(), nullable=True),
        sa.Column("target_audience", sa.Text(), nullable=True),
        sa.Column("cost", sa.Text(), nullable=True),
        sa.Column("participation", sa.Text(), nullable=True),
        sa.Column("registration_url", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("last_verified", sa.String(), nullable=True),
        sa.Column("first_seen_at", sa.BigInteger(), nullable=False),
        sa.Column("last_seen_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_big_event_start", "big_event", ["start"])
    op.create_index("ix_big_event_source_type", "big_event", ["source_type"])

    op.create_table(
        "big_event_discovery_state",
        sa.Column("id", sa.String(), nullable=False, primary_key=True),
        sa.Column("last_success", sa.BigInteger(), nullable=True),
        sa.Column("last_attempt", sa.BigInteger(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("engine", sa.String(), nullable=True),
    )


def downgrade():
    op.drop_table("big_event_discovery_state")
    op.drop_index("ix_big_event_source_type", table_name="big_event")
    op.drop_index("ix_big_event_start", table_name="big_event")
    op.drop_table("big_event")
