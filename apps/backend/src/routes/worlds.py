"""Phase 7's three read endpoints — the real consumer FastAPI was waiting for.

One write endpoint since: PUT /worlds/{slug}/patterns/{key} stores the UI's
note / hidden / manual order for one account peak pattern (topology.pattern_notes).

Otherwise read-only over topology.world_term_stats/post_terms, written by `make overview`
(src/scripts/run_overview.py). Nothing here recomputes a ranking; a stale
world just means `make overview` hasn't been rerun since the last `make route`.
"""

import datetime

import numpy as np
from fastapi import APIRouter, Body, HTTPException, Query
from sqlalchemy import func, select

from src.db.models import ANNOTATION_FIELDS, Account, Annotation, Post, PostTerm, World, WorldPost, WorldTermStat
from src.db.session import SessionLocal
from src.services.account_patterns import account_patterns
from src.services.overview import MIN_ACCOUNTS, MIN_POSTS, world_median

router = APIRouter(prefix="/worlds", tags=["worlds"])


def _get_world(db, slug: str) -> World:
    world = db.execute(select(World).where(World.slug == slug)).scalar_one_or_none()
    if world is None:
        raise HTTPException(404, f"no world {slug!r}")
    return world


@router.get("")
def list_worlds() -> list[dict]:
    db = SessionLocal()
    try:
        worlds = db.execute(select(World).order_by(World.slug)).scalars().all()
        out = []
        for world in worlds:
            n_posts, n_accounts = db.execute(
                select(func.count(WorldPost.post_id), func.count(func.distinct(WorldPost.account_id))).where(
                    WorldPost.world_id == world.id
                )
            ).one()
            n_with_vlm = db.execute(
                select(func.count())
                .select_from(WorldPost)
                .join(Post, Post.id == WorldPost.post_id)
                .where(WorldPost.world_id == world.id, Post.vlm_json.isnot(None))
            ).scalar()
            last_computed_at = db.execute(
                select(func.max(WorldTermStat.computed_at)).where(WorldTermStat.world_id == world.id)
            ).scalar()
            out.append(
                {
                    "slug": world.slug,
                    "name": world.name,
                    "n_posts": n_posts,
                    "n_accounts": n_accounts,
                    "vlm_coverage": (n_with_vlm / n_posts) if n_posts else 0.0,
                    "last_computed_at": last_computed_at,
                }
            )
        return out
    finally:
        db.close()


