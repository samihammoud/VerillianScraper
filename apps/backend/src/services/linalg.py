"""Shared vector math for cosine-similarity scoring — used by routing.py
(posts vs. the 8 world references) and clustering.py (posts vs. each other).
Same normalize-then-matmul operation either way; kept in one place so a future
fix (e.g. guarding a zero-norm row) doesn't need to land in both call sites.
"""

import numpy as np


def l2_normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / norms
