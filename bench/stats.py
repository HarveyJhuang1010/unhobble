"""Descriptive statistics for the summary. Stdlib only."""
from __future__ import annotations

import math
import random


def quantile(ordered: list[float], q: float) -> float:
    """Linear interpolation between closest ranks (numpy's default)."""
    pos = (len(ordered) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def describe(values: list) -> tuple:
    """(n, median, q1, q3) over the values that are not None."""
    ordered = sorted(v for v in values if v is not None)
    if not ordered:
        return 0, None, None, None
    return len(ordered), quantile(ordered, 0.5), quantile(ordered, 0.25), quantile(ordered, 0.75)


def median(values: list[float]) -> float:
    return quantile(sorted(values), 0.5)


BOOTSTRAP_SEED = 20260915
BOOTSTRAP_RESAMPLES = 2000


def bootstrap_median_diff(before: list[float], after: list[float], seed: int = BOOTSTRAP_SEED,
                          resamples: int = BOOTSTRAP_RESAMPLES) -> tuple[float, float] | None:
    """Percentile 95% CI of median(after) - median(before), resampling each group independently."""
    if not before or not after:
        return None
    rng = random.Random(seed)
    diffs = sorted(
        median([rng.choice(after) for _ in after]) - median([rng.choice(before) for _ in before])
        for _ in range(resamples)
    )
    return quantile(diffs, 0.025), quantile(diffs, 0.975)


def fisher_exact(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact p for [[a, b], [c, d]]: sum of tables no more likely than the observed one."""
    row1, col1, n = a + b, a + c, a + b + c + d

    def probability(x: int) -> float:
        return math.comb(col1, x) * math.comb(n - col1, row1 - x) / math.comb(n, row1)

    observed = probability(a)
    tables = range(max(0, row1 - (n - col1)), min(row1, col1) + 1)
    return min(1.0, sum(p for p in map(probability, tables) if p <= observed * (1 + 1e-7)))
