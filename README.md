[![Lint and Test](https://github.com/Triple-Pat/python-syntheticalert/actions/workflows/ci.yml/badge.svg)](https://github.com/Triple-Pat/python-syntheticalert/actions/workflows/ci.yml) [![Coverage Status](https://coveralls.io/repos/github/Triple-Pat/python-syntheticalert/badge.svg?branch=main)](https://coveralls.io/github/Triple-Pat/python-syntheticalert?branch=main)

# python-syntheticalert

Drive a synthetic alert metric from Python, so a
[Triple Pat](https://triplepat.com) check-in timer can verify your alerting
pipeline end to end. Works with Prometheus and OpenTelemetry.

## Why

A broken alerting pipeline looks exactly like a healthy system. No alerts
might mean nothing is wrong, or it might mean your alerting is down, and
your alerting system is the one thing that cannot alert you about itself.

This library provides a time-based callback to drive a synthetic alert
metric. You register the callback as a gauge in your existing metrics
setup, alert on the gauge like any other metric, and route the alert to a
Triple Pat check-in timer. Every delivered alert then becomes a check-in,
and every firing is another fire drill for the whole path from metric to
notification. If the check-ins ever stop, your alerting pipeline is
broken, and the Triple Pat app raises an alarm through a separate channel
to tell you so. An example alert rule and Alertmanager route are below.

## Usage

```sh
uv add triplepat-syntheticalert   # or: pip install triplepat-syntheticalert
```

The library has no dependencies and no background threads. It is a single
callable that answers the question "should the synthetic alert be firing
right now?", and you hand it to your metrics client as a gauge callback.

### Prometheus

Alongside your existing Prometheus setup:

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

This is for single-process servers. In a multi-process server the synthetic
alert must come from exactly one process, and the next section shows how.
The principle is worth stating on its own: a synthetic alert is a schedule,
and a schedule has to have one owner. With one `SyntheticAlert` per worker
there are as many schedules as workers, each scrape lands on a random one,
and the alert flaps. Whether that one owner is the master process or a
small sidecar exporter of its own is a deployment choice; what matters is
that there is one.

### Prometheus under gunicorn

Under gunicorn, `prometheus_client` runs in multiprocess mode: with
`PROMETHEUS_MULTIPROC_DIR` set, every gauge keeps its value in a
per-process file, and the scrape is answered by a `MultiProcessCollector`
that reads those files. It never calls a gauge's function, so the
`set_function` wiring above reports the 0 that was written when the gauge
was constructed, on every scrape, forever. The alert never fires and the
check-in timer raises an alarm for a pipeline that is fine.

The fix is to drive the gauge from the master, which is the one process
that exists exactly once. `when_ready` runs in the master, once, before
any worker forks. A daemon thread there sets the gauge from the schedule,
which writes the master's file, and every scrape reports the master's
value whichever worker answers it. In `gunicorn.conf.py`:

```python
import threading
import time

from prometheus_client import Gauge
from prometheus_client.multiprocess import mark_process_dead
from syntheticalert import SyntheticAlert


def when_ready(server):
    alert = SyntheticAlert()
    gauge = Gauge(
        "triplepat_synthetic_alert",
        "Set to 1 when the synthetic alert should fire and 0 otherwise.",
        multiprocess_mode="livemostrecent",
    )

    def drive():
        while True:
            gauge.set(alert())
            time.sleep(1)

    threading.Thread(target=drive, daemon=True, name="synthetic-alert").start()


def child_exit(server, worker):
    mark_process_dead(worker.pid)
```

`multiprocess_mode="livemostrecent"` is what makes this work. Workers
fork after `when_ready`, so they inherit the gauge, and the first metric a
worker touches resets every inherited value and writes a placeholder 0 for
the gauge into that worker's file, with no timestamp. Each `set` in the
master stamps its write with the current time, and `livemostrecent`
reports the sample with the newest timestamp, so the master's value always
wins and the placeholders never show. The mode also collapses the series
to one line without a `pid` label and drops processes that have exited.
The scrape is at most one second stale, which is nothing against a
ten-minute firing. The usual multiprocess rule still applies: empty
`PROMETHEUS_MULTIPROC_DIR` when gunicorn starts, or a previous master's
file lingers.

The library itself still starts no thread. The thread belongs in the
deployment configuration, next to the decision about how many processes
there are.

### OpenTelemetry

The same callable serves OpenTelemetry through its `observe` method. The
OTel-to-Prometheus exporter turns the dotted metric name into
`triplepat_synthetic_alert`. The one-owner principle applies here too:
register the observable gauge in exactly one process.

```python
from syntheticalert import SyntheticAlert

meter.create_observable_gauge(
    "triplepat.synthetic.alert",
    callbacks=[SyntheticAlert().observe],
    description="Set to 1 when the synthetic alert should fire and 0 otherwise.",
)
```

### The schedule

Each firing holds the gauge at 1 for exactly 10 minutes. The silent gap
between firings, from the end of one to the start of the next, is
memoryless: exponentially distributed with a mean of one hour.

Memoryless gaps make the firings an attempt at a Poisson process, which
cannot synchronize with cron jobs or scrape cycles, and which by the
[PASTA theorem](https://en.wikipedia.org/wiki/Arrival_theorem#Theorem_for_arrivals_governed_by_a_Poisson_process)
sees your pipeline as it typically is rather than at some special moment.

As a nod to practicality the gap is truncated. It is never less than 10
minutes, so the alert visibly resolves between firings, and never more
than two hours, so the check-in timer can be sized. The truncation pulls
the realized mean gap down to about 49 minutes and makes the process only
roughly Poisson. If you need the PASTA property and can tolerate wider
variation in start times, set a lower min and a higher max, then size the
timer for the larger max. That recovers most of the Poisson behavior; for
the last few percent, use a mean much longer than the firing duration,
since the interval between firing starts is the firing plus the gap.

The schedule advances lazily, at scrape time, from `time.monotonic()`. If
nobody scrapes for a while, the next scrape replays every transition it
missed, so the process stays honest whatever your scrape interval.

There is no magic here: one line is a serviceable substitute, firing for
the first ten minutes of every hour:

```python
import time

gauge.set_function(lambda: 1.0 if time.localtime().tm_min < 10 else 0.0)
```

But that version fires at the top of every hour, exactly when your cron
jobs are doing something interesting. The memoryless schedule cannot
synchronize with anything, and that is the point of the library. If you
want a deterministic schedule anyway, the line above is all you need.

### Options

Every duration is a `datetime.timedelta`, Python's standard duration type,
so the unit is in the code rather than in the documentation. The type hints
are the contract; nothing checks types at runtime:

```python
from datetime import timedelta

SyntheticAlert(mean_interval=timedelta(minutes=30), max_interval=timedelta(hours=1))
```

| Keyword | Effect | Default |
|---|---|---|
| `mean_interval` | Mean silent gap between firings | `timedelta(hours=1)` |
| `min_interval` | Lower bound on the silent gap | `timedelta(minutes=10)` |
| `max_interval` | Upper bound on the silent gap | `timedelta(hours=2)` |
| `firing_duration` | How long each firing holds the gauge at 1 | `timedelta(minutes=10)` |
| `clock` | Time source in seconds, for tests | `time.monotonic` |

The firing duration must be shorter than the mean interval, and the min and
max intervals must bracket the mean. Bad options raise `ValueError` at
construction, never at scrape time. Setting all three intervals equal
is allowed: every gap is then exactly that long and the schedule is
periodic, which is pointless in production but handy for deterministic
debugging. The defaults are exported as `DEFAULT_MEAN_INTERVAL`,
`DEFAULT_MIN_INTERVAL`, `DEFAULT_MAX_INTERVAL`, and `DEFAULT_FIRING_DURATION`.

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
the alert at it. Prefer email delivery: mail transfer agents queue, retry,
and try every backend listed in DNS, so a check-in email is more likely to
arrive than a single webhook request to a single destination. Send to the
same timer at both the `.com` and `.net` addresses. The two domains are
served by independent DNS providers, so if one zone cannot be resolved the
other address still delivers, and extra simultaneous check-ins are
harmless. Merge this into your existing Alertmanager config (the fragment
assumes you already have a default receiver and working `smtp_*` defaults):

```yaml
route:
  routes:
    - matchers:
        - alertname="SyntheticAlert"
      receiver: triplepat
      group_wait: 0s
receivers:
  - name: triplepat
    email_configs:
      - to: YOUR-TIMER-UUID@checkin.triplepat.com
        send_resolved: false
      - to: YOUR-TIMER-UUID@checkin.triplepat.net
        send_resolved: false
```

`send_resolved: false` keeps the resolve notification from counting as an
extra check-in, so each firing checks in when it starts and not again when
it resolves.

If you cannot send email, replace the `triplepat` receiver above with this
webhook receiver instead. Alertmanager rejects a configuration that defines
the same receiver name twice:

```yaml
receivers:
  - name: triplepat
    webhook_configs:
      - url: https://triplepat.com/api/v1/checkin/YOUR-TIMER-UUID
        send_resolved: false
```

## Sizing the timer

Set the check-in timer's interval to at least
`max interval + firing duration + your alerting pipeline's latency`. With
the defaults (silent gaps of at most two hours, plus 10 minutes of
firing), a three-hour timer is comfortable.

## License

Apache-2.0. See [LICENSE](LICENSE).
