"""Scene estimates: the horizon line (dominant sky/ground edge)."""

from __future__ import annotations

import numpy as np


def estimate_horizon(gray: np.ndarray) -> float | None:
    """Normalized y of the strongest bright-above/dark-below row transition, or None."""
    h = gray.shape[0]
    rows = gray.astype(np.float32).mean(axis=1)
    win = max(4, h // 60)
    best_y, best_c = None, 0.0
    for y in range(int(h * 0.12), int(h * 0.85)):
        above = rows[max(0, y - win) : y].mean()
        below = rows[y : y + win].mean()
        c = float(above - below)
        if c > best_c:
            best_y, best_c = y, c
    if best_y is None or best_c < 12.0:
        return None
    return float(best_y / h)
