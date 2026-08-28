from __future__ import annotations

import math

import pytest

from syntheticalert import SyntheticAlert

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
    d = 0.0
    for i, x in enumerate(samples):
        theoretical = truncated_exponential_cdf(x, mean, lo, hi)
        d = max(d, abs((i + 1) / N - theoretical), abs(i / N - theoretical))
    return d


def test_every_gap_lies_within_bounds() -> None:
    alert = SyntheticAlert(
        mean_interval=100.0, min_interval=50.0, max_interval=150.0, firing_duration=1.0
    )
    for _ in range(N):
        assert 50.0 <= alert._gap() <= 150.0


# A correct sampler fails this about 1% of the time by construction; two
# reruns bring the false-failure rate to ~1e-6. A clamped, uniform, or
# wrong-mean sampler fails every run.
@pytest.mark.flaky(reruns=2)
def test_gaps_are_memoryless() -> None:
    alert = SyntheticAlert()
    samples = [alert._gap() for _ in range(N)]
    d = ks_statistic(samples, alert._mean, alert._min, alert._max)
    assert d <= KS_CRITICAL, f"K-S statistic {d:.4f} exceeds {KS_CRITICAL:.4f}"


def test_ks_test_rejects_a_clamped_sampler() -> None:
    """Prove the test has teeth: clamping instead of resampling piles mass on the bounds."""
    alert = SyntheticAlert()
    samples = [
        min(max(alert._mean * -math.log(1 - i / N), alert._min), alert._max) for i in range(N)
    ]
    assert ks_statistic(samples, alert._mean, alert._min, alert._max) > KS_CRITICAL
