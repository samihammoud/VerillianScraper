"""posts: music_info fields (id, title, author, original)

Revision ID: f2b7c4a91d6e
Revises: d3f8a91c4b2e
Create Date: 2026-09-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2b7c4a91d6e'
down_revision: Union[str, None] = 'd3f8a91c4b2e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('posts', sa.Column('music_id', sa.Text(), nullable=True))
    op.add_column('posts', sa.Column('music_title', sa.Text(), nullable=True))
    op.add_column('posts', sa.Column('music_author', sa.Text(), nullable=True))
    op.add_column('posts', sa.Column('music_original', sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column('posts', 'music_original')
    op.drop_column('posts', 'music_author')
    op.drop_column('posts', 'music_title')
    op.drop_column('posts', 'music_id')
