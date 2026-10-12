"""The warehouse's harvest (ADR-055, Plan 2): drawings the scene writer built from shapes in finished
lessons, kept as candidates for an admin to review.

A candidate is one drawing of one named thing:
- a `group` whose children are all plain shapes (circle, ellipse, rectangle, polygon, path, line), or
- shapes sharing an id prefix (`cart_body`, `cart_wheel_1`: the planned flow's convention for one
  drawing), or one closed path with some detail,
and the scene's brief must name it (`grape_cluster: a bunch of purple grapes`): that gives it its
name and what it is. Drawings with text, icons or components in them are left alone.

The engine draws the scene; the shapes are taken at a moment when all of them are on screen, in
their exact places (moves, scales and groups applied), turned into path data, and checked like any
saved drawing (quality.py), plus: the shapes form one connected drawing, it is big enough, its scene
was accepted. A candidate is never used by a lesson until an admin approves it (and may first rename
it, add the words it is used for, and label its parts). The harvest never changes a lesson, reads
each lesson once, remembers drawings it turned down, and works only while no lesson is being made.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from animator.config import Settings
from animator.engine_client import EngineClient
from animator.engine_client.errors import EngineError
from animator.jobs.store import GenerationStore
from animator.objects import art as A
from animator.objects.matching import IRREGULAR
from animator.objects.models import Check, SavedObject, Size, Source, paint_ok
from animator.objects.quality import check, to_art, with_anchors
from animator.objects.store import ObjectStore
from animator.objects.svg import RawShape, part_name
from animator.pipeline.models import Generation, SceneRecord
from animator_contracts.protocol import SceneState

log = logging.getLogger("animator.objects.harvest")

SHAPES = {"circle", "ellipse", "rectangle", "polygon", "path", "line"}
# Names of pictures that are not one thing (a chart, a map, a road layout, a scene, a diagram of
# boxes): never kept, however well drawn.
NOT_THINGS = {
    "area", "arrow", "arrows", "axis", "background", "bar", "board", "box", "boxes", "callout", "card",
    "chart", "column", "cutaway", "diagram", "flow", "frame", "graph", "grid", "ground", "junction",
    "label", "landscape", "layout", "legend", "line", "lines", "map", "panel", "pie", "region", "road",
    "route", "row", "scene", "screen", "section", "table", "timeline", "track", "trail", "tree", "window",
    "zone", "example", "generic", "sample", "vignette", "wave", "loop", "path", "material", "placeholder",
}  # fmt: skip
MIN_SHAPES = 3
KAPPA = 0.5522847498
MIN_SIZE = 60.0  # canvas px: smaller drawings are details, not things worth keeping
MIN_SCORE = 0.6
PER_CONCEPT = 3  # candidates waiting for review per thing (the best are kept)
# The checks a harvested drawing must pass (the rest only lower its score).
REQUIRED = {"paths", "fits", "parts_visible", "anchors_inside", "anchors_on_parts", "proportions", "painted"}


def ellipse_segs(rx: float, ry: float) -> list[A.Seg]:
    kx, ky = rx * KAPPA, ry * KAPPA
    return [
        ("M", rx, 0.0),
        ("C", rx, ky, kx, ry, 0.0, ry),
        ("C", -kx, ry, -rx, ky, -rx, 0.0),
        ("C", -rx, -ky, -kx, -ry, 0.0, -ry),
        ("C", kx, -ry, rx, -ky, rx, 0.0),
        ("Z",),
    ]


def rect_segs(w: float, h: float, r: float) -> list[A.Seg]:
    x0, y0, x1, y1 = -w / 2, -h / 2, w / 2, h / 2
    r = max(0.0, min(r, w / 2, h / 2))
    if r <= 0:
        return [("M", x0, y0), ("L", x1, y0), ("L", x1, y1), ("L", x0, y1), ("Z",)]
    k = r * (1 - KAPPA)
    return [
        ("M", x0 + r, y0),
        ("L", x1 - r, y0),
        ("C", x1 - k, y0, x1, y0 + k, x1, y0 + r),
        ("L", x1, y1 - r),
        ("C", x1, y1 - k, x1 - k, y1, x1 - r, y1),
        ("L", x0 + r, y1),
        ("C", x0 + k, y1, x0, y1 - k, x0, y1 - r),
        ("L", x0, y0 + r),
        ("C", x0, y0 + k, x0 + k, y0, x0 + r, y0),
        ("Z",),
    ]


def polygon_segs(points: list[tuple[float, float]]) -> list[A.Seg]:
    segs: list[A.Seg] = [("M", *points[0])]
    segs += [("L", *p) for p in points[1:]]
    return [*segs, ("Z",)]


def _num(v: Any, d: float) -> float:
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v) else d


def _pt(v: Any) -> tuple[float, float] | None:
    if isinstance(v, dict) and isinstance(v.get("x"), int | float) and isinstance(v.get("y"), int | float):
        return float(v["x"]), float(v["y"])
    return None


def local_shape(kind: str, params: dict[str, Any]) -> tuple[list[A.Seg], bool] | None:
    """A shape's outline in its own frame (a line's in the canvas's), and whether it is filled."""
    if kind == "circle":
        r = _num(params.get("radius"), 50)
        return ellipse_segs(r, r), True
    if kind == "ellipse":
        return ellipse_segs(_num(params.get("rx"), 80), _num(params.get("ry"), 50)), True
    if kind == "rectangle":
        w, h = _num(params.get("width"), 200), _num(params.get("height"), 120)
        return rect_segs(w, h, _num(params.get("cornerRadius"), 0)), True
    if kind == "polygon":
        raw = params.get("points")
        pts = [p for p in (_pt(v) for v in raw) if p] if isinstance(raw, list) else []
        if len(pts) < 3:
            n = max(3, min(12, round(_num(params.get("sides"), 6))))
            r = _num(params.get("radius"), 60)
            pts = [
                (
                    r * math.cos(-math.pi / 2 + 2 * math.pi * i / n),
                    r * math.sin(-math.pi / 2 + 2 * math.pi * i / n),
                )
                for i in range(n)
            ]
        return polygon_segs(pts), True
    if kind == "path":
        d = params.get("__morphD") if isinstance(params.get("__morphD"), str) else params.get("d")
        if not isinstance(d, str):
            return None
        try:
            segs = A.parse_path(d)
        except A.PathError:
            return None
        return segs, params.get("closed") is True or isinstance(params.get("__morphD"), str)
    if kind == "line":
        a, b = _pt(params.get("from")), _pt(params.get("to"))
        return ([("M", *a), ("L", *b)], False) if a and b else None
    return None


