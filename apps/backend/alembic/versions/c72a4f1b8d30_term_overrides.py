"""term_merges -> term_overrides (+action/target_term) and world_term_stats.parent_term

Phase 16 v2: merge was the only decision v1 could express. A characteristic
("paint stripping") isn't a synonym of its parent and a vague umbrella term
("free discarded item") isn't a synonym of anything, so the vocabulary widens
to keep/merge/child_of/drop. keep is stored as absence.

Revision ID: c72a4f1b8d30
Revises: b41e7c0d9a52
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c72a4f1b8d30"
down_revision: Union[str, None] = "b41e7c0d9a52"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.rename_table("term_merges", "term_overrides", schema="topology")
    op.alter_column("term_overrides", "canon_term", new_column_name="target_term", schema="topology")
    # Existing rows predate the column and are all merges by construction.
    op.add_column(
        "term_overrides",
        sa.Column("action", sa.Text(), nullable=False, server_default="merge"),
        schema="topology",
    )
    # NULL target_term is only legal for action='drop'.
    op.alter_column("term_overrides", "target_term", nullable=True, schema="topology")
    op.add_column("world_term_stats", sa.Column("parent_term", sa.Text(), nullable=True), schema="topology")


def downgrade() -> None:
    op.drop_column("world_term_stats", "parent_term", schema="topology")
    op.execute("DELETE FROM topology.term_overrides WHERE action <> 'merge' OR target_term IS NULL")
    op.drop_column("term_overrides", "action", schema="topology")
    op.alter_column("term_overrides", "target_term", nullable=False, schema="topology")
    op.alter_column("term_overrides", "target_term", new_column_name="canon_term", schema="topology")
    op.rename_table("term_overrides", "term_merges", schema="topology")
