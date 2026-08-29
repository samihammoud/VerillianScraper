"""posts.discovered_by_world

Revision ID: c5e2a83f1d47
Revises: b4c1e7f20a91
Create Date: 2026-08-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c5e2a83f1d47'
down_revision: Union[str, None] = 'b4c1e7f20a91'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # world_slug of the crawl_query that discovered this post's account, not a
    # routing result — routing (topology.world_posts) can disagree with this.
    op.add_column('posts', sa.Column('discovered_by_world', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('posts', 'discovered_by_world')
