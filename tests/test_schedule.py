from __future__ import annotations

import logging
import threading

import pytest

from syntheticalert import (
    DEFAULT_FIRING_DURATION,
    DEFAULT_MAX_INTERVAL,
    DEFAULT_MIN_INTERVAL,
    SyntheticAlert,
)
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


def test_each_transition_is_logged(clock: FakeClock, caplog: pytest.LogCaptureFixture) -> None:
    alert = SyntheticAlert(clock=clock)
    with caplog.at_level(logging.DEBUG, logger="syntheticalert"):
        clock.now = alert._next_transition
        alert()
        clock.now = alert._next_transition
        alert()
    assert [r.getMessage() for r in caplog.records] == [
        "synthetic alert firing",
        "synthetic alert resolved",
    ]


TEN_DAYS = 10 * 24 * 3600.0
# Every cycle is one silent gap plus one firing, so ten days holds this many cycles.
FEWEST_CYCLES = int(TEN_DAYS // (DEFAULT_MAX_INTERVAL + DEFAULT_FIRING_DURATION))
MOST_CYCLES = int(TEN_DAYS // (DEFAULT_MIN_INTERVAL + DEFAULT_FIRING_DURATION)) + 1


def assert_transitions_alternate(caplog: pytest.LogCaptureFixture) -> None:
    """Every logged transition flips state: firing, resolved, firing, ... starting with firing."""
    messages = [r.getMessage() for r in caplog.records]
    expected = ["synthetic alert firing", "synthetic alert resolved"] * (len(messages) // 2 + 1)
    assert messages == expected[: len(messages)]


def assert_schedule_is_one_transition_ahead(alert: SyntheticAlert, clock: FakeClock) -> None:
    assert (
        clock.now
        < alert._next_transition
        <= clock.now + DEFAULT_MAX_INTERVAL + DEFAULT_FIRING_DURATION
    )


def test_long_pause_replays_every_transition(
    clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    alert = SyntheticAlert(clock=clock)
    clock.advance(TEN_DAYS)
    with caplog.at_level(logging.DEBUG, logger="syntheticalert"):
        value = alert()
    assert value in (0.0, 1.0)
    assert_schedule_is_one_transition_ahead(alert, clock)
    assert_transitions_alternate(caplog)
    assert 2 * FEWEST_CYCLES <= len(caplog.records) <= 2 * MOST_CYCLES


def test_concurrent_scrapes_replay_exactly_once(
    clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    """Eight threads race to replay the same long pause; the lock must let exactly one do it."""
    alert = SyntheticAlert(clock=clock)
    clock.advance(TEN_DAYS)
    starting_gun = threading.Barrier(8)
    values: list[float] = []

    def scrape() -> None:
        starting_gun.wait()
        values.append(alert())

    with caplog.at_level(logging.DEBUG, logger="syntheticalert"):
        threads = [threading.Thread(target=scrape) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    assert len(set(values)) == 1, "every thread must observe the same state"
    assert_schedule_is_one_transition_ahead(alert, clock)
    # Without the lock, two threads can both read _firing, both flip it the same way,
    # and log two "firing" lines in a row.
    assert_transitions_alternate(caplog)
    assert 2 * FEWEST_CYCLES <= len(caplog.records) <= 2 * MOST_CYCLES
