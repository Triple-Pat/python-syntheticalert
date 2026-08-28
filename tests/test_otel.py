from __future__ import annotations

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader, NumberDataPoint

from syntheticalert import SyntheticAlert
from tests.conftest import FakeClock


def latest_value(reader: InMemoryMetricReader) -> float:
    data = reader.get_metrics_data()
    assert data is not None
    metric = data.resource_metrics[0].scope_metrics[0].metrics[0]
    assert metric.name == "triplepat.synthetic.alert"
    point = metric.data.data_points[0]
    assert isinstance(point, NumberDataPoint)
    return float(point.value)


def test_observe_feeds_an_observable_gauge(clock: FakeClock) -> None:
    alert = SyntheticAlert(clock=clock)
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    meter = provider.get_meter("test")
    meter.create_observable_gauge(
        "triplepat.synthetic.alert",
        callbacks=[alert.observe],
        description="Set to 1 when the synthetic alert should fire and 0 otherwise.",
    )

    assert latest_value(reader) == 0.0
    clock.now = alert._next_transition
    assert latest_value(reader) == 1.0
    provider.shutdown()
