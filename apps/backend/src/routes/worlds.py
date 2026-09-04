"""Phase 7's three read endpoints — the real consumer FastAPI was waiting for.

Read-only over topology.world_term_stats/post_terms, written by `make overview`
(src/scripts/run_overview.py). Nothing here recomputes a ranking; a stale
world just means `make overview` hasn't been rerun since the last `make route`.
"""

import numpy as np
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from src.db.models import Account, Post, PostTerm, World, WorldPost, WorldTermStat
from src.db.session import SessionLocal
from src.services.account_patterns import account_patterns
from src.services.overview import MIN_POSTS, world_median

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
    sort: str = Query("lift", pattern="^(lift|volume|account)$"),
    min_posts: int = Query(MIN_POSTS, ge=1),
    limit: int = Query(25, ge=1, le=200),
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

        stmt = select(WorldTermStat).where(
            WorldTermStat.world_id == world.id, WorldTermStat.facet == facet, WorldTermStat.n_posts >= min_posts
        )
        facet_n_posts = [row.n_posts for row in db.execute(stmt).scalars()]
        median_n_posts = float(np.median(facet_n_posts)) if facet_n_posts else 0.0

        if sort == "volume":
            order = WorldTermStat.n_posts.desc()
        elif sort == "account":
            order = WorldTermStat.account_lift.desc()
        else:
            order = WorldTermStat.lift.desc()
        stmt = stmt.order_by(order)
        term_rows = db.execute(stmt.limit(limit)).scalars().all()

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
            "terms": [
                {
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
                }
                for t in term_rows
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
