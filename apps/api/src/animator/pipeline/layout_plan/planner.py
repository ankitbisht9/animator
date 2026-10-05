"""The layout planner (ADR-047): from a valid scene and its stage plan to the same scene, laid out
precisely in the fixed frame. No model call.

    the scene, frozen (moves replaced by pauses)  -> engine frames
      -> figures: each planned item's objects, labels, measured core and footprint, time on stage
      -> solve: groups in lanes of their regions, one scale as large as fits, items placed
      -> rewrite: every coordinate, move and keyframe of the scene
      -> candidates for the pipeline to compile and keep only if the engine finds them clean

Two candidates: the plan as written (rows, grids, cycles), then the same items kept exactly as drawn
relative to each other and only sized and centred (when the first does not come out clean).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from animator.engine_client import EngineClient
from animator.engine_client.errors import EngineError
from animator.pipeline.layout_plan.arrange import Group, Solved, solve_region
from animator.pipeline.layout_plan.figures import Measured, frozen, measure, rigid_reason, sample_times
from animator.pipeline.layout_plan.plan import StagePlan
from animator.pipeline.layout_plan.regions import Box, content_regions
from animator.pipeline.layout_plan.rewrite import Rewriter, Similarity, fit_into, footprint
from animator_contracts.protocol import CompileResult, SceneState

log = logging.getLogger("animator.layout_plan")

ENVELOPE_MARGIN = 6.0  # what moves stays this far inside its region
DEFAULT_CAMERA = {"center": {"x": 800, "y": 450}, "zoom": 1}


def _framed(dsl: dict[str, Any]) -> bool:
    """A camera that does not show the canvas as it is."""
    cam = dsl.get("camera") or {}
    center = cam.get("center") or {}
    return (
        abs(float(center.get("x", 800)) - 800) > 0.5
        or abs(float(center.get("y", 450)) - 450) > 0.5
        or abs(float(cam.get("zoom") or 1) - 1) > 1e-3
    )


@dataclass
class Candidate:
    name: str
    dsl: dict[str, Any]
    fits: bool
    scale: dict[str, float]
    least: float = 1.0  # the smallest scale anything ends up at (with the corrections)


@dataclass
class Planned:
    """What the planner made of a scene: candidates, best first, and a report (kept on the scene)."""

    candidates: list[Candidate] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)


def _solve(m: Measured, groups: dict[str, list[Group]]) -> Solved:
    regions = content_regions(m.layout)
    placed: dict[str, Any] = {}
    lanes: dict[str, Box] = {}
    scale: dict[str, float] = {}
    fits = True
    for region, gs in groups.items():
        if not gs:
            continue
        solved = solve_region(regions[region], gs, m.direction)
        placed.update(solved.placed)
        lanes.update(solved.lanes)
        scale.update(solved.scale)
        fits = fits and solved.fits
    return Solved(placed, lanes, scale, fits)


def _as_drawn(m: Measured) -> dict[str, list[Group]]:
    """The fallback: each region's items as one free group (their arrangement exactly as drawn)."""
    out: dict[str, list[Group]] = {}
    for region, gs in m.groups.items():
        items = [it for g in gs for it in g.items]
        if not items:
            continue
        out[region] = [
            Group(
                id=f"{region}_as_drawn",
                kind="free",
                items=items,
                region=region,
                t0=min(g.t0 for g in gs),
                t1=max(g.t1 for g in gs),
            )
        ]
    return out


