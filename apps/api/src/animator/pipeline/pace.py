"""How fast a lesson's voice really speaks (ADR-063): the time budget of a lesson is in words, so it
needs the pace of the voice that will read them.

Every narrated scene adds one measurement (its caption words over the time from its first word to its
last, as recorded), per provider, voice and lesson language. A lesson is planned with the median of
the latest measurements of its voice, or the configured default until there are a few. The book is a
small JSON file next to the speech cache; losing it only means starting from the default again.
"""

from __future__ import annotations

import json
import logging
import os
import statistics
import threading
from pathlib import Path

log = logging.getLogger("animator.pace")

KEEP = 40  # measurements kept per voice (the newest)
LEAST = 3  # measurements needed before they replace the default
# A measurement outside this range is not a pace (a scene of two words, a failed timing).
SLOWEST, FASTEST = 1.2, 4.5
LEAST_WORDS = 8


class PaceBook:
    def __init__(self, path: Path | None, default_wps: float) -> None:
        self.path = path
        self.default = default_wps
        self._lock = threading.Lock()
        self._data: dict[str, list[float]] | None = None

    @staticmethod
    def key(provider: str, voice: str, language: str) -> str:
        return f"{provider}|{voice}|{language.strip().lower()}"

    def _load(self) -> dict[str, list[float]]:
        if self._data is None:
            data: dict[str, list[float]] = {}
            if self.path is not None:
                try:
                    raw = json.loads(self.path.read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        for k, v in raw.items():
                            if isinstance(v, list):
                                data[str(k)] = [float(x) for x in v if isinstance(x, (int, float))][-KEEP:]
                except (OSError, ValueError):
                    pass
            self._data = data
        return self._data

    def wps(self, key: str) -> float:
        """Words per second of this voice: the median of its recent measurements, or the default."""
        with self._lock:
            samples = self._load().get(key) or []
        return round(statistics.median(samples), 3) if len(samples) >= LEAST else self.default

    def samples(self, key: str) -> int:
        with self._lock:
            return len(self._load().get(key) or [])

    def record(self, key: str, words: int, seconds: float) -> None:
        """One scene's measurement (ignored when it cannot be a pace)."""
        if words < LEAST_WORDS or seconds <= 0:
            return
        pace = words / seconds
        if not SLOWEST <= pace <= FASTEST:
            return
        with self._lock:
            data = self._load()
            data[key] = [*(data.get(key) or []), round(pace, 3)][-KEEP:]
            snapshot = json.dumps(data, separators=(",", ":"))
        self._write(snapshot)

    def _write(self, text: str) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(self.path)
        except OSError as e:  # another process has it open (Windows): the next scene writes it
            log.debug("pace book not written (%s)", e)