@dataclass
class Candidate:
    key: str  # the drawing's id (a group's, or the shared prefix)
    members: list[str]  # its shapes' object ids, in paint order
    entry: str  # the brief's "id: description" that names it


def _words(oid: str) -> list[str]:
    return [w for w in re.split(r"[_\W]+", oid.lower()) if w]


def concept_of(text: str) -> str:
    """A thing's name from an id or a brief's words, singular ("grapes_cluster" -> "grape cluster")."""
    out = []
    for w in _words(text):
        if w in IRREGULAR:
            w = IRREGULAR[w]
        elif len(w) > 4 and w.endswith("ies"):
            w = w[:-3] + "y"
        elif len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
            w = w[:-1]
        out.append(w)
    return " ".join(out)


def _entry_for(key: str, entries: list[str]) -> str | None:
    """The brief's object entry ("id: description") that names this drawing."""
    for e in entries:
        eid = e.split(":", 1)[0].strip().lower()
        if eid and (eid == key or eid.startswith(key + "_") or key.startswith(eid + "_")):
            return e
    return None


def candidates(dsl: dict[str, Any], entries: list[str]) -> list[Candidate]:
    objects = [o for o in dsl.get("objects") or [] if isinstance(o, dict) and isinstance(o.get("id"), str)]
    by_id = {o["id"]: o for o in objects}
    order = {o["id"]: i for i, o in enumerate(objects)}
    children: dict[str, list[str]] = {}
    for o in objects:
        if isinstance(o.get("parent"), str):
            children.setdefault(o["parent"], []).append(o["id"])
    out: list[Candidate] = []
    taken: set[str] = set()

    def descendants(gid: str) -> list[str] | None:
        found: list[str] = []
        for cid in children.get(gid, []):
            kind = by_id[cid].get("type")
            if kind == "group":
                sub = descendants(cid)
                if sub is None:
                    return None
                found += sub
            elif kind in SHAPES:
                found.append(cid)
            else:
                return None  # text, an icon, a component: not a drawing made of shapes only
        return found

    def wanted(key: str, members: list[str] | None) -> str | None:
        """The brief entry naming a drawing worth keeping: of a thing, with some detail."""
        entry = _entry_for(key.lower(), entries)
        if not entry or not members or len(members) < MIN_SHAPES:
            return None
        words = set(_words(entry.split(":", 1)[0])) | set(_words(key))
        return None if words & NOT_THINGS else entry

    for o in objects:
        if o.get("type") != "group" or o.get("parent"):
            continue
        members = descendants(o["id"])
        if members and (entry := wanted(o["id"], members)):
            out.append(Candidate(o["id"], sorted(members, key=order.__getitem__), entry))
            taken.update(members)
    families: dict[str, list[str]] = {}
    for o in objects:
        if o["id"] in taken or o.get("parent") or o.get("type") not in SHAPES:
            continue
        families.setdefault(_words(o["id"])[0] if _words(o["id"]) else o["id"], []).append(o["id"])
    for prefix, members in families.items():
        if entry := wanted(prefix, members):
            out.append(Candidate(prefix, sorted(members, key=order.__getitem__), entry))
    return out


