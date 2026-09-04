from __future__ import annotations

import math
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from syntheticalert import SyntheticAlert

if TYPE_CHECKING:
    from collections.abc import Callable

N = 10_000
# Kolmogorov-Smirnov critical value at alpha = 0.01 for large N.
KS_CRITICAL = 1.628 / math.sqrt(N)


def exponential_cdf(x: float, mean: float) -> float:
    return 1.0 - math.exp(-x / mean)


def truncated_exponential_cdf(x: float, mean: float, lo: float, hi: float) -> float:
    lo_mass, hi_mass = exponential_cdf(lo, mean), exponential_cdf(hi, mean)
    return (exponential_cdf(x, mean) - lo_mass) / (hi_mass - lo_mass)


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
    lo_mass, hi_mass = exponential_cdf(lo, mean), exponential_cdf(hi, mean)
    return -mean * math.log(1.0 - (lo_mass + u * (hi_mass - lo_mass)))


# Evenly spaced quantiles: a noise-free stand-in for a sample from a distribution.
QUANTILE_GRID = [(i + 0.5) / N for i in range(N)]


def test_every_gap_lies_within_bounds() -> None:
    alert = SyntheticAlert(
        mean_interval=timedelta(seconds=100),
        min_interval=timedelta(seconds=50),
        max_interval=timedelta(seconds=150),
        firing_duration=timedelta(seconds=1),
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
    """Prove the test has teeth."""
    alert = SyntheticAlert()
    samples = wrong(alert._mean, alert._min, alert._max)
    assert ks_statistic(samples, alert._mean, alert._min, alert._max) > KS_CRITICAL


def test_ks_test_accepts_the_right_distribution() -> None:
    """Positive control: noise-free quantiles of the right distribution pass."""
    alert = SyntheticAlert()
    samples = correct(alert._mean, alert._min, alert._max)
    assert ks_statistic(samples, alert._mean, alert._min, alert._max) < KS_CRITICAL
