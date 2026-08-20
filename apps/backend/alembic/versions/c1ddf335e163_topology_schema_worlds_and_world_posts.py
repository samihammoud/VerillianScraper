"""topology schema: worlds and world_posts

Revision ID: c1ddf335e163
Revises: 1a32630dc678
Create Date: 2026-08-20 14:51:06.219829

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c1ddf335e163'
down_revision: Union[str, None] = '1a32630dc678'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS topology")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "worlds",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(), nullable=False, unique=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("example_snippets", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column("reference_embedding", Vector(1536), nullable=True),
        sa.Column("embed_model", sa.String(), nullable=False, server_default="text-embedding-3-small"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        schema="topology",
    )

    op.create_table(
        "world_posts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("post_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("world_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("topology.worlds.id"), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=False),
        sa.Column("engagement", sa.Float(), nullable=True),
        sa.Column("posted_at", sa.DateTime(), nullable=True),
        schema="topology",
    )


def downgrade() -> None:
    op.drop_table("world_posts", schema="topology")
    op.drop_table("worlds", schema="topology")
    op.execute("DROP SCHEMA IF EXISTS topology CASCADE")