@router.get("/{slug}/overview")
def world_overview(
    slug: str,
    facet: str = Query(...),
    sort: str = Query("lift", pattern="^(lift|volume|account|variants)$"),
    min_posts: int = Query(MIN_POSTS, ge=1),
    max_posts: int | None = Query(None, ge=1),
    min_accounts: int = Query(MIN_ACCOUNTS, ge=1),
    limit: int = Query(25, ge=1, le=200),
    offset: int = Query(0, ge=0),
    favorites_only: bool = Query(False),
) -> dict:
    db = SessionLocal()
    try:
        world = _get_world(db, slug)

        n_posts, n_accounts = db.execute(
            select(func.count(WorldPost.post_id), func.count(func.distinct(WorldPost.account_id))).where(
                WorldPost.world_id == world.id
            )
        ).one()

        vlm_jsons = db.execute(
            select(Post.vlm_json)
            .select_from(WorldPost)
            .join(Post, Post.id == WorldPost.post_id)
            .where(WorldPost.world_id == world.id, Post.vlm_json.isnot(None))
        ).scalars().all()
        n_with_vlm = len(vlm_jsons)
        n_low_conf_excluded = sum(1 for v in vlm_jsons if (v or {}).get("confidence") == "low")
        n_none_visible = sum(1 for v in vlm_jsons if not (v or {}).get("products"))
        products_none_visible_rate = (n_none_visible / n_with_vlm) if n_with_vlm else 0.0

        computed_at = db.execute(
            select(func.max(WorldTermStat.computed_at)).where(WorldTermStat.world_id == world.id)
        ).scalar()

        median = world_median(db, world)

        stmt = select(WorldTermStat).where(WorldTermStat.world_id == world.id, WorldTermStat.facet == facet)
        # volume_ratio's denominator stays the whole facet's median, computed
        # before any support filter narrows the set — otherwise filtering to the small
        # clusters would rescale every ratio against the small clusters and a
        # term's number would change meaning depending on the active filter.
        # min_posts used to be applied above this line, which exempted it from that
        # rule: it was a no-op only while the UI left it at MIN_POSTS, and became a
        # silent rescale of every ratio in the app once the default floor moved to 6.
        facet_n_posts = [row.n_posts for row in db.execute(stmt).scalars()]
        median_n_posts = float(np.median(facet_n_posts)) if facet_n_posts else 0.0

        # Support floors are read-time, not pipeline constants: a 3-post term still
        # reaches world_term_stats (MIN_POSTS was deliberately lowered to 3 for recall),
        # it just isn't ranked by default. Measured on discount-shopping's 13,657 posts
        # via split-half replication: at 3 posts/3 accounts a term's lift agrees on
        # direction across halves only 65% of the time (vs. a 50% coin flip), 70% at
        # 6/4. Shuffling account labels admits MORE terms than the real data at every
        # floor, so n_accounts is a concentration check, not evidence of convergence.
        stmt = stmt.where(WorldTermStat.n_posts >= min_posts, WorldTermStat.n_accounts >= min_accounts)
        if max_posts is not None:
            stmt = stmt.where(WorldTermStat.n_posts <= max_posts)

        if sort == "variants":
            # How many raw terms the cluster absorbed. Surfaces over-merged
            # clusters for review — single-link chaining pulls 50+ loosely
            # related phrasings under one label (see overview.py's note).
            order = func.cardinality(WorldTermStat.variants).desc()
        elif sort == "volume":
            order = WorldTermStat.n_posts.desc()
        elif sort == "account":
            order = WorldTermStat.account_lift.desc()
        else:
            order = WorldTermStat.lift.desc()

        # Annotations are joined and ordered in SQL, not applied client-side after
        # the limit — a starred term that has since fallen out of the top `limit`
        # by lift must still come back, which a post-hoc sort of the page can't do.
        annotated = stmt.add_columns(Annotation).outerjoin(
            Annotation, Annotation.key == func.concat(f"{slug}:{facet}:", WorldTermStat.canon_term)
        ).order_by(
            func.coalesce(Annotation.favorite, False).desc(),
            func.coalesce(Annotation.sort_order, 0),
            order,
            # Total order, required for offset paging to be correct: lift/n_posts
            # tie constantly at the low-volume end (dozens of terms share 3 posts),
            # and Postgres is free to return tied rows in a different order per
            # query — which silently duplicates some across page boundaries and
            # drops others entirely.
            WorldTermStat.canon_term.asc(),
        )
        if favorites_only:
            # Filtered in SQL for the same reason it's ordered there: a starred term
            # below the top `limit` by lift must still appear in the favourites view.
            annotated = annotated.where(Annotation.favorite.is_(True))
        total = db.execute(select(func.count()).select_from(annotated.subquery())).scalar_one()
        term_rows = db.execute(annotated.limit(limit).offset(offset)).all()

        return {
            "coverage": {
                "n_posts": n_posts,
                "n_with_vlm": n_with_vlm,
                "n_low_conf_excluded": n_low_conf_excluded,
                "products_none_visible_rate": products_none_visible_rate,
                "n_accounts": n_accounts,
                "world_median_views": float(np.expm1(median)),
                "computed_at": computed_at,
            },
            # Row count for the whole ordered set, so a paging client knows how
            # far it has left to go without fetching to find out.
            "total": total,
            "offset": offset,
            "terms": [
                {
                    "key": term_key(slug, facet, t.canon_term),
                    "canon_term": t.canon_term,
                    "n_posts": t.n_posts,
                    "n_accounts": t.n_accounts,
                    "n_hero": t.n_hero,
                    "view_ratio": t.view_ratio,
                    "lift": t.lift,
                    "volume_ratio": (t.n_posts / median_n_posts) if median_n_posts else 0.0,
                    "account_view_ratio": t.account_view_ratio,
                    "account_lift": t.account_lift,
                    "variants": t.variants,
                    "note": a.note if a else "",
                    "favorite": bool(a and a.favorite),
                    "reviewed": bool(a and a.reviewed),
                    "hidden": bool(a and a.hidden),
                    "sort_order": a.sort_order if a else 0,
                }
                for t, a in term_rows
            ],
        }
    finally:
        db.close()


