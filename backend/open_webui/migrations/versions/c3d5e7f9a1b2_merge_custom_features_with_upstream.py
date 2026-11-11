"""Merge the custom feature migrations (conversation agents, big events,
file search credentials) with upstream Open WebUI's migration history.

The custom chain branched off upstream v0.6.41 (3e0e00844bb0). Installs that
already applied it keep their revision and get upstream's newer migrations
applied on top; fresh installs apply both branches.

Revision ID: c3d5e7f9a1b2
Revises: a81f5c2d4e90, 9f6c2a8d1b43
"""

from typing import Sequence, Union

revision: str = 'c3d5e7f9a1b2'
down_revision: Union[str, Sequence[str], None] = ('a81f5c2d4e90', '9f6c2a8d1b43')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
