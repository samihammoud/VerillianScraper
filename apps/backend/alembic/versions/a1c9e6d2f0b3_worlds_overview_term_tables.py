"""worlds overview: post_terms, world_term_stats, term_embeddings

Revision ID: a1c9e6d2f0b3
Revises: c5e2a83f1d47
Create Date: 2026-08-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'a1c9e6d2f0b3'
down_revision: Union[str, None] = 'c5e2a83f1d47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "post_terms",
        sa.Column("post_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("world_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("topology.worlds.id"), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("facet", sa.Text(), nullable=False),
        sa.Column("raw_term", sa.Text(), nullable=False),
        sa.Column("norm_term", sa.Text(), nullable=False),
        sa.Column("canon_term", sa.Text(), nullable=False),
        sa.Column("prominence", sa.Text(), nullable=True),
        sa.Column("metric", sa.Float(), nullable=True),
        sa.Column("low_conf", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.PrimaryKeyConstraint("post_id", "facet", "raw_term"),
        schema="topology",
    )
    op.create_index(
        "ix_post_terms_world_facet_canon",
        "post_terms",
        ["world_id", "facet", "canon_term"],
        schema="topology",
    )

    op.create_table(
        "world_term_stats",
        sa.Column("world_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("topology.worlds.id"), nullable=False),
        sa.Column("facet", sa.Text(), nullable=False),
        sa.Column("canon_term", sa.Text(), nullable=False),
        sa.Column("n_posts", sa.Integer(), nullable=False),
        sa.Column("n_accounts", sa.Integer(), nullable=False),
        sa.Column("n_hero", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("median_m", sa.Float(), nullable=False),
        sa.Column("lift", sa.Float(), nullable=False),
        sa.Column("view_ratio", sa.Float(), nullable=False),
        sa.Column("variants", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("world_id", "facet", "canon_term"),
        schema="topology",
    )

    op.create_table(
        "term_embeddings",
        sa.Column("norm_term", sa.Text(), primary_key=True),
        sa.Column("facet", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="topology",
    )


def downgrade() -> None:
    op.drop_table("term_embeddings", schema="topology")
    op.drop_table("world_term_stats", schema="topology")
    op.drop_table("post_terms", schema="topology")