@router.get("/{slug}/accounts")
def world_accounts(slug: str) -> list[dict]:
    db = SessionLocal()
    try:
        world = _get_world(db, slug)
        return account_patterns(db, world)
    finally:
        db.close()


def term_key(slug: str, facet: str, canon_term: str) -> str:
    """The annotation key for one term cluster. Terms have no stored id — the
    world_term_stats row is rebuilt wholesale every `make overview` — so the
    (world, facet, canon_term) triple is the stable handle."""
    return f"{slug}:{facet}:{canon_term}"


@router.put("/annotations/{key:path}")
def set_annotation(key: str, body: dict = Body(...)) -> dict:
    """Upsert-merge of one annotation — only the keys present in the body are
    touched, so the UI can send one field at a time (note edit, star, review
    check, reorder) without round-tripping the others. Shared by the account
    patterns view and the terms view; `key` is whatever that view uses (a post
    uuid, or term_key()'s slug:facet:term triple), url-encoded."""
    db = SessionLocal()
    try:
        row = db.get(Annotation, key) or Annotation(key=key)
        for field in ANNOTATION_FIELDS:
            if field in body:
                setattr(row, field, body[field])
        row.updated_at = datetime.datetime.utcnow()
        row = db.merge(row)
        db.commit()
        return {"key": key} | {f: getattr(row, f) for f in ANNOTATION_FIELDS}
    finally:
        db.close()


@router.get("/{slug}/terms/{facet}/{canon_term}/posts")
def term_posts(slug: str, facet: str, canon_term: str, limit: int = Query(20, ge=1, le=100)) -> list[dict]:
    db = SessionLocal()
    try:
        world = _get_world(db, slug)

        rows = db.execute(
            select(Post, Account.handle, PostTerm.raw_term)
            .join(PostTerm, PostTerm.post_id == Post.id)
            .join(Account, Account.id == Post.account_id)
            .where(PostTerm.world_id == world.id, PostTerm.facet == facet, PostTerm.canon_term == canon_term)
        ).all()

        # A post can carry two raw_terms that clustered into the same canon_term
        # (two distinctly-phrased mentions in one video), so dedupe by post id
        # rather than trusting the join to be 1:1 — keeps whichever raw_term the
        # join happens to return first, which is fine since for the single-value
        # embedding-cluster facets (hook/premise/punchline) there's only ever one.
        by_post_id = {post.id: (post, handle, raw_term) for post, handle, raw_term in rows}
        ordered = sorted(by_post_id.values(), key=lambda phr: -(phr[0].views or 0))[:limit]

        return [
            {
                "handle": handle,
                "video_url": f"https://www.tiktok.com/@{handle}/video/{post.external_id}",
                "thumbnail_url": post.thumbnail_url,
                "caption": post.caption,
                "views": post.views,
                "posted_at": post.posted_at.isoformat() if post.posted_at else None,
                "format": (post.vlm_json or {}).get("format"),
                "summary": (post.vlm_json or {}).get("summary"),
                # The actual field that put this post under this canon_term — for a
                # short-phrase facet (product/topic/hook/...) this is what to check
                # the term ranking against, not caption/summary (unrelated fields
                # that made "why is this video under this hook" look like a bug when
                # it wasn't — the raw_term just was never shown).
                "matched_term": raw_term,
            }
            for post, handle, raw_term in ordered
        ]
    finally:
        db.close()
