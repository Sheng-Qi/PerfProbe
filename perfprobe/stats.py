from __future__ import annotations

from statistics import mean, median
from typing import Iterable


def summarize_throughput(values: Iterable[float]) -> dict[str, float | int | None]:
    """Return robust summary metrics for throughput-like values."""
    series = [float(v) for v in values if v is not None]
    if not series:
        return {
            "best": None,
            "median": None,
            "avg": None,
            "min": None,
            "max": None,
            "round_count": 0,
        }

    best = max(series)
    return {
        "best": best,
        "median": median(series),
        "avg": mean(series),
        "min": min(series),
        "max": max(series),
        "round_count": len(series),
    }
