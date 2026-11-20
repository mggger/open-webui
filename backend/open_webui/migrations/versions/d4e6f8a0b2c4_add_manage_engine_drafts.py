"""Persist reviewed Manage Engine creation requests and their submission state."""
from alembic import op
import sqlalchemy as sa

revision = 'd4e6f8a0b2c4'
down_revision = 'c3d5e7f9a1b2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'manage_engine_draft',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('chat_id', sa.Text(), nullable=False),
        sa.Column('source_id', sa.Text(), nullable=False),
        sa.Column('endpoint', sa.Text(), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('result', sa.JSON(), nullable=True),
        sa.Column('expires_at', sa.BigInteger(), nullable=False),
        sa.UniqueConstraint('user_id', 'chat_id', 'source_id', name='uq_manage_engine_draft_source'),
    )


def downgrade():
    op.drop_table('manage_engine_draft')
