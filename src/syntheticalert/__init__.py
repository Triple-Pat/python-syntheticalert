"""Synthetic alert measurement callback for Triple Pat check-in timers.

A broken alerting pipeline looks exactly like a healthy system. ``SyntheticAlert``
is a callable that returns ``1.0`` while a synthetic alert should be firing and
``0.0`` otherwise, on a memoryless schedule. Hook it up as a gauge callback,
alert on the gauge, and route the alert to a Triple Pat check-in timer
(https://triplepat.com); every delivered alert becomes a check-in, and the
timer raises an alarm if the alerts stop arriving.

Prometheus::

    gauge = Gauge("triplepat_synthetic_alert", "...")
    gauge.set_function(SyntheticAlert())

OpenTelemetry::

    meter.create_observable_gauge("triplepat.synthetic.alert",
                                  callbacks=[SyntheticAlert().observe])
"""

from __future__ import annotations

import logging
import math
import random
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from opentelemetry.metrics import CallbackOptions, Observation

__all__ = [
    "DEFAULT_FIRING_DURATION",
    "DEFAULT_MAX_INTERVAL",
    "DEFAULT_MEAN_INTERVAL",
    "DEFAULT_MIN_INTERVAL",
    "SyntheticAlert",
]

DEFAULT_MEAN_INTERVAL = 3600.0
"""Default mean silent gap between firings, in seconds."""
DEFAULT_MIN_INTERVAL = 600.0
"""Default lower bound on the silent gap, in seconds."""
DEFAULT_MAX_INTERVAL = 7200.0
"""Default upper bound on the silent gap, in seconds."""
DEFAULT_FIRING_DURATION = 600.0
"""Default length of each firing, in seconds."""

_log = logging.getLogger("syntheticalert")


class SyntheticAlert:
    """A measurement callback that is 1.0 while the synthetic alert fires.

    Each firing holds the value at 1 for exactly ``firing_duration`` seconds.
    The silent gap between firings, from the end of one to the start of the
    next, is exponentially distributed (memoryless) with mean ``mean_interval``,
    truncated to ``[min_interval, max_interval]``. The firings form a Poisson
    process and so cannot synchronize with cron jobs or with each other.

    The schedule advances lazily: nothing happens until the object is called,
    at which point every transition up to ``clock()`` is replayed. Calls are
    serialized with a lock, so the object is safe to scrape from several
    threads.

    Args:
        mean_interval: Mean silent gap between firings, in seconds.
        min_interval: Lower bound on the silent gap, in seconds.
        max_interval: Upper bound on the silent gap, in seconds.
        firing_duration: How long each firing lasts, in seconds.
        clock: Returns the current time in seconds; must never go backwards.

    Raises:
        ValueError: If any duration is not positive and finite, the firing duration is
            not shorter than the mean interval, the min and max intervals do
            not bracket the mean, or the min interval is not below the max.
    """

    def __init__(
        self,
        *,
        mean_interval: float = DEFAULT_MEAN_INTERVAL,
        min_interval: float = DEFAULT_MIN_INTERVAL,
        max_interval: float = DEFAULT_MAX_INTERVAL,
        firing_duration: float = DEFAULT_FIRING_DURATION,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        for name, value in (
            ("mean interval", mean_interval),
            ("min interval", min_interval),
            ("max interval", max_interval),
            ("firing duration", firing_duration),
        ):
            # NaN compares False to everything, so it needs the explicit isfinite check.
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive and finite, got {value}")
        if firing_duration >= mean_interval:
            raise ValueError(
                f"firing duration ({firing_duration}) must be less than "
                f"the mean interval ({mean_interval})"
            )
        if min_interval > mean_interval:
            raise ValueError(
                f"min interval ({min_interval}) must not exceed the mean interval ({mean_interval})"
            )
        if max_interval < mean_interval:
            raise ValueError(
                f"max interval ({max_interval}) must be at least "
                f"the mean interval ({mean_interval})"
            )
        if min_interval >= max_interval:
            raise ValueError(
                f"min interval ({min_interval}) must be less than max interval ({max_interval})"
            )
        self._mean = mean_interval
        self._min = min_interval
        self._max = max_interval
        self._firing_duration = firing_duration
        self._clock = clock
        self._lock = threading.Lock()
        self._firing = False
        self._next_transition = clock() + self._gap()

    def _gap(self) -> float:
        """Draw one silent gap from the exponential distribution truncated to [min, max].

        Inverse-CDF sampling: pick a uniform point within the probability
        mass the exponential puts on the window, then map it back through
        the exponential's quantile function. One draw, exact shape, and the
        bounds hold literally.
        """
        lo = 1.0 - math.exp(-self._min / self._mean)
        hi = 1.0 - math.exp(-self._max / self._mean)
        u = lo + random.random() * (hi - lo)
        return -self._mean * math.log(1.0 - u)

    def __call__(self) -> float:
        """Return 1.0 if the synthetic alert should be firing right now, else 0.0.

        Replays every schedule transition between the last call and now, so
        the firings stay an honest Poisson process whatever the scrape cadence.
        """
        with self._lock:
            now = self._clock()
            while now >= self._next_transition:
                self._firing = not self._firing
                self._next_transition += self._firing_duration if self._firing else self._gap()
                _log.debug("synthetic alert firing" if self._firing else "synthetic alert resolved")
            return 1.0 if self._firing else 0.0

    def observe(self, options: CallbackOptions) -> Iterator[Observation]:  # noqa: ARG002
        """Adapt the callable to an OpenTelemetry observable-gauge callback.

        Pass as ``callbacks=[alert.observe]`` to
        ``Meter.create_observable_gauge``. Imports ``opentelemetry-api`` lazily,
        so this package has no dependency on it; anyone holding a ``Meter``
        already has it installed.
        """
        from opentelemetry.metrics import Observation  # noqa: PLC0415

        yield Observation(self())
