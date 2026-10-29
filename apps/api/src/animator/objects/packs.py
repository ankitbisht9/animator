"""Drawing packs (ADR-066): open-licensed drawings shipped with the code beside the built-in library.

A pack is `packs/<name>.jsonl.gz`: its description on the first line (`{"pack": {...}}`), then one
SavedObject a line, read one at a time (a thousand drawings never sit in memory as one document). Its
drawings are
built-in (origin "library"): they come approved by the pack's curation (every check passed, named by a
model that looked at each drawing, reviewed on contact sheets), and an admin renames, retires or resets
them like any built-in drawing; they are never deleted. Each keeps its source (the pack, its author, its
licence): a lesson that places a drawing whose licence asks for credit lists it (`credits`).

Packs are made by `scripts/vendor_drawings.py` from the original files; nothing here downloads.

A pack holds a thousand detailed drawings: held whole they would take over 100 MB. Its drawings are kept
light (`thin`): their names, words, size and proportions, with the shapes and checks compressed aside
(about 7 MB), and made whole (`with_art`, under a millisecond) only where a drawing is placed in a scene
or shown to an admin. Matching scenes to drawings never needs the shapes.
"""

from __future__ import annotations

import gzip
import json
import logging
import zlib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from animator.objects.models import Art, Quality, SavedObject

PACKS_DIR = Path(__file__).parent / "packs"
log = logging.getLogger("animator.objects")

# The licences a pack may bring in: public domain, or credit given. Share-alike and no-derivatives
# licences are left out (a lesson adapts a drawing and is shared under its own terms).
LICENCES: dict[str, tuple[str, str]] = {
    "cc-0": ("CC0 1.0", "https://creativecommons.org/publicdomain/zero/1.0/"),
    "cc-by-3.0": ("CC BY 3.0", "https://creativecommons.org/licenses/by/3.0/"),
    "cc-by-4.0": ("CC BY 4.0", "https://creativecommons.org/licenses/by/4.0/"),
}

_CACHE: dict[Path, tuple[tuple[int, int], list[SavedObject], list[str], dict[str, Any]]] = {}
# A thin drawing's shapes and checks, compressed (by id), and the stand-in shape that marks it thin.
_WHOLE: dict[str, bytes] = {}
_STAND_IN = "M0 0Z"


def _thin(raw: dict[str, Any]) -> dict[str, Any]:
    art = raw.get("art") or {}
    quality = raw.get("quality") or None
    _WHOLE[str(raw.get("id"))] = zlib.compress(
        json.dumps({"art": art, "quality": quality}, separators=(",", ":")).encode("utf-8"), 6
    )
    light = {"w": art.get("w", 1), "h": art.get("h", 1), "shapes": [{"d": _STAND_IN, "fill": "none"}]}
    score = quality.get("score", 0.0) if isinstance(quality, dict) else 0.0
    return {**raw, "art": light, "quality": {"score": score, "checks": []} if quality else None}


def is_thin(obj: SavedObject) -> bool:
    shapes = obj.art.shapes
    return obj.id in _WHOLE and len(shapes) == 1 and shapes[0].d == _STAND_IN


def with_art(obj: SavedObject) -> SavedObject:
    """The drawing whole: a thin pack drawing with its shapes and checks back; anything else as it is."""
    if not is_thin(obj):
        return obj
    whole = json.loads(zlib.decompress(_WHOLE[obj.id]))
    quality = Quality.model_validate(whole["quality"]) if whole.get("quality") else obj.quality
    return obj.model_copy(update={"art": Art.model_validate(whole["art"]), "quality": quality})


def _read(path: Path) -> tuple[list[SavedObject], list[str], dict[str, Any]]:
    """A pack's drawings (thin), the problems found, and its own description (cached by file size and
    time)."""
    st = path.stat()
    key = (st.st_size, st.st_mtime_ns)
    hit = _CACHE.get(path)
    if hit is not None and hit[0] == key:
        return hit[1], hit[2], hit[3]
    objs: list[SavedObject] = []
    errors: list[str] = []
    meta: dict[str, Any] = {}
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for n, line in enumerate(f):
                if not line.strip():
                    continue
                raw = json.loads(line)
                if n == 0 and isinstance(raw, dict) and "pack" in raw:
                    meta = raw["pack"] or {}
                    continue
                obj = _object(raw, path, errors)
                if obj is not None:
                    objs.append(obj)
    except (OSError, ValueError, EOFError) as e:
        errors.append(f"packs/{path.name}: {str(e)[:200]}")
    _CACHE[path] = (key, objs, errors, meta)
    return objs, errors, meta


def _object(raw: Any, path: Path, errors: list[str]) -> SavedObject | None:
    """One line of a pack as a thin drawing, checked whole first (None, with the problem, if not)."""
    if not isinstance(raw, dict):
        errors.append(f"packs/{path.name}: a line is not a drawing")
        return None
    try:
        whole = SavedObject.model_validate(raw)
        if whole.origin != "library":
            errors.append(f"packs/{path.name}: {whole.id} is not a built-in drawing")
            return None
        return SavedObject.model_validate(_thin(raw))
    except ValidationError as e:
        errors.append(f"packs/{path.name}: {raw.get('id')}: {str(e)[:160]}")
        return None


def pack_files(folder: Path = PACKS_DIR) -> list[Path]:
    return sorted(folder.glob("*.jsonl.gz")) if folder.is_dir() else []


def load_packs(folder: Path = PACKS_DIR) -> tuple[list[SavedObject], list[str]]:
    """Every pack's drawings (in file order), and the problems found."""
    objs: list[SavedObject] = []
    errors: list[str] = []
    for f in pack_files(folder):
        o, e, _ = _read(f)
        objs += o
        errors += e
    return objs, errors


def packs_info(folder: Path = PACKS_DIR) -> list[dict[str, Any]]:
    """Each pack's description (name, title, where it comes from, licences) and its size."""
    out = []
    for f in pack_files(folder):
        o, _, meta = _read(f)
        out.append({**meta, "drawings": len(o)})
    return out


def credits(objs: Iterable[SavedObject]) -> list[dict[str, Any]]:
    """The credits a set of drawings needs: one entry per author, pack and licence (CC0 and the
    product's own drawings need none), with the drawings it covers (title, and where the original is)."""
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for o in objs:
        s = o.source
        if s.credit is None:
            continue
        key = (s.author or "", s.pack or "", s.license or "")
        entry = groups.setdefault(
            key,
            {
                "author": s.author,
                "pack": s.pack,
                "license": s.license,
                "license_url": s.license_url,
                "drawings": [],
            },
        )
        if all(d["title"] != o.title for d in entry["drawings"]):
            entry["drawings"].append({"title": o.title, "url": s.url})
    return sorted(groups.values(), key=lambda e: (str(e["pack"]), str(e["author"])))
