"""posts: discovered_by_query_id — links a post's account back to the exact
crawl_queries row that surfaced it, not just the world

Revision ID: a7d4f1c9b3e2
Revises: f2b7c4a91d6e
Create Date: 2026-09-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'a7d4f1c9b3e2'
down_revision: Union[str, None] = 'f2b7c4a91d6e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'posts',
        sa.Column('discovered_by_query_id', postgresql.UUID(as_uuid=True),
                   sa.ForeignKey('crawl_queries.id'), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('posts', 'discovered_by_query_id')
