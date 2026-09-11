"""topology.annotations — notes/favorite/reviewed/hide/order, keyed by text

Replaces topology.pattern_notes (uuid-keyed, patterns only) with one table both
the account-pattern view and the terms view annotate against. pattern_notes was
empty, so there is nothing to migrate.

Revision ID: a7c1d9e4f210
Revises: d5b3e08a71cc
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a7c1d9e4f210"
down_revision: Union[str, None] = "d5b3e08a71cc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "annotations",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("favorite", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("reviewed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("hidden", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        schema="topology",
    )
    op.drop_table("pattern_notes", schema="topology")


def downgrade() -> None:
    op.create_table(
        "pattern_notes",
        sa.Column("anchor_post_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("hidden", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["anchor_post_id"], ["posts.id"], ondelete="CASCADE"),
        schema="topology",
    )
    op.drop_table("annotations", schema="topology")
