from __future__ import annotations

import logging
import threading

import pytest

from syntheticalert import DEFAULT_FIRING_DURATION, DEFAULT_MAX_INTERVAL, SyntheticAlert
from tests.conftest import FakeClock

EPSILON = 1e-6


def test_starts_resolved(clock: FakeClock) -> None:
    alert = SyntheticAlert(clock=clock)
    assert alert() == 0.0


def test_fires_after_one_silent_gap_then_resolves(clock: FakeClock) -> None:
    alert = SyntheticAlert(clock=clock)
    first_firing = alert._next_transition

    clock.now = first_firing - EPSILON
    assert alert() == 0.0

    clock.now = first_firing
    assert alert() == 1.0
    assert alert._next_transition == first_firing + DEFAULT_FIRING_DURATION

    clock.now = first_firing + DEFAULT_FIRING_DURATION - EPSILON
    assert alert() == 1.0

    clock.now = first_firing + DEFAULT_FIRING_DURATION
    assert alert() == 0.0


def test_gap_is_measured_from_end_of_firing(clock: FakeClock) -> None:
    alert = SyntheticAlert(clock=clock)
    clock.now = alert._next_transition
    alert()  # firing
    resolved_at = alert._next_transition
    clock.now = resolved_at
    alert()  # resolved; a fresh gap was drawn from here
    next_firing = alert._next_transition
    assert resolved_at + 600.0 <= next_firing <= resolved_at + DEFAULT_MAX_INTERVAL


def test_long_pause_replays_every_transition(
    clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    alert = SyntheticAlert(clock=clock)
    ten_days = 10 * 24 * 3600.0
    clock.advance(ten_days)
    with caplog.at_level(logging.INFO, logger="syntheticalert"):
        value = alert()
    assert value in (0.0, 1.0)
    assert alert._next_transition > clock.now
    firings = [r for r in caplog.records if r.getMessage() == "synthetic alert firing"]
    # Each cycle is at most max gap + firing duration long.
    assert len(firings) >= ten_days // (DEFAULT_MAX_INTERVAL + DEFAULT_FIRING_DURATION)
    assert firings[0].__dict__["firing_duration"] == DEFAULT_FIRING_DURATION


def test_concurrent_scrapes_are_safe() -> None:
    alert = SyntheticAlert(
        mean_interval=0.002, min_interval=0.001, max_interval=0.004, firing_duration=0.001
    )
    seen: set[float] = set()
    errors: list[BaseException] = []

    def scrape() -> None:
        try:
            for _ in range(2_000):
                seen.add(alert())
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=scrape) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert seen <= {0.0, 1.0}
