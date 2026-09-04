"""phase 9 step 5: cluster_profiles table

Revision ID: b7d4f92a1c6e
Revises: debbc7ebf449
Create Date: 2026-09-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b7d4f92a1c6e'
down_revision: Union[str, None] = 'debbc7ebf449'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cluster_profiles",
        sa.Column("world_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("topology.worlds.id"), nullable=False),
        sa.Column("facet", sa.Text(), nullable=False),
        sa.Column("canon_term", sa.Text(), nullable=False),
        sa.Column("n_posts", sa.Integer(), nullable=False),
        sa.Column("median_duration_sec", sa.Float(), nullable=True),
        sa.Column("median_dialogue_turns", sa.Float(), nullable=True),
        # {"relationship_register": {"funny": 5, "venting": 3, ...}, "relationship_conflict": {...}, ...}
        sa.Column("enum_distribution", postgresql.JSONB(none_as_null=True), nullable=False, server_default="{}"),
        # {"relationship_register": {"funny": 1.8, "venting": -0.4, ...}, ...} — lift of each enum value
        # WITHIN this cluster, not against the world — this is what the fit/mismatch comparison reads.
        sa.Column("enum_view_ratio", postgresql.JSONB(none_as_null=True), nullable=False, server_default="{}"),
        sa.Column("modal_register", sa.Text(), nullable=True),
        sa.Column("highest_lift_register", sa.Text(), nullable=True),
        # True when modal_register != highest_lift_register for this cluster — a proven situation
        # being executed in a register that isn't its best one. See the doc's "Mismatch" case.
        sa.Column("register_mismatch", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("world_id", "facet", "canon_term"),
        schema="topology",
    )


def downgrade() -> None:
    op.drop_table("cluster_profiles", schema="topology")
