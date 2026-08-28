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
import random
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

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
    resampled until it lies within ``[min_interval, max_interval]``. The
    firings form a Poisson process and so cannot synchronize with cron jobs or
    with each other.

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
        ValueError: If any duration is not positive, the firing duration is
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
        if mean_interval <= 0:
            raise ValueError(f"mean interval must be positive, got {mean_interval}")
        if min_interval <= 0:
            raise ValueError(f"min interval must be positive, got {min_interval}")
        if max_interval <= 0:
            raise ValueError(f"max interval must be positive, got {max_interval}")
        if firing_duration <= 0:
            raise ValueError(f"firing duration must be positive, got {firing_duration}")
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
        """Draw one silent gap: exponential with the configured mean, resampled into bounds.

        Resampling beats clamping because it preserves the distribution's
        shape. The constructor guarantees ``min < max`` and
        ``min <= mean <= max``, so the window has positive probability mass
        and this loop terminates.
        """
        wait = -1.0  # outside [min, max], so at least one sample is drawn
        while wait < self._min or wait > self._max:
            wait = random.expovariate(1.0 / self._mean)
        return wait