def _paint(style_value: Any, frame_value: Any) -> str | None:
    """A shape's color: the role the scene gave it (it follows the theme), else the color it was drawn in."""
    if isinstance(style_value, str) and paint_ok(style_value):
        return style_value
    return frame_value if isinstance(frame_value, str) and paint_ok(frame_value) else None


def _matrix(fo: dict[str, Any]) -> A.Matrix | None:
    m = fo.get("matrix")
    if isinstance(m, list) and len(m) == 6 and all(isinstance(v, int | float) for v in m):
        return (float(m[0]), float(m[1]), float(m[2]), float(m[3]), float(m[4]), float(m[5]))
    return None


def _box(fo: dict[str, Any]) -> A.Box | None:
    b = fo.get("bbox")
    if not isinstance(b, dict):
        return None
    x, y, w, h = (float(b.get(k) or 0) for k in ("x", "y", "w", "h"))
    return A.Box(x, y, x + w, y + h)


def connected(boxes: list[A.Box], gap: float = 8.0) -> bool:
    """Do the shapes form one drawing (each touches, or nearly, another of it)?"""
    if len(boxes) < 2:
        return True
    seen = {0}
    frontier = [0]
    while frontier:
        i = frontier.pop()
        for j, b in enumerate(boxes):
            if j not in seen and boxes[i].grow(gap).overlaps(b):
                seen.add(j)
                frontier.append(j)
    return len(seen) == len(boxes)


@dataclass
class Harvested:
    obj: SavedObject | None
    reason: str = ""  # why it was turned down


