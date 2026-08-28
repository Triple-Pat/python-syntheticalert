from __future__ import annotations

import math
from typing import TYPE_CHECKING

import pytest

from syntheticalert import SyntheticAlert

if TYPE_CHECKING:
    from collections.abc import Callable

N = 10_000
# Kolmogorov-Smirnov critical value at alpha = 0.01 for large N.
KS_CRITICAL = 1.628 / math.sqrt(N)


def truncated_exponential_cdf(x: float, mean: float, lo: float, hi: float) -> float:
    def cdf(v: float) -> float:
        return 1.0 - math.exp(-v / mean)

    return (cdf(x) - cdf(lo)) / (cdf(hi) - cdf(lo))


def ks_statistic(samples: list[float], mean: float, lo: float, hi: float) -> float:
    """Largest distance between the empirical CDF and the theoretical one."""
    samples = sorted(samples)
    n = len(samples)
    d = 0.0
    for i, x in enumerate(samples):
        theoretical = truncated_exponential_cdf(x, mean, lo, hi)
        d = max(d, abs((i + 1) / n - theoretical), abs(i / n - theoretical))
    return d


def inverse_truncated_exponential(u: float, mean: float, lo: float, hi: float) -> float:
    """Quantile function of the exponential(mean) distribution truncated to [lo, hi]."""

    def cdf(v: float) -> float:
        return 1.0 - math.exp(-v / mean)

    return -mean * math.log(1.0 - (cdf(lo) + u * (cdf(hi) - cdf(lo))))


# Evenly spaced quantiles: a noise-free stand-in for a sample from a distribution.
QUANTILE_GRID = [(i + 0.5) / N for i in range(N)]


def test_every_gap_lies_within_bounds() -> None:
    alert = SyntheticAlert(
        mean_interval=100.0, min_interval=50.0, max_interval=150.0, firing_duration=1.0
    )
    for _ in range(N):
        assert 50.0 <= alert._gap() <= 150.0


# A correct sampler fails this about 1% of the time by construction; two
# reruns bring the false-failure rate to ~1e-6. The wrong samplers in
# test_ks_test_rejects_wrong_distributions fail every run.
@pytest.mark.flaky(reruns=2)
def test_gaps_are_memoryless() -> None:
    alert = SyntheticAlert()
    samples = [alert._gap() for _ in range(N)]
    d = ks_statistic(samples, alert._mean, alert._min, alert._max)
    assert d <= KS_CRITICAL, f"K-S statistic {d:.4f} exceeds {KS_CRITICAL:.4f}"


def clamped_exponential(mean: float, lo: float, hi: float) -> list[float]:
    """What a sampler that clamps instead of resampling would produce: mass piled on the bounds."""
    return [min(max(-mean * math.log(1.0 - u), lo), hi) for u in QUANTILE_GRID]


def uniform(_mean: float, lo: float, hi: float) -> list[float]:
    return [lo + u * (hi - lo) for u in QUANTILE_GRID]


def mean_off_by_a_quarter(mean: float, lo: float, hi: float) -> list[float]:
    return [inverse_truncated_exponential(u, mean * 1.25, lo, hi) for u in QUANTILE_GRID]


def correct(mean: float, lo: float, hi: float) -> list[float]:
    return [inverse_truncated_exponential(u, mean, lo, hi) for u in QUANTILE_GRID]


@pytest.mark.parametrize("wrong", [clamped_exponential, uniform, mean_off_by_a_quarter])
def test_ks_test_rejects_wrong_distributions(
    wrong: Callable[[float, float, float], list[float]],
) -> None:
    """Prove the test has teeth, and that the right distribution passes on noise-free data."""
    alert = SyntheticAlert()
    assert (
        ks_statistic(
            wrong(alert._mean, alert._min, alert._max), alert._mean, alert._min, alert._max
        )
        > KS_CRITICAL
    )
    assert (
        ks_statistic(
            correct(alert._mean, alert._min, alert._max), alert._mean, alert._min, alert._max
        )
        < KS_CRITICAL
    )
