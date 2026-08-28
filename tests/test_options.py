from __future__ import annotations

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
        ({"mean_interval": 0}, "mean interval must be positive"),
        ({"mean_interval": -1}, "mean interval must be positive"),
        ({"min_interval": 0}, "min interval must be positive"),
        ({"max_interval": 0}, "max interval must be positive"),
        ({"firing_duration": 0}, "firing duration must be positive"),
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
    with pytest.raises(ValueError, match=r".*") as excinfo:
        SyntheticAlert(**kwargs)  # type: ignore[arg-type]
    assert message in str(excinfo.value)
