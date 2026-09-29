"""Generation persistence behind an interface (ADR-003: no database yet).

`FileStore` keeps one JSON document per generation plus an append-only LLM call log under
`<data dir>/generations/<id>/`; writes are atomic, so a crash never leaves a half-written record.
A SQL store (SQLite, then Postgres) implements the same protocol when it is needed.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any, Protocol

from animator.pipeline.models import Generation

ID_RE = re.compile(r"^g_[0-9a-f]{12}$")
# Windows refuses to replace or read a file another process has open for that moment: a scan by
# Windows Defender or the search indexer, or a concurrent reader. Such locks last milliseconds.
_LOCK_RETRIES = 12
_LOCK_WAIT_S = 0.05


def _retry_locked[T](op: Callable[[], T]) -> T:
    """Run a file operation, retrying for up to about 3 s while Windows reports the file locked."""
    for attempt in range(_LOCK_RETRIES):
        try:
            return op()
        except PermissionError:
            if attempt == _LOCK_RETRIES - 1:
                raise
            time.sleep(min(_LOCK_WAIT_S * 2**attempt, 0.5))
    raise AssertionError("unreachable")


class GenerationStore(Protocol):
    async def save(self, gen: Generation) -> None: ...

    async def get(self, gen_id: str) -> Generation | None: ...

    async def recent(self, limit: int = 50) -> list[Generation]: ...

    async def append_calls(self, gen_id: str, records: list[dict[str, Any]]) -> None: ...

    async def calls(self, gen_id: str) -> list[dict[str, Any]]: ...


class MemoryStore:
    def __init__(self) -> None:
        self._gens: dict[str, str] = {}
        self._calls: dict[str, list[dict[str, Any]]] = {}

    async def save(self, gen: Generation) -> None:
        self._gens[gen.id] = gen.model_dump_json()

    async def get(self, gen_id: str) -> Generation | None:
        raw = self._gens.get(gen_id)
        return Generation.model_validate_json(raw) if raw else None

    async def recent(self, limit: int = 50) -> list[Generation]:
        gens = [Generation.model_validate_json(v) for v in self._gens.values()]
        return sorted(gens, key=lambda g: g.created_at, reverse=True)[:limit]

    async def append_calls(self, gen_id: str, records: list[dict[str, Any]]) -> None:
        self._calls.setdefault(gen_id, []).extend(records)

    async def calls(self, gen_id: str) -> list[dict[str, Any]]:
        return list(self._calls.get(gen_id, []))


class FileStore:
    def __init__(self, root: Path) -> None:
        self.root = root / "generations"
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir(self, gen_id: str) -> Path:
        if not ID_RE.match(gen_id):
            raise ValueError(f"invalid generation id '{gen_id}'")
        return self.root / gen_id

    async def save(self, gen: Generation) -> None:
        data = gen.model_dump_json(indent=1)

        def write() -> None:
            d = self._dir(gen.id)
            d.mkdir(parents=True, exist_ok=True)
            tmp = d / "generation.json.tmp"
            _retry_locked(lambda: tmp.write_text(data, encoding="utf-8"))
            _retry_locked(lambda: tmp.replace(d / "generation.json"))

        await asyncio.to_thread(write)

    async def get(self, gen_id: str) -> Generation | None:
        try:
            path = self._dir(gen_id) / "generation.json"
        except ValueError:
            return None
        if not path.exists():
            return None
        text = await asyncio.to_thread(_retry_locked, lambda: path.read_text(encoding="utf-8"))
        return Generation.model_validate_json(text)

    async def recent(self, limit: int = 50) -> list[Generation]:
        def scan() -> list[Generation]:
            out = []
            for d in self.root.iterdir():
                f = d / "generation.json"
                if ID_RE.match(d.name) and f.exists():
                    try:
                        text = _retry_locked(partial(f.read_text, encoding="utf-8"))
                        out.append(Generation.model_validate_json(text))
                    except (ValueError, OSError):
                        continue  # incompatible version, or being rewritten: skip, never crash the list
            return sorted(out, key=lambda g: g.created_at, reverse=True)[:limit]

        return await asyncio.to_thread(scan)

    async def append_calls(self, gen_id: str, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        lines = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)

        def append() -> None:
            d = self._dir(gen_id)
            d.mkdir(parents=True, exist_ok=True)
            with (d / "calls.jsonl").open("a", encoding="utf-8") as f:
                f.write(lines)

        await asyncio.to_thread(_retry_locked, append)

    async def calls(self, gen_id: str) -> list[dict[str, Any]]:
        try:
            path = self._dir(gen_id) / "calls.jsonl"
        except ValueError:
            return []
        if not path.exists():
            return []
        text = await asyncio.to_thread(_retry_locked, lambda: path.read_text(encoding="utf-8"))
        return [json.loads(line) for line in text.splitlines() if line.strip()]