def build_candidate(
    cand: Candidate, dsl: dict[str, Any], frame: dict[str, Any], gen: Generation, rec: SceneRecord
) -> Harvested:
    objects = {o["id"]: o for o in dsl.get("objects") or [] if isinstance(o, dict) and "id" in o}
    shown = {fo.get("id"): fo for fo in frame.get("objects") or [] if isinstance(fo, dict)}
    shapes: list[RawShape] = []
    boxes: list[A.Box] = []
    for oid in cand.members:
        fo, o = shown.get(oid), objects.get(oid, {})
        m = _matrix(fo) if fo else None
        if fo is None or m is None or float(fo.get("alpha") or 0) < 0.25:
            return Harvested(None, f"{oid} is not on screen with the rest")
        outline = local_shape(str(fo.get("type")), fo.get("params") or {})
        if outline is None:
            return Harvested(None, f"{oid} has no outline")
        local, filled = outline
        given = o.get("style")
        style: dict[str, Any] = given if isinstance(given, dict) else {}
        fill = _paint(style.get("fill"), fo.get("fill")) if filled else "none"
        stroke = _paint(style.get("stroke"), fo.get("stroke"))
        width = _num(fo.get("strokeWidth"), 0) * A.scale_of(m)
        if not filled and (stroke is None or width <= 0):
            continue
        part = part_name(oid[len(cand.key) + 1 :] if oid.lower().startswith(cand.key.lower() + "_") else oid)
        shapes.append(
            RawShape(
                segs=A.transform(local, m),
                fill=fill or "none",
                stroke=stroke if width > 0 else "none",
                stroke_width=width if stroke else 0.0,
                opacity=max(0.0, min(1.0, float(fo.get("alpha") or 1))),
                evenodd=False,
                part=part,
            )
        )
        if (b := _box(fo)) is not None:
            boxes.append(b)
    if len(shapes) < MIN_SHAPES:
        return Harvested(None, "too few shapes")
    if not connected(boxes):
        return Harvested(None, "its shapes are scattered, not one drawing")
    whole = boxes[0]
    for b in boxes[1:]:
        whole = whole.union(b)
    if whole.w < MIN_SIZE or whole.h < MIN_SIZE:
        return Harvested(None, f"too small ({whole.w:.0f} x {whole.h:.0f} px)")
    eid, _, desc = cand.entry.partition(":")
    concept = concept_of(eid) or concept_of(cand.key)
    try:
        art, spots = with_anchors(to_art(shapes))
    except ValueError as e:
        return Harvested(None, str(e))
    parts = {p: p.replace("_", " ") for p in art.parts}
    width = round(max(MIN_SIZE, min(whole.w, 1500.0)))
    fingerprint = art.fingerprint()
    title = concept[:1].upper() + concept[1:]
    try:
        obj = SavedObject(
            id=f"{re.sub(r'[^a-z0-9]+', '_', concept)[:48].strip('_') or 'drawing'}_{fingerprint[:6]}",
            title=title[:80],
            concept=concept[:80],
            description=desc.strip()[:300],
            aliases=[concept[:60]],
            parts=parts,
            size=Size(
                min=max(24.0, round(width * 0.5)), default=float(width), max=min(1500.0, round(width * 1.8))
            ),
            art=art,
            status="candidate",
            origin="harvest",
            source=Source(
                kind="harvest",
                generation_id=gen.id,
                scene_id=rec.id,
                object_id=cand.key,
                lesson_title=(gen.plan.title if gen.plan else gen.request.topic)[:120],
            ),
            fingerprint=fingerprint,
        )
    except ValueError as e:
        return Harvested(None, f"not a valid drawing ({str(e)[:120]})")
    quality = check(obj, spots)
    quality.checks.append(Check(name="from_a_finished_scene", ok=True, detail=f"{gen.id} / {rec.id}"))
    failed = [c.name for c in quality.checks if not c.ok and c.name in REQUIRED]
    if failed or quality.score < MIN_SCORE:
        return Harvested(None, f"failed checks: {', '.join(failed) or 'score'} ({quality.score:.2f})")
    return Harvested(obj.model_copy(update={"quality": quality}))


@dataclass
class Report:
    lessons: int = 0
    scenes: int = 0
    added: list[str] = field(default_factory=list)
    turned_down: int = 0
    seconds: float = 0.0


