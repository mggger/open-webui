"""Add per-user file search credentials

Revision ID: a81f5c2d4e90
Revises: f4b8a2c1d9e0
Create Date: 2026-07-24 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a81f5c2d4e90"
down_revision: Union[str, None] = "f4b8a2c1d9e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "file_search_credential",
        sa.Column("user_id", sa.Text(), nullable=False, primary_key=True, unique=True),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("encrypted_password", sa.Text(), nullable=False),
        sa.Column("default_directory", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )


def downgrade():
    op.drop_table("file_search_credential")
