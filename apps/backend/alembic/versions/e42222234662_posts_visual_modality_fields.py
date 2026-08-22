"""posts visual modality fields

Revision ID: e42222234662
Revises: c1ddf335e163
Create Date: 2026-08-21 18:01:23.989508

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e42222234662'
down_revision: Union[str, None] = 'c1ddf335e163'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('posts', sa.Column('thumbnail_url', sa.Text(), nullable=True))
    op.add_column('posts', sa.Column('visual_description', sa.Text(), nullable=True))
    op.add_column('posts', sa.Column('visual_model', sa.String(), nullable=True))
    op.add_column('posts', sa.Column('visual_generated_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('posts', 'visual_generated_at')
    op.drop_column('posts', 'visual_model')
    op.drop_column('posts', 'visual_description')
    op.drop_column('posts', 'thumbnail_url')
