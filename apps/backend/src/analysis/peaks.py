"""Stage 4a: find the posts that spike relative to one account's own post history.

Views only, not a blended engagement score. View count is TikTok's own reach
verdict — allocated by the algorithm from early watch-time and interaction
rate — so computing our own likes/comments/shares blend would just re-derive
that, worse, from a thinner sample. The usual reason to normalize by views or
followers is to strip out account size; that's moot here, since every
comparison is an account against its own history, never against another
account.

Known cost: views can't separate reach from resonance. Rage-bait and
trending-audio posts will enter the peak set with PRODUCTS: none visible.
That's expected, not a bug — the caller reports the rate.
"""

from typing import NamedTuple

import numpy as np

from src.db.models import Post

MIN_POSTS_FOR_MAD = 10  # below this, MAD is too noisy to trust — fall back to top 10%
MODIFIED_Z_THRESHOLD = 3.5  # Iglewicz & Hoaglin's standard modified-z outlier cutoff
TOP_PERCENT_FALLBACK = 0.10


class Peak(NamedTuple):
    post: Post
    metric: float
    modified_z: float | None  # None under the top-10% fallback, which has no z-score


def compute_metrics(posts: list) -> list[tuple]:
    """(post, metric) pairs for posts with a usable view count — nulls and
    zeros skipped, not treated as 0.

    log1p before any statistics: view counts are heavy-tailed. On raw counts,
    one 10M-view post inflates the spread enough to hide itself — mean + 1
    stdev lands above the very outlier that produced the stdev.
    """
    return [(post, float(np.log1p(post.views))) for post in posts if post.views]


def find_peaks(posts_with_metrics: list[tuple]) -> tuple[list[Peak], dict]:
    """Returns (peaks, info). info carries the method used and the cutoff
    translated back to raw views, since a z-score threshold alone isn't
    legible to a human reading the output.
    """
    if not posts_with_metrics:
        return [], {"method": "none", "cutoff_views": None, "n_total": 0, "n_peaks": 0}

    metrics = np.array([metric for _, metric in posts_with_metrics])
    median = float(np.median(metrics))
    mad = float(np.median(np.abs(metrics - median)))

    if mad == 0 or len(posts_with_metrics) < MIN_POSTS_FOR_MAD:
        # MAD undefined/unreliable (too few posts, or the metric is degenerate) —
        # fall back to a plain top-N% cut rather than a modified z-score.
        n_peaks = max(1, int(np.ceil(len(posts_with_metrics) * TOP_PERCENT_FALLBACK)))
        ordered = sorted(posts_with_metrics, key=lambda pair: pair[1], reverse=True)
        chosen = ordered[:n_peaks]
        cutoff_metric = chosen[-1][1]
        method = "top_10_percent"
        peaks = [Peak(post, metric, None) for post, metric in chosen]
    else:
        # Median + MAD, not mean + stdev: stdev is defined by the very outliers
        # being hunted, so a large spike inflates the threshold meant to catch it.
        # MAD isn't self-referential that way.
        modified_z = 0.6745 * (metrics - median) / mad
        peaks = [
            Peak(post, metric, float(z))
            for (post, metric), z in zip(posts_with_metrics, modified_z)
            if z >= MODIFIED_Z_THRESHOLD
        ]
        cutoff_metric = (MODIFIED_Z_THRESHOLD * mad / 0.6745) + median
        method = "modified_z_score"

    return peaks, {
        "method": method,
        "cutoff_views": int(np.expm1(cutoff_metric)),
        "n_total": len(posts_with_metrics),
        "n_peaks": len(peaks),
    }
