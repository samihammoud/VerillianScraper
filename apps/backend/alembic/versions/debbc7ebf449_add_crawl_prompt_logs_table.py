"""add crawl_prompt_logs table

Revision ID: debbc7ebf449
Revises: a7d4f1c9b3e2
Create Date: 2026-09-02 13:14:18.391737

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'debbc7ebf449'
down_revision: Union[str, None] = 'a7d4f1c9b3e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('crawl_prompt_logs',
    sa.Column('world_slug', sa.Text(), nullable=False),
    sa.Column('round_no', sa.Integer(), nullable=False),
    sa.Column('system_instruction', sa.Text(), nullable=False),
    sa.Column('prompt', sa.Text(), nullable=False),
    sa.Column('model', sa.Text(), nullable=False),
    sa.Column('generated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('world_slug', 'round_no')
    )


def downgrade() -> None:
    op.drop_table('crawl_prompt_logs')
