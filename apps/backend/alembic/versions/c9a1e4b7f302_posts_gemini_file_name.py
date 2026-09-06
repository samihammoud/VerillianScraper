"""posts.gemini_file_name — Gemini Files API registration state

Phase 10: video bytes moved to GCS and are registered with Gemini by URI
instead of uploaded. This column replaces the out/gemini_uploads.txt ledger as
the record of "has this post's video been registered", so registration is
resumable by a plain claim query after a crash.

Partial index mirrors the pass-A claim predicate (gemini_file_name IS NULL),
same "no queue table, the row itself says whether it needs work" pattern as
the existing comment/visual attempt indexes.

Revision ID: c9a1e4b7f302
Revises: b7d4f92a1c6e
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c9a1e4b7f302"
down_revision: Union[str, None] = "b7d4f92a1c6e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("posts", sa.Column("gemini_file_name", sa.Text(), nullable=True))
    op.create_index(
        "ix_posts_unregistered",
        "posts",
        ["visual_attempts"],
        postgresql_where=sa.text("gemini_file_name IS NULL AND vlm_json IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_posts_unregistered", table_name="posts")
    op.drop_column("posts", "gemini_file_name")
