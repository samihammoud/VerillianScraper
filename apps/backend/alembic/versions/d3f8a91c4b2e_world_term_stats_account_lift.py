"""world_term_stats: account-relative lift columns

Revision ID: d3f8a91c4b2e
Revises: a1c9e6d2f0b3
Create Date: 2026-09-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd3f8a91c4b2e'
down_revision: Union[str, None] = 'a1c9e6d2f0b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "world_term_stats",
        sa.Column("account_lift", sa.Float(), nullable=False, server_default="0"),
        schema="topology",
    )
    op.add_column(
        "world_term_stats",
        sa.Column("account_view_ratio", sa.Float(), nullable=False, server_default="1"),
        schema="topology",
    )


def downgrade() -> None:
    op.drop_column("world_term_stats", "account_view_ratio", schema="topology")
    op.drop_column("world_term_stats", "account_lift", schema="topology")