class LayoutPlanner:
    def __init__(self, engine: EngineClient) -> None:
        self.engine = engine

    async def plan(
        self,
        dsl: dict[str, Any],
        plan: StagePlan,
        res: CompileResult,
        prev_state: SceneState | None,
    ) -> Planned:
        report: dict[str, Any] = {"plan": plan.model_dump(exclude_none=True)}
        out = Planned(report=report)
        # A draft whose only problems are layout errors (things overlapping or off the stage) is
        # what the planner is for: it compiled, so it can be measured.
        if res.compiled is None:
            res = await self.engine.compile(dsl, prev_state, lenient=True)
        if res.compiled is None:
            report["skipped"] = "the scene did not compile"
            return out
        reason = rigid_reason(dsl)
        if reason:
            report["skipped"] = reason
            return out
        # The plan places things on the screen itself: the camera shows the canvas as it is (the
        # regions are where the title bar and the captions leave room on screen).
        if _framed(dsl):
            dsl = {**dsl, "camera": dict(DEFAULT_CAMERA)}
            res = await self.engine.compile(dsl, prev_state, lenient=True)
            if res.compiled is None:
                report["skipped"] = "the scene does not compile with the camera reset"
                return out
        duration = float(res.duration or dsl.get("duration") or 0)
        try:
            still = await self.engine.sample(
                frozen(dsl, res.compiled), sample_times(duration, res.compiled), prev_state, lenient=True
            )
        except EngineError as e:
            report["skipped"] = f"engine: {e}"
            return out
        if not still.frames:
            report["skipped"] = "the frozen scene did not compile"
            return out
        m, why = measure(dsl, plan, still.frames)
        if m is None:
            report["skipped"] = why
            return out
        starts = await self._starts(dsl, res, prev_state)
        report["items"] = {
            fid: {
                "members": sorted(f.members),
                "attached": sorted(f.attached),
                "core": footprint(f.core),
                "foot": footprint(f.foot),
                "scalable": f.scalable,
            }
            for fid, f in m.figures.items()
        }
        report["unplanned"] = m.unplanned
        times = sample_times(duration, res.compiled)
        for name, groups in (("plan", m.groups), ("as_drawn", _as_drawn(m))):
            solved = _solve(m, groups)
            rw = Rewriter(m, solved.placed)
            rewritten = rw.rewrite(dsl, starts)
            # Motion is not in the footprints (they are measured at rest): if anything now travels
            # out of its region, the region's content is brought back in as one piece.
            corrected: dict[str, float] = {}
            for _ in range(2):
                fixes = await self._envelope_fixes(rewritten, m, rw, times, prev_state)
                if not fixes:
                    break
                for region, c in fixes.items():
                    rw.correct(region, c)
                    corrected[region] = round(corrected.get(region, 1.0) * c.k, 3)
                rewritten = rw.rewrite(dsl, starts)
            region_of = {g.id: g.region for gs in groups.values() for g in gs}
            least = min(
                (k * corrected.get(region_of.get(gid, ""), 1.0) for gid, k in solved.scale.items()),
                default=1.0,
            )
            out.candidates.append(Candidate(name, rewritten, solved.fits, solved.scale, round(least, 3)))
            report.setdefault("scales", {})[name] = {k: round(v, 3) for k, v in solved.scale.items()}
            report.setdefault("fits", {})[name] = solved.fits
            if corrected:
                report.setdefault("corrected", {})[name] = corrected
        return out

    async def _envelope_fixes(
        self,
        dsl: dict[str, Any],
        m: Measured,
        rw: Rewriter,
        times: list[float],
        prev_state: SceneState | None,
    ) -> dict[str, Similarity]:
        """For each region whose content, at any moment, reaches outside it: the correction that
        brings it all back in."""
        try:
            sample = await self.engine.sample(dsl, times, prev_state, lenient=True)
        except EngineError:
            return {}
        if not sample.frames:
            return {}
        regions = content_regions(m.layout)
        owner_region: dict[str, str] = {}
        envelope: dict[str, Box] = {}
        for frame in sample.frames:
            for o in frame.get("objects") or []:
                oid = str(o.get("id"))
                if (
                    (o.get("alpha") or 0) <= 0.05
                    or o.get("type") == "group"
                    or (o.get("presence") or 0) < 0.95
                ):
                    continue
                if oid not in owner_region:
                    fid = rw.owner(oid)
                    owner_region[oid] = rw.region_of.get(fid or "", "")
                region = owner_region[oid]
                b = o.get("bbox")
                if not region or not isinstance(b, dict):
                    continue
                box = Box.of(b)
                envelope[region] = envelope[region].union(box) if region in envelope else box
        fixes: dict[str, Similarity] = {}
        for region, env in envelope.items():
            room = regions[region].inset(ENVELOPE_MARGIN, ENVELOPE_MARGIN)
            c = fit_into(env, room)
            if c is not None:
                fixes[region] = c
        return fixes

    async def _starts(
        self, dsl: dict[str, Any], res: CompileResult, prev_state: SceneState | None
    ) -> dict[str, tuple[float, float]]:
        """Where each object moved `by` an offset is when its move starts (its centre, as drawn)."""
        times = (res.compiled or {}).get("actions") or {}
        wanted = {
            str(a["id"]): (str(a.get("target")), float((times.get(a["id"]) or {}).get("start", 0)))
            for a in dsl.get("timeline") or []
            if a.get("action") == "move" and a.get("by") and a.get("id")
        }
        if not wanted:
            return {}
        stamps = sorted({round(t, 4) for _, t in wanted.values()})
        try:
            sample = await self.engine.sample(dsl, stamps, prev_state, lenient=True)
        except EngineError:
            return {}
        if not sample.frames:
            return {}
        at = {round(float(f.get("t", 0)), 4): f for f in sample.frames}
        out: dict[str, tuple[float, float]] = {}
        for aid, (target, t) in wanted.items():
            frame = at.get(round(t, 4))
            o = next((x for x in (frame or {}).get("objects") or [] if x.get("id") == target), None)
            b = (o or {}).get("bbox")
            if isinstance(b, dict):
                out[aid] = (float(b["x"]) + float(b["w"]) / 2, float(b["y"]) + float(b["h"]) / 2)
        return out
