from __future__ import annotations

import math
import re
from datetime import timedelta

import pytest

from syntheticalert import (
    DEFAULT_FIRING_DURATION,
    DEFAULT_MAX_INTERVAL,
    DEFAULT_MEAN_INTERVAL,
    DEFAULT_MIN_INTERVAL,
    SyntheticAlert,
)
from tests.conftest import FakeClock

# The defaults as float seconds, for arithmetic against the float clock.
MEAN = DEFAULT_MEAN_INTERVAL.total_seconds()
MIN = DEFAULT_MIN_INTERVAL.total_seconds()
MAX = DEFAULT_MAX_INTERVAL.total_seconds()


def test_defaults_are_timedeltas_matching_the_siblings() -> None:
    defaults = (
        DEFAULT_MEAN_INTERVAL,
        DEFAULT_MIN_INTERVAL,
        DEFAULT_MAX_INTERVAL,
        DEFAULT_FIRING_DURATION,
    )
    assert defaults == (
        timedelta(hours=1),
        timedelta(minutes=10),
        timedelta(hours=2),
        timedelta(minutes=10),
    )
    assert [d.total_seconds() for d in defaults] == [3600, 600, 7200, 600]


def test_defaults_construct(clock: FakeClock) -> None:
    alert = SyntheticAlert(clock=clock)
    assert alert._firing is False
    assert clock.now + MIN <= alert._next_transition <= clock.now + MAX


@pytest.mark.parametrize(
    "option", ["mean_interval", "min_interval", "max_interval", "firing_duration"]
)
@pytest.mark.parametrize("value", [3600, 3600.0, "1h", None, True])
def test_durations_must_be_timedeltas(option: str, value: object) -> None:
    name = option.replace("_", " ")
    with pytest.raises(TypeError, match=re.escape(f"{name} must be a datetime.timedelta, got")):
        SyntheticAlert(**{option: value})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"mean_interval": timedelta(0)}, "mean interval must be positive, got 0:00:00"),
        ({"mean_interval": timedelta(seconds=-1)}, "mean interval must be positive"),
        ({"min_interval": timedelta(0)}, "min interval must be positive"),
        ({"max_interval": timedelta(0)}, "max interval must be positive"),
        ({"firing_duration": timedelta(0)}, "firing duration must be positive"),
        (
            {"firing_duration": timedelta(hours=1)},
            "firing duration (1:00:00) must be less than the mean interval (1:00:00)",
        ),
        (
            {"min_interval": timedelta(minutes=70)},
            "min interval (1:10:00) and max interval (2:00:00) must bracket the mean interval",
        ),
        (
            {"max_interval": timedelta(minutes=50)},
            "min interval (0:10:00) and max interval (0:50:00) must bracket the mean interval",
        ),
    ],
)
def test_bad_options_raise(kwargs: dict[str, timedelta], message: str) -> None:
    with pytest.raises(ValueError, match=re.escape(message)):
        SyntheticAlert(**kwargs)  # type: ignore[arg-type]


def test_zero_width_window_is_a_periodic_schedule(clock: FakeClock) -> None:
    """min == mean == max is legal: every gap is exactly that long, for deterministic debugging."""
    minute = timedelta(minutes=1)
    alert = SyntheticAlert(
        mean_interval=minute,
        min_interval=minute,
        max_interval=minute,
        firing_duration=timedelta(seconds=1),
        clock=clock,
    )
    for _ in range(100):
        clock.now = alert._next_transition  # fire
        assert alert() == 1.0
        clock.now = alert._next_transition  # resolve, drawing a new gap
        assert alert() == 0.0
        assert alert._next_transition == pytest.approx(clock.now + 60)


def survival(x: float, mean: float) -> float:
    return math.exp(-x / mean)


def uniform_that_maps_to(gap: float, mean: float, lo: float, hi: float) -> float:
    """Invert _gap: the random() value for which the drawn gap is exactly `gap`."""
    s_hi, s_lo = survival(hi, mean), survival(lo, mean)
    return 1.0 - (survival(gap, mean) - s_hi) / (s_lo - s_hi)


@pytest.mark.parametrize(
    ("uniform", "gap"),
    [
        (0.0, MIN),
        (1.0 - 2**-53, MAX),  # the largest value random() can return
        (uniform_that_maps_to(MEAN, MEAN, MIN, MAX), MEAN),
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
    second = timedelta(seconds=1)
    alert = SyntheticAlert(
        mean_interval=second,
        min_interval=second,
        max_interval=timedelta(seconds=1e6),
        firing_duration=timedelta(seconds=0.5),
        clock=clock,
    )
    gap = alert._next_transition - clock.now
    assert 1.0 <= gap <= 1e6
