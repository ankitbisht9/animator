"""Request metrics, in process: counts, errors and latency per route (ids folded into the route's
shape), for the admin's system panel. Event streams are counted but not timed (they stay open)."""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass, field

_IDS = [
    (re.compile(r"/g_[0-9a-f]{12}"), "/{lesson}"),
    (re.compile(r"/d_[0-9a-f]{12}"), "/{doubt}"),
    (re.compile(r"/[0-9a-f]{32}"), "/{id}"),
    (re.compile(r"/[0-9a-f]{64}\.(mp3|wav)"), "/{audio}"),
]


def route_shape(path: str) -> str:
    for pattern, repl in _IDS:
        path = pattern.sub(repl, path)
    return path


@dataclass
class _Route:
    count: int = 0
    errors: int = 0
    latencies: deque[float] = field(default_factory=lambda: deque(maxlen=512))


def _quantile_ms(sorted_s: list[float], q: float) -> float | None:
    if not sorted_s:
        return None
    return round(sorted_s[min(len(sorted_s) - 1, int(q * len(sorted_s)))] * 1000, 1)


class Metrics:
    def __init__(self) -> None:
        self.routes: dict[str, _Route] = {}
        self.started = time.time()
        self.recent: deque[float] = deque(maxlen=20_000)  # completion times, for requests per minute

    def record(self, method: str, path: str, status: int, seconds: float, streaming: bool) -> None:
        if not path.startswith("/api"):
            return
        key = f"{method} {route_shape(path)}"
        r = self.routes.get(key)
        if r is None:
            if len(self.routes) > 400:
                return  # unknown paths (scanners) must not grow this without bound
            r = self.routes[key] = _Route()
        r.count += 1
        if status >= 500:
            r.errors += 1
        if not streaming:
            r.latencies.append(seconds)
        self.recent.append(time.time())

    def snapshot(self) -> dict[str, object]:
        now = time.time()
        per_min = sum(1 for t in self.recent if now - t < 60)
        rows = []
        for key, r in self.routes.items():
            lat = sorted(r.latencies)
            rows.append(
                {
                    "route": key,
                    "count": r.count,
                    "errors": r.errors,
                    "p50_ms": _quantile_ms(lat, 0.5),
                    "p95_ms": _quantile_ms(lat, 0.95),
                }
            )
        rows.sort(key=lambda x: -int(x["count"]))  # type: ignore[call-overload]
        total = sum(r.count for r in self.routes.values())
        errors = sum(r.errors for r in self.routes.values())
        return {
            "uptime_s": round(now - self.started),
            "requests": total,
            "errors": errors,
            "per_minute": per_min,
            "routes": rows[:25],
        }
