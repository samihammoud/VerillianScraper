import datetime
import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import ARRAY, ForeignKey, Float, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("platform", "external_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    platform: Mapped[str] = mapped_column(nullable=False)
    external_id: Mapped[str] = mapped_column(nullable=False)
    handle: Mapped[str] = mapped_column(nullable=False)
    follower_count: Mapped[int | None] = mapped_column(nullable=True)

    posts: Mapped[list["Post"]] = relationship(back_populates="account")


class Post(Base):
    __tablename__ = "posts"
    __table_args__ = (
        UniqueConstraint("account_id", "external_id"),
        # Partial indexes = the enrich claim queries. No queue table: "needs work"
        # is a visible fact about the row (comments/visual_description IS NULL),
        # and these indexes make that predicate cheap at bulk volume.
        Index("ix_posts_comment_attempts_pending", "comment_attempts", postgresql_where=text("comments IS NULL")),
        Index(
            "ix_posts_visual_attempts_pending",
            "visual_attempts",
            postgresql_where=text("visual_description IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    external_id: Mapped[str] = mapped_column(nullable=False)
    caption: Mapped[str | None] = mapped_column(nullable=True)
    media_urls: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    posted_at: Mapped[datetime.datetime | None] = mapped_column(nullable=True)
    likes: Mapped[int | None] = mapped_column(nullable=True)
    # none_as_null=True: without it, SQLAlchemy encodes a Python None bind value as
    # the JSON null *literal*, not SQL NULL — invisible via the ORM (which decodes
    # JSON null back to Python None on read) but breaks any raw `IS NULL` query,
    # including the enrich claim query this column exists for.
    comments: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    shares: Mapped[int | None] = mapped_column(nullable=True)
    views: Mapped[int | None] = mapped_column(nullable=True)
    comment_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    # Visual modality: thumbnail_url is the raw scraped URL (goes stale in hours),
    # kept only for display. The VLM describes the full downloaded video (see
    # video_storage.py), not a cover frame — cover_key/cover_status are legacy
    # columns from when it did; nothing writes them anymore. visual_generated_at
    # means "when it succeeded" — it is NOT stamped on failure, so a failed
    # attempt can be retried up to visual_attempts < 3 rather than being
    # silently permanent.
    thumbnail_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_status: Mapped[str | None] = mapped_column(String, nullable=True)  # legacy, unused
    visual_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # vlm_json is the structured extraction; visual_description is a prose
    # rendering of it (vision_schema.flatten_for_blob). Routing reads only the
    # prose, so nothing downstream has to learn about this column.
    vlm_json: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    visual_model: Mapped[str | None] = mapped_column(String, nullable=True)
    visual_generated_at: Mapped[datetime.datetime | None] = mapped_column(nullable=True)
    visual_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    # world_slug of the crawl_query that discovered this post's account, not a
    # routing result — routing (topology.world_posts) can disagree with this.
    # Kept for later comparison of query-intent vs. actual routed world.
    discovered_by_world: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The specific crawl_queries row, not just its world — a real FK, safe
    # unlike one to topology.worlds, since crawl_queries is never truncated/
    # reseeded. Lets a query's yield (handles_found) be connected to its
    # actual downstream outcome (content quality/engagement), not just volume.
    # NULL for posts ingested outside the crawl loop.
    discovered_by_query_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crawl_queries.id"), nullable=True
    )

    # From TikTok's own music_info metadata, not the VLM — the VLM can't
    # identify a song from watching a clip, but the scrape already says
    # exactly which track and whether it's original vs. licensed/trending.
    # music_author is only meaningful when music_original is False; an
    # original sound has no separate "artist".
    music_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    music_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    music_author: Mapped[str | None] = mapped_column(Text, nullable=True)
    music_original: Mapped[bool | None] = mapped_column(nullable=True)

    account: Mapped["Account"] = relationship(back_populates="posts")


class CrawlQuery(Base):
    """The crawl ledger: both the work list and the query generator's memory.

    handles_found / new_handles are the only feedback the generator gets, so
    they are written even when a query yields nothing — a zero row is the
    strongest signal it has (that vein is dead, stop mining it).
    """

    __tablename__ = "crawl_queries"
    __table_args__ = (Index("ix_crawl_queries_status", "world_slug", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Slug rather than an FK to topology.worlds: reseed-worlds regenerates those
    # UUIDs, and the ledger has to survive a reseed. Every read of this table is
    # scoped by it — an unscoped ledger would hand one world's generator another
    # world's history to reason from, which is exactly what the ledger is for.
    world_slug: Mapped[str] = mapped_column(Text, nullable=False)
    round_no: Mapped[int] = mapped_column(Integer, nullable=False)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    intent: Mapped[str] = mapped_column(Text, nullable=False)  # seed | exploit | explore
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    handles_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    new_handles: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    status: Mapped[str] = mapped_column(Text, nullable=False)  # pending | done
    executed_at: Mapped[datetime.datetime | None] = mapped_column(nullable=True)


class CrawlPromptLog(Base):
    """One row per query_gen.generate() call — the exact prompt sent to the LLM.

    _evidence() is cumulative over vlm_json, so re-building the prompt later
    reflects today's data, not what the model actually saw at generation time
    (same reasoning as WorldPost.blob_text). Immutable once written.
    """

    __tablename__ = "crawl_prompt_logs"

    world_slug: Mapped[str] = mapped_column(Text, primary_key=True)
    round_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    system_instruction: Mapped[str] = mapped_column(Text, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    generated_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.utcnow)


class World(Base):
    """Manually seeded, static reference set — 8 hand-written world definitions."""

    __tablename__ = "worlds"
    __table_args__ = {"schema": "topology"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(String)
    example_snippets: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    reference_embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)
    embed_model: Mapped[str] = mapped_column(String, default="text-embedding-3-small")
    created_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.utcnow)


class WorldPost(Base):
    """One row per routed post — a post's embedding plus which world it was assigned to.

    Unique on post_id + upsert on write: routing is idempotent and re-runnable —
    a re-route (e.g. after a blob-assembly change) updates the existing row rather
    than duplicating it.
    """

    __tablename__ = "world_posts"
    __table_args__ = (UniqueConstraint("post_id"), {"schema": "topology"})

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    post_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    world_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("topology.worlds.id"))
    embedding: Mapped[list[float]] = mapped_column(Vector(1536))
    posted_at: Mapped[datetime.datetime | None] = mapped_column(nullable=True)

    # blob_text is the exact string that was embedded — can't be rebuilt later since
    # comments keep accruing after the scrape. cosine/margin: all 8 cosines are already
    # computed for the argmax, margin is what the (future) abstain threshold keys off.
    blob_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    cosine: Mapped[float | None] = mapped_column(Float, nullable=True)
    margin: Mapped[float | None] = mapped_column(Float, nullable=True)


class PostTerm(Base):
    """One row per (post, facet, term) edge — fully derived from vlm_json,
    safe to truncate and recompute. Exists (rather than aggregating vlm_json
    in one query) so a ranked term can be joined back to the real posts
    behind it for drill-down."""

    __tablename__ = "post_terms"
    __table_args__ = (Index("ix_post_terms_world_facet_canon2", "world_id", "facet", "canon_term"), {"schema": "topology"})

    post_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    world_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("topology.worlds.id"), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    facet: Mapped[str] = mapped_column(Text, primary_key=True)
    raw_term: Mapped[str] = mapped_column(Text, primary_key=True)
    norm_term: Mapped[str] = mapped_column(Text, nullable=False)
    canon_term: Mapped[str] = mapped_column(Text, nullable=False)
    prominence: Mapped[str | None] = mapped_column(Text, nullable=True)
    metric: Mapped[float | None] = mapped_column(Float, nullable=True)
    low_conf: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")


class WorldTermStat(Base):
    """The served rollup: one row per ranked term. Cache table — deleted and
    reinserted wholesale per world on every `make overview`, never
    incrementally updated."""

    __tablename__ = "world_term_stats"
    __table_args__ = {"schema": "topology"}

    world_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("topology.worlds.id"), primary_key=True)
    facet: Mapped[str] = mapped_column(Text, primary_key=True)
    canon_term: Mapped[str] = mapped_column(Text, primary_key=True)
    n_posts: Mapped[int] = mapped_column(Integer, nullable=False)
    n_accounts: Mapped[int] = mapped_column(Integer, nullable=False)
    n_hero: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    median_m: Mapped[float] = mapped_column(Float, nullable=False)
    lift: Mapped[float] = mapped_column(Float, nullable=False)
    view_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    account_lift: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    account_view_ratio: Mapped[float] = mapped_column(Float, nullable=False, default=1.0, server_default="1")
    variants: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    computed_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.utcnow)


class TermEmbedding(Base):
    """Embedding cache for canonicalization, keyed by the normalized string —
    a rerun embeds only norm_terms seen for the first time."""

    __tablename__ = "term_embeddings"
    __table_args__ = {"schema": "topology"}

    norm_term: Mapped[str] = mapped_column(Text, primary_key=True)
    facet: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(1536), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.utcnow)


