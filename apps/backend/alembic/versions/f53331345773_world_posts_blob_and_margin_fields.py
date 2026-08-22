"""world_posts blob text, cosine, and margin fields

Revision ID: f53331345773
Revises: e42222234662
Create Date: 2026-08-21 18:01:24.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f53331345773'
down_revision: Union[str, None] = 'e42222234662'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('world_posts', sa.Column('blob_text', sa.Text(), nullable=True), schema='topology')
    op.add_column('world_posts', sa.Column('cosine', sa.Float(), nullable=True), schema='topology')
    op.add_column('world_posts', sa.Column('margin', sa.Float(), nullable=True), schema='topology')


def downgrade() -> None:
    op.drop_column('world_posts', 'margin', schema='topology')
    op.drop_column('world_posts', 'cosine', schema='topology')
    op.drop_column('world_posts', 'blob_text', schema='topology')
