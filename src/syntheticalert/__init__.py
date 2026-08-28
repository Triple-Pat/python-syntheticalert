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
        ValueError: If any duration is not positive and finite, the firing
            duration is not shorter than the mean interval, or the min and max
            intervals do not bracket the mean. Setting all three intervals
            equal is allowed: the window has zero width, every gap is exactly
            that long, and the schedule becomes periodic, which is pointless
            in production but handy for deterministic debugging.
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
        if not min_interval <= mean_interval <= max_interval:
            raise ValueError(
                f"min interval ({min_interval}) and max interval ({max_interval}) "
                f"must bracket the mean interval ({mean_interval})"
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
        # Work with the survival function S(x) = exp(-x / mean), which is
        # strictly positive at min (min <= mean, so the exponent is at least
        # -1) but underflows to 0.0 when max is hundreds of means away. Using
        # 1 - random() puts u in (S(max), S(min)], never at S(max), so log()
        # always gets a positive argument. The clamp only tidies rounding.
        s_max = math.exp(-self._max / self._mean)
        s_min = math.exp(-self._min / self._mean)
        u = s_max + (1.0 - random.random()) * (s_min - s_max)
        return min(max(-self._mean * math.log(u), self._min), self._max)

    def __call__(self) -> float:
        """Return 1.0 if the synthetic alert should be firing right now, else 0.0.

        Replays every schedule transition between the last call and now, so
        the firings stay an honest Poisson process whatever the scrape cadence.
        """
        # Why carry state and replay transitions, rather than compute the state
        # from the clock alone?
        #
        # A stateless answer to "is a firing in progress?" needs the firing
        # times to be a pure function of wall-clock time. That is possible for
        # a plain Poisson process, because it has independent increments: chop
        # time into epochs, seed a PRNG from the epoch index, draw that epoch's
        # arrivals, and check whether one falls within the last firing_duration.
        # It has a real attraction, too: every replica of a service would
        # compute the same schedule and raise one alert instead of N.
        #
        # But the min and max bounds on the silent gap make each gap depend on
        # where the previous firing ended, which destroys independent
        # increments; epochs can no longer be generated in isolation. Thinning
        # and back-filling a plain Poisson stream to fake the bounds would have
        # to peek across epoch boundaries and would no longer have a
        # distribution the tests can name. The bounds exist for practical
        # reasons (the alert must visibly resolve; the check-in timer must not
        # false-alarm), so we honor them exactly with an alternating renewal
        # process: fixed firings, i.i.d. truncated-exponential gaps, and a few
        # words of state.
        #
        # Replaying every missed transition, rather than jumping to the current
        # state, keeps the realized schedule identical whatever the scrape
        # cadence. It costs one loop iteration per elapsed transition, about
        # fifty a day at the defaults, so even a scrape after a week of silence
        # is trivial.
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
