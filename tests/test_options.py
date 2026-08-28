from __future__ import annotations

import math
import re

import pytest

from syntheticalert import (
    DEFAULT_FIRING_DURATION,
    DEFAULT_MAX_INTERVAL,
    DEFAULT_MEAN_INTERVAL,
    DEFAULT_MIN_INTERVAL,
    SyntheticAlert,
)
from tests.conftest import FakeClock


def test_defaults_match_the_go_siblings() -> None:
    assert DEFAULT_MEAN_INTERVAL == 3600.0
    assert DEFAULT_MIN_INTERVAL == 600.0
    assert DEFAULT_MAX_INTERVAL == 7200.0
    assert DEFAULT_FIRING_DURATION == 600.0


def test_defaults_construct(clock: FakeClock) -> None:
    alert = SyntheticAlert(clock=clock)
    assert alert._firing is False
    assert (
        clock.now + DEFAULT_MIN_INTERVAL
        <= alert._next_transition
        <= clock.now + DEFAULT_MAX_INTERVAL
    )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"mean_interval": 0}, "mean interval must be positive and finite"),
        ({"mean_interval": -1}, "mean interval must be positive and finite"),
        ({"min_interval": 0}, "min interval must be positive and finite"),
        ({"max_interval": 0}, "max interval must be positive and finite"),
        ({"firing_duration": 0}, "firing duration must be positive and finite"),
        ({"mean_interval": math.nan}, "mean interval must be positive and finite, got nan"),
        ({"max_interval": math.inf}, "max interval must be positive and finite, got inf"),
        (
            {"firing_duration": 3600},
            "firing duration (3600) must be less than the mean interval (3600.0)",
        ),
        (
            {"min_interval": 4000},
            "min interval (4000) must not exceed the mean interval (3600.0)",
        ),
        (
            {"max_interval": 3000},
            "max interval (3000) must be at least the mean interval (3600.0)",
        ),
        (
            {"mean_interval": 60, "min_interval": 60, "max_interval": 60, "firing_duration": 1},
            "min interval (60) must be less than max interval (60)",
        ),
    ],
)
def test_bad_options_raise(kwargs: dict[str, float], message: str) -> None:
    with pytest.raises(ValueError, match=re.escape(message)):
        SyntheticAlert(**kwargs)  # type: ignore[arg-type]


def survival(x: float, mean: float) -> float:
    return math.exp(-x / mean)


def uniform_that_maps_to(gap: float, mean: float, lo: float, hi: float) -> float:
    """Invert _gap: the random() value for which the drawn gap is exactly `gap`."""
    s_hi, s_lo = survival(hi, mean), survival(lo, mean)
    return 1.0 - (survival(gap, mean) - s_hi) / (s_lo - s_hi)


@pytest.mark.parametrize(
    ("uniform", "gap"),
    [
        (0.0, DEFAULT_MIN_INTERVAL),
        (1.0 - 2**-53, DEFAULT_MAX_INTERVAL),  # the largest value random() can return
        (
            uniform_that_maps_to(
                DEFAULT_MEAN_INTERVAL,
                DEFAULT_MEAN_INTERVAL,
                DEFAULT_MIN_INTERVAL,
                DEFAULT_MAX_INTERVAL,
            ),
            DEFAULT_MEAN_INTERVAL,
        ),
    ],
)
def test_gap_is_the_truncated_exponential_quantile(
    monkeypatch: pytest.MonkeyPatch, clock: FakeClock, uniform: float, gap: float
) -> None:
    monkeypatch.setattr("syntheticalert.random.random", lambda: uniform)
    alert = SyntheticAlert(clock=clock)
    assert alert._next_transition == pytest.approx(clock.now + gap)


@pytest.mark.parametrize("uniform", [0.0, 0.5, 1.0 - 2**-53])
def test_gap_survives_a_max_interval_hundreds_of_means_away(
    monkeypatch: pytest.MonkeyPatch, clock: FakeClock, uniform: float
) -> None:
    """A naive CDF-space draw crashes here: exp(-max/mean) underflows to 0.0, so the CDF at
    max is exactly 1.0, and with the CDF at min above 0.5 the largest random() value rounds
    u to exactly 1.0 and log(1 - u) raises. The draw must instead be finite and in bounds.
    """
    monkeypatch.setattr("syntheticalert.random.random", lambda: uniform)
    alert = SyntheticAlert(
        mean_interval=1.0, min_interval=1.0, max_interval=1e6, firing_duration=0.5, clock=clock
    )
    gap = alert._next_transition - clock.now
    assert 1.0 <= gap <= 1e6
