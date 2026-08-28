# python-syntheticalert

Emit a synthetic alert metric from Python, so a
[Triple Pat](https://triplepat.com) check-in timer can verify your alerting
pipeline end to end. Works with Prometheus and OpenTelemetry.

## Why

A broken alerting pipeline looks exactly like a healthy system. No alerts
might mean nothing is wrong, or it might mean your alerting is down, and
your alerting system is the one thing that cannot alert you about itself.

This library gives your monitoring a synthetic alert metric that fires on a
schedule. You alert on that metric like any other (example alert rule and
Alertmanager route below) and route the alert to a Triple Pat check-in
timer, so every delivered alert becomes a check-in. If the check-ins stop
arriving, the timer raises an alarm through a separate channel. Your
alerting pipeline is broken. It is an automated fire drill for the whole
path from metric to notification.

## Usage

```sh
uv add triplepat-syntheticalert   # or: pip install triplepat-syntheticalert
```

The library has no dependencies and no background threads. It is a single
callable that answers "should the synthetic alert be firing right now?" and
you hand it to your metrics client as a gauge callback. Alongside your
existing Prometheus setup:

```python
from prometheus_client import Gauge
from syntheticalert import SyntheticAlert

gauge = Gauge(
    "triplepat_synthetic_alert",
    "Set to 1 when the synthetic alert should fire and 0 otherwise. Alert on "
    "this metric and route the alert to a Triple Pat check-in timer to "
    "continuously test your alerting pipeline.",
)
gauge.set_function(SyntheticAlert())
```

Or with OpenTelemetry (the OTel-to-Prometheus exporter turns the dotted name
into `triplepat_synthetic_alert`):

```python
from syntheticalert import SyntheticAlert

meter.create_observable_gauge(
    "triplepat.synthetic.alert",
    callbacks=[SyntheticAlert().observe],
    description="Set to 1 when the synthetic alert should fire and 0 otherwise.",
)
```

Each firing holds the gauge at 1 for exactly 10 minutes. The silent gap
between firings — from the end of one to the start of the next — is
memoryless (exponentially distributed with a configured mean of one hour;
the 10-minute and two-hour bounds pull the realized average down to about
49 minutes), never more than two hours, and never less than 10 minutes, so
the alert always visibly resolves between firings. The firings form a
Poisson process, and the
[PASTA theorem](https://en.wikipedia.org/wiki/Arrival_theorem#Theorem_for_arrivals_governed_by_a_Poisson_process)
means that this synthetic alert will not accidentally synchronize with other
periodic processes in your system. This is mathematically complex, but it is
almost certainly what you want.

The schedule advances lazily, at scrape time, from `time.monotonic()`. If
nobody scrapes for a while, the next scrape replays every transition it
missed, so the process stays honest whatever your scrape interval.

There is no magic here. One line is a serviceable substitute, firing for the
first ten minutes of every hour:

```python
import time

gauge.set_function(lambda: 1.0 if time.localtime().tm_min < 10 else 0.0)
```

But that version fires at the top of every hour, exactly when your cron jobs
are doing something interesting. The point of the library is the memoryless
schedule, which cannot synchronize with anything. If you want a
deterministic schedule anyway, the line above is all you need.

### Options

All durations are floats, in seconds.

| Keyword | Effect | Default |
|---|---|---|
| `mean_interval` | Mean silent gap between firings | `3600.0` |
| `min_interval` | Lower bound on the silent gap | `600.0` |
| `max_interval` | Upper bound on the silent gap | `7200.0` |
| `firing_duration` | How long each firing holds the gauge at 1 | `600.0` |
| `clock` | Time source, for tests | `time.monotonic` |

The firing duration must be shorter than the mean interval, and the min and
max intervals must bracket the mean. Bad options raise `ValueError` at
construction. Each scrape logs the current state at DEBUG on the `syntheticalert` logger.

## Alert on the metric

```yaml
groups:
  - name: synthetic
    rules:
      - alert: SyntheticAlert
        expr: triplepat_synthetic_alert == 1
        labels:
          severity: synthetic
        annotations:
          summary: Synthetic alert exercising the alerting pipeline.
```

## Route the alert to a check-in timer

Create a check-in timer at [Triple Pat](https://triplepat.com), then point
the alert at it. Merge this into your existing Alertmanager config (the
fragment assumes you already have a default receiver). As a webhook:

```yaml
route:
  routes:
    - matchers:
        - alertname="SyntheticAlert"
      receiver: triplepat
      group_wait: 0s
receivers:
  - name: triplepat
    webhook_configs:
      - url: https://triplepat.com/api/v1/checkin/YOUR-TIMER-UUID
        send_resolved: false
```

`send_resolved: false` keeps the resolve notification from counting as a
second check-in, so one firing is one check-in. Or as an email to
`YOUR-TIMER-UUID@checkin.triplepat.com`.

## Sizing the timer

Set the check-in timer's interval to at least
`max interval + firing duration + your alerting pipeline's latency`. With
the defaults (silent gaps of at most two hours, plus 10 minutes of
firing), a three-hour timer is comfortable.

## License

Apache-2.0. See [LICENSE](LICENSE).
