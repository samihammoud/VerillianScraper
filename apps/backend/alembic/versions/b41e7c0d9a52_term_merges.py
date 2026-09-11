"""topology.term_merges — phase 16 LLM merges applied on top of embedding clusters

Keyed on norm_term (a pure function of the string) rather than canon_term (a
cluster label that drifts as posts arrive). Read inside rollup(), never a
one-off UPDATE on post_terms — that table is rebuilt on every make overview.

Revision ID: b41e7c0d9a52
Revises: a7c1d9e4f210
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b41e7c0d9a52"
down_revision: Union[str, None] = "a7c1d9e4f210"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "term_merges",
        sa.Column("world_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("facet", sa.Text(), nullable=False),
        sa.Column("norm_term", sa.Text(), nullable=False),
        sa.Column("canon_term", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["world_id"], ["topology.worlds.id"]),
        sa.PrimaryKeyConstraint("world_id", "facet", "norm_term"),
        schema="topology",
    )


def downgrade() -> None:
    op.drop_table("term_merges", schema="topology")
