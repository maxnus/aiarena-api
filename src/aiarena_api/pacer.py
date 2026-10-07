import asyncio
import time

# Ceiling on the backoff multiplier. At 32x a job paced to one request every 4s drops to one every two minutes, which
# is as good as stopped.
MAX_PENALTY = 32.0

# How long a backoff step takes to undo itself once the errors stop. Time-based so that a heavily paced job, which
# makes few requests, still recovers.
PENALTY_HALFLIFE_SECONDS = 300.0


class Pacer:
    """Caps how often requests may start, across all concurrent callers, and widens the gap while the server struggles.

    aiarena is a volunteer-run service: a job that makes a day's worth of requests should spread them out rather than
    fire them in one burst. Unpaced, the default, costs nothing: the penalty multiplies an interval that is zero.
    """

    def __init__(self, rate_per_minute: float | None = None) -> None:
        self._min_interval = 0.0
        self._lock = asyncio.Lock()
        self._next_slot = 0.0
        self._penalty = 1.0
        self._penalty_set_at = 0.0
        self.set_rate_per_minute(rate_per_minute)

    @property
    def min_interval(self) -> float:
        """Seconds between request starts before any backoff; zero when unpaced."""
        return self._min_interval

    def set_rate_per_minute(self, rate: float | None) -> None:
        """Set the cap on request starts per minute; None or 0 means unpaced.

        This bounds the request rate, not the concurrency, and the two are independent.
        """
        self._min_interval = 60.0 / rate if rate else 0.0

    @property
    def penalty(self) -> float:
        """The backoff multiplier now, decayed by how long it has been since the last failure.

        Recovery is measured in time, not in responses: a job paced to a few requests a minute would otherwise need
        hours of clean responses to undo a short burst of errors, and the throttle would throttle its own recovery.
        """
        if self._penalty <= 1.0:
            return 1.0
        elapsed = time.monotonic() - self._penalty_set_at
        decayed = self._penalty * 0.5 ** (elapsed / PENALTY_HALFLIFE_SECONDS)
        return max(1.0, decayed)

    def slow_down(self) -> None:
        """Double the backoff after the server signals distress (a 5xx or a transport error).

        A fixed rate is a guess about someone else's database, and a server degrading under sustained load fails at
        steadily shallower depths. This makes the guess self-correcting.
        """
        self._penalty = min(self.penalty * 2.0, MAX_PENALTY)
        self._penalty_set_at = time.monotonic()

    async def wait(self) -> None:
        """Block until this request's turn in the paced schedule.

        Slots are handed out from a shared cursor so N concurrent workers share one rate rather than getting N times
        it. The lock covers claiming a slot, never the sleep, so workers queue instantly and then wait apart.
        """
        if not self._min_interval:
            return
        loop = asyncio.get_running_loop()
        async with self._lock:
            now = loop.time()
            start = max(now, self._next_slot)
            self._next_slot = start + self._min_interval * self.penalty
        delay = start - now
        if delay > 0:
            await asyncio.sleep(delay)