class Harvester:
    """Runs in the API process: every `animator_objects_harvest_every_s`, a few unread lessons."""

    def __init__(
        self,
        store: ObjectStore,
        engine: EngineClient,
        lessons: GenerationStore,
        settings: Settings,
        busy: Callable[[], bool] = lambda: False,
    ) -> None:
        self.store = store
        self.engine = engine
        self.lessons = lessons
        self.settings = settings
        self.busy = busy
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="objects-harvest")

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        await asyncio.sleep(30)  # let the API settle first
        while True:
            try:
                if not self.busy():
                    await self.run(limit=10)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("object harvest failed (it tries again later)")
            await asyncio.sleep(self.settings.animator_objects_harvest_every_s)

    async def run(self, limit: int = 10, force: bool = False) -> Report:
        """Read up to `limit` finished lessons not read before (or changed since); it stops when a
        lesson starts being made, unless `force` (an admin asked)."""
        async with self._lock:
            t0 = time.perf_counter()
            report = Report()
            state = await asyncio.to_thread(self.store.read_state)
            seen: dict[str, float] = dict(state.get("seen") or {})
            rejected: list[str] = list(state.get("rejected") or [])
            for gen in await self.lessons.recent(limit=100_000):
                if report.lessons >= limit or (self.busy() and not force):
                    break
                if gen.status != "complete" or seen.get(gen.id) == gen.updated_at:
                    continue
                report.lessons += 1
                await self._lesson(gen, report, rejected)
                seen[gen.id] = gen.updated_at
            state.update(
                seen=seen,
                rejected=rejected[-5000:],
                last_run=time.time(),
                last={
                    "lessons": report.lessons,
                    "added": len(report.added),
                    "turned_down": report.turned_down,
                },
            )
            await asyncio.to_thread(self.store.write_state, state)
            report.seconds = round(time.perf_counter() - t0, 2)
            if report.added:
                log.info(
                    "object harvest: %d candidate(s) from %d lesson(s)", len(report.added), report.lessons
                )
            return report

    async def _lesson(self, gen: Generation, report: Report, rejected: list[str]) -> None:
        prev: SceneRecord | None = None
        for rec in gen.scenes:
            if rec.status == "ready" and rec.dsl:
                report.scenes += 1
                try:
                    await self._scene(gen, rec, prev, report, rejected)
                except EngineError as e:
                    log.warning("object harvest: %s/%s could not be drawn (%s)", gen.id, rec.id, e)
            prev = rec

    async def _scene(
        self,
        gen: Generation,
        rec: SceneRecord,
        prev: SceneRecord | None,
        report: Report,
        rejected: list[str],
    ) -> None:
        assert rec.dsl is not None
        found = candidates(rec.dsl, list(rec.brief.objects_new))
        if not found:
            return
        duration = float(rec.dsl.get("duration") or rec.duration or 10)
        times = [round(duration * f, 2) for f in (0.35, 0.6, 0.85, 0.98)]
        fresh = bool((rec.dsl.get("transition_in") or {}).get("fresh"))
        prev_state = (
            SceneState.model_validate(prev.end_state)
            if prev is not None and prev.end_state and not fresh
            else None
        )
        sample = await self.engine.sample(rec.dsl, times, prev_state)
        if not sample.ok or not sample.frames:
            return
        known = await asyncio.to_thread(self.store.fingerprints)
        for cand in found:
            frame = _best_frame(sample.frames, cand.members)
            if frame is None:
                report.turned_down += 1
                continue
            got = build_candidate(cand, rec.dsl, frame, gen, rec)
            if got.obj is None:
                report.turned_down += 1
                continue
            if got.obj.fingerprint in known or got.obj.fingerprint in rejected:
                continue
            if await asyncio.to_thread(self._keep, got.obj):
                report.added.append(got.obj.id)
                known.add(got.obj.fingerprint)
            else:
                rejected.append(got.obj.fingerprint)

    def _keep(self, obj: SavedObject) -> bool:
        """Keep a candidate unless enough better ones of the same thing are waiting for review."""
        waiting = [
            o
            for o in self.store.all()
            if o.status == "candidate" and o.origin == "harvest" and o.concept == obj.concept
        ]
        score = obj.quality.score if obj.quality else 0.0
        if len(waiting) >= PER_CONCEPT:
            worst = min(waiting, key=lambda o: o.quality.score if o.quality else 0.0)
            if (worst.quality.score if worst.quality else 0.0) >= score:
                return False
            self.store.delete(worst.id)
        self.store.save(obj)
        return True


def _best_frame(frames: list[dict[str, Any]], members: list[str]) -> dict[str, Any] | None:
    """The latest sampled moment showing every shape of the drawing."""
    want = set(members)
    for frame in reversed(frames):
        here = {
            fo.get("id")
            for fo in frame.get("objects") or []
            if isinstance(fo, dict) and float(fo.get("alpha") or 0) >= 0.25
        }
        if want <= here:
            return frame
    return None
