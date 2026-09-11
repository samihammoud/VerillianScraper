"""topology.pattern_notes — notes/hide/order on account peak patterns

Account patterns are computed per request (account_patterns.py) and have no
identity of their own; this table hangs UI annotations off the cluster's
smallest member post id.

Revision ID: d5b3e08a71cc
Revises: c9a1e4b7f302
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d5b3e08a71cc"
down_revision: Union[str, None] = "c9a1e4b7f302"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_table("pattern_notes", schema="topology")
