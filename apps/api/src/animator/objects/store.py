"""Where saved objects live (ADR-055).

- The library: drawings made for the product, shipped in the package (`library/*.json`, built from
  `library/src/` by `python -m animator.objects build`). Read-only.
- The warehouse (`<objects dir>/warehouse/<id>.json`): drawings harvested from finished lessons or
  imported by an admin, each with its review status.
- Decisions on library objects (`<objects dir>/overrides/<id>.json`): an admin's edits to their
  names and words, or retiring one. The drawing itself never changes.
- `uses.jsonl`: one line per scene that placed a saved drawing (statistics only).
- `harvest.json`: which lessons the harvester has looked at, and the drawings it turned down.

Writes are atomic (a temporary file, then a rename), so a crash never leaves half a record. Only
the API process writes; the in-memory copy is reloaded when a folder changes.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from animator.objects.models import EDITABLE, SavedObject

LIBRARY_DIR = Path(__file__).parent / "library"
log = logging.getLogger("animator.objects")

_REVIEW = ("status", "note", "reviewed_at", "reviewed_by", "updated_at")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    # Windows refuses a replace for a moment while another process (a virus scan) reads the file.
    for attempt in range(12):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == 11:
                raise
            time.sleep(min(0.05 * 2**attempt, 0.5))


class ObjectStore:
    def __init__(self, root: Path, library: Path = LIBRARY_DIR) -> None:
        self.root = root
        self.library = library
        self.warehouse = root / "warehouse"
        self.overrides = root / "overrides"
        self._lock = threading.RLock()
        self._cache: dict[str, SavedObject] | None = None
        self._stamp: tuple[Any, ...] | None = None
        self._uses: tuple[tuple[int, int], dict[str, int]] | None = None
        # Files that could not be read, for the admin page (a bad file never stops the others).
        self.errors: list[str] = []

    # ------------------------------------------------------------------ reading

    def _folder_stamp(self) -> tuple[Any, ...]:
        out: list[Any] = []
        for d in (self.library, self.warehouse, self.overrides):
            try:
                st = d.stat()
                out.append((st.st_mtime_ns, sum(1 for _ in d.glob("*.json"))))
            except FileNotFoundError:
                out.append(None)
        return tuple(out)

    def _load(self) -> dict[str, SavedObject]:
        out: dict[str, SavedObject] = {}
        errors: list[str] = []
        for f in sorted(self.library.glob("*.json")):
            try:
                obj = SavedObject.model_validate_json(f.read_text(encoding="utf-8"))
            except (ValidationError, ValueError, OSError) as e:
                errors.append(f"library/{f.name}: {str(e)[:200]}")
                continue
            if obj.origin != "library" or obj.id != f.stem:
                errors.append(f"library/{f.name}: not a library object named {f.stem}")
                continue
            out[obj.id] = self._overridden(obj, errors)
        for f in sorted(self.warehouse.glob("*.json")):
            try:
                obj = SavedObject.model_validate_json(f.read_text(encoding="utf-8"))
            except (ValidationError, ValueError, OSError) as e:
                errors.append(f"warehouse/{f.name}: {str(e)[:200]}")
                continue
            if obj.id != f.stem or obj.origin == "library" or obj.id in out:
                errors.append(f"warehouse/{f.name}: its id is taken or does not match the file")
                continue
            out[obj.id] = obj
        self.errors = errors
        for problem in errors:
            log.warning("saved objects: %s", problem)
        return out

    def _overridden(self, obj: SavedObject, errors: list[str]) -> SavedObject:
        path = self.overrides / f"{obj.id}.json"
        if not path.exists():
            return obj
        try:
            changes = json.loads(path.read_text(encoding="utf-8"))
            allowed = {k: v for k, v in changes.items() if k in EDITABLE or k in _REVIEW}
            return SavedObject.model_validate({**obj.model_dump(), **allowed})
        except (ValidationError, ValueError, OSError) as e:
            errors.append(f"overrides/{path.name}: {str(e)[:200]} (the built-in version is used)")
            return obj

    def _all(self) -> dict[str, SavedObject]:
        with self._lock:
            stamp = self._folder_stamp()
            if self._cache is None or stamp != self._stamp:
                self._cache = self._load()
                self._stamp = stamp
            return self._cache

    def all(self) -> list[SavedObject]:
        return list(self._all().values())

    def get(self, obj_id: str) -> SavedObject | None:
        return self._all().get(obj_id)

    def approved(self) -> list[SavedObject]:
        return [o for o in self._all().values() if o.status == "approved"]

    def fingerprints(self) -> set[str]:
        return {o.fingerprint for o in self._all().values()}

    # ------------------------------------------------------------------ writing

    def save(self, obj: SavedObject) -> SavedObject:
        """Store an object: a library object's editable fields and review as an override, a warehouse
        object whole. Validated first."""
        obj = SavedObject.model_validate(obj.model_dump())
        with self._lock:
            if obj.origin == "library":
                if not (self.library / f"{obj.id}.json").exists():
                    raise ValueError(f"no built-in object '{obj.id}'")
                data = obj.model_dump(include=set(EDITABLE) | set(_REVIEW))
                _write(self.overrides / f"{obj.id}.json", json.dumps(data, ensure_ascii=False, indent=1))
            else:
                taken = self.get(obj.id)
                if taken is not None and taken.origin == "library":
                    raise ValueError(f"'{obj.id}' is a built-in object's id")
                _write(self.warehouse / f"{obj.id}.json", obj.model_dump_json(indent=1))
            self._cache = None
        return obj

    def delete(self, obj_id: str) -> bool:
        """Delete a warehouse object (built-in ones can only be retired)."""
        with self._lock:
            obj = self.get(obj_id)
            if obj is None:
                return False
            if obj.origin == "library":
                raise ValueError("built-in objects cannot be deleted; retire it instead")
            (self.warehouse / f"{obj_id}.json").unlink(missing_ok=True)
            self._cache = None
            return True

    def reset(self, obj_id: str) -> SavedObject | None:
        """A library object back to how it was built (its override removed)."""
        with self._lock:
            (self.overrides / f"{obj_id}.json").unlink(missing_ok=True)
            self._cache = None
        return self.get(obj_id)

    # ------------------------------------------------------------------ harvest state, uses

    def read_state(self) -> dict[str, Any]:
        path = self.root / "harvest.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def write_state(self, state: dict[str, Any]) -> None:
        with self._lock:
            _write(self.root / "harvest.json", json.dumps(state, ensure_ascii=False, separators=(",", ":")))

    def record_uses(self, gen_id: str, scene_id: str, refs: list[str]) -> None:
        if not refs:
            return
        now = round(time.time(), 1)
        lines = "".join(
            json.dumps({"t": now, "g": gen_id, "s": scene_id, "o": r.split("@")[0]}) + "\n" for r in refs
        )
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            with (self.root / "uses.jsonl").open("a", encoding="utf-8") as f:
                f.write(lines)

    def use_counts(self) -> dict[str, int]:
        """Scenes that placed each object (a scene counted once per object)."""
        path = self.root / "uses.jsonl"
        try:
            st = path.stat()
        except FileNotFoundError:
            return {}
        key = (st.st_size, st.st_mtime_ns)
        with self._lock:
            if self._uses is not None and self._uses[0] == key:
                return self._uses[1]
            seen: set[tuple[str, str, str]] = set()
            try:
                with path.open(encoding="utf-8") as f:
                    for line in f:
                        try:
                            r = json.loads(line)
                            seen.add((str(r["g"]), str(r["s"]), str(r["o"])))
                        except (ValueError, KeyError, TypeError):
                            continue
            except OSError:
                return {}
            counts: dict[str, int] = {}
            for _, _, o in seen:
                counts[o] = counts.get(o, 0) + 1
            self._uses = (key, counts)
            return counts
