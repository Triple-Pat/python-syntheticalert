from __future__ import annotations

from prometheus_client import CollectorRegistry, Gauge, generate_latest

from syntheticalert import SyntheticAlert
from tests.conftest import FakeClock


def test_set_function_scrapes_the_alert(clock: FakeClock) -> None:
    alert = SyntheticAlert(clock=clock)
    registry = CollectorRegistry()
    gauge = Gauge(
        "triplepat_synthetic_alert",
        "Set to 1 when the synthetic alert should fire and 0 otherwise.",
        registry=registry,
    )
    gauge.set_function(alert)

    assert b"triplepat_synthetic_alert 0.0\n" in generate_latest(registry)
    clock.now = alert._next_transition
    assert b"triplepat_synthetic_alert 1.0\n" in generate_latest(registry)