class ClusterProfile(Base):
    """Phase 9 step 5 (CLAUDEphase9romanceoverview.md) — the per-cluster
    cross-examination table. Cache table like WorldTermStat: deleted and
    reinserted wholesale per (world, facet) every time it's recomputed, keyed
    on the same canon_term the cluster it describes already uses (so a
    premise cluster's profile row and its world_term_stats row share a key)."""

    __tablename__ = "cluster_profiles"
    __table_args__ = {"schema": "topology"}

    world_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("topology.worlds.id"), primary_key=True)
    facet: Mapped[str] = mapped_column(Text, primary_key=True)
    canon_term: Mapped[str] = mapped_column(Text, primary_key=True)
    n_posts: Mapped[int] = mapped_column(Integer, nullable=False)
    median_duration_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    median_dialogue_turns: Mapped[float | None] = mapped_column(Float, nullable=True)
    enum_distribution: Mapped[dict] = mapped_column(JSONB(none_as_null=True), nullable=False, default=dict)
    enum_view_ratio: Mapped[dict] = mapped_column(JSONB(none_as_null=True), nullable=False, default=dict)
    modal_register: Mapped[str | None] = mapped_column(Text, nullable=True)
    highest_lift_register: Mapped[str | None] = mapped_column(Text, nullable=True)
    register_mismatch: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    computed_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.utcnow)
