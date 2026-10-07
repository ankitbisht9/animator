"""Rate limits: a sliding-window counter per key (O(1) memory per key), in process.

One API process holds the counts. Several processes behind a load balancer would each count on their
own (so the effective limit multiplies); a shared store (Redis) would replace this class then, with
the same `hit` interface.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass


@dataclass
class _Window:
    index: int
    count: int
    previous: int


class RateLimiter:
    def __init__(self) -> None:
        self._windows: dict[tuple[str, float], _Window] = {}
        self._swept = time.monotonic()

    def hit(self, key: str, limit: int, window_s: float, cost: int = 1) -> float | None:
        """Count one request. None: allowed; otherwise the seconds to wait."""
        if limit <= 0:
            return None
        now = time.monotonic()
        self._sweep(now)
        index = math.floor(now / window_s)
        w = self._windows.get((key, window_s))
        if w is None:
            w = self._windows[(key, window_s)] = _Window(index, 0, 0)
        elif w.index != index:
            w.previous = w.count if w.index == index - 1 else 0
            w.count = 0
            w.index = index
        elapsed = now / window_s - index
        estimate = w.previous * (1 - elapsed) + w.count
        if estimate + cost > limit:
            return max(1.0, (1 - elapsed) * window_s)
        w.count += cost
        return None

    def reset(self, key: str) -> None:
        for k in [k for k in self._windows if k[0] == key]:
            del self._windows[k]

    def _sweep(self, now: float) -> None:
        if now - self._swept < 60:
            return
        self._swept = now
        stale = [k for k, w in self._windows.items() if math.floor(now / k[1]) - w.index > 1]
        for k in stale:
            del self._windows[k]
