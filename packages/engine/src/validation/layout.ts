/**
 * Layout/geometry validation: evaluate the compiled scene at beat boundaries,
 * action ends and regular samples, and check what the viewer would actually see
 * (after camera): content off-screen, overlapping labels, text too small at
 * 1080p, low contrast, objects hidden under the caption bar, clutter.
 * Only settled objects are checked (entrances/exits in progress are transient).
 */
import { getSpec } from "../catalog/registry";
import { contrastRatio } from "../color/color";
import { LIMITS } from "../dsl/scene";
import { cameraMatrix, evaluate, type FrameObject } from "../evaluate/evaluate";
import { CANVAS_RECT, CAPTION_BAR } from "../layout/templates";
import { apply, multiply, transformRect } from "../math/matrix";
import { roundTo } from "../math/num";
import { area, contains, expand, intersection, type Rect } from "../math/rect";
import type { Vec2 } from "../math/vec";
import { getTheme } from "../theme/themes";
import type { CompiledScene } from "../timeline/types";
import { type IssueFix, issue, type ValidationIssue } from "./issues";

const EXPORT_SCALE = 1080 / 900;
/** Where an out-of-frame object is moved to: this far inside the frame edge. */
const FIT_MARGIN = 24;

/** The move (after an optional shrink about the box center) that brings a box inside the frame. */
export function fitHint(box: Rect): IssueFix | undefined {
  const inner = expand(CANVAS_RECT, -FIT_MARGIN);
  const scale = Math.min(1, inner.w / Math.max(1, box.w), inner.h / Math.max(1, box.h));
  const w = box.w * scale;
  const h = box.h * scale;
  const x = box.x + (box.w - w) / 2;
  const y = box.y + (box.h - h) / 2;
  const shift = (lo: number, size: number, min: number, max: number) =>
    lo < min ? min - lo : lo + size > max ? max - (lo + size) : 0;
  const dx = roundTo(shift(x, w, inner.x, inner.x + inner.w), 1);
  const dy = roundTo(shift(y, h, inner.y, inner.y + inner.h), 1);
  if (dx === 0 && dy === 0 && scale > 0.999) return undefined;
  return scale < 0.999 ? { dx, dy, scale: roundTo(scale, 3) } : { dx, dy };
}

const inRect = (p: Vec2, r: Rect) => p.x > r.x && p.x < r.x + r.w && p.y > r.y && p.y < r.y + r.h;

/** Things drawn through or around others by nature: effects, fluids, plots, containers, pipes. */
const NOT_SOLID = new Set([
  "particles",
  "flow_particles",
  "burst",
  "container",
  "pipe",
  "axes",
  "grid",
  "graph",
  "group",
  "line",
  "marker",
  "caption",
]);

/** Parts of one drawing share the first word of their ids (cart_body, cart_wheel_1). */
export function sameDrawing(a: string, b: string): boolean {
  return a.split("_")[0] === b.split("_")[0];
}

/** A box anchor of a world-space box (what "id.top" and friends resolve to). */
function boxAnchor(r: Rect, side: "top" | "bottom" | "left" | "right"): Vec2 {
  if (side === "top") return { x: r.x + r.w / 2, y: r.y };
  if (side === "bottom") return { x: r.x + r.w / 2, y: r.y + r.h };
  if (side === "left") return { x: r.x, y: r.y + r.h / 2 };
  return { x: r.x + r.w, y: r.y + r.h / 2 };
}

function segmentsCross(a: Vec2, b: Vec2, c: Vec2, d: Vec2): Vec2 | null {
  const rx = b.x - a.x;
  const ry = b.y - a.y;
  const sx = d.x - c.x;
  const sy = d.y - c.y;
  const den = rx * sy - ry * sx;
  if (Math.abs(den) < 1e-9) return null;
  const t = ((c.x - a.x) * sy - (c.y - a.y) * sx) / den;
  const u = ((c.x - a.x) * ry - (c.y - a.y) * rx) / den;
  return t >= 0 && t <= 1 && u >= 0 && u <= 1 ? { x: a.x + t * rx, y: a.y + t * ry } : null;
}

/** Whether two strokes cross, away from their ends (two arrows leaving the same anchor do not). */
export function polylinesCross(p: readonly Vec2[], q: readonly Vec2[], near = 14): boolean {
  if (p.length < 2 || q.length < 2) return false;
  const box = (pts: readonly Vec2[]) => {
    const xs = pts.map((v) => v.x);
    const ys = pts.map((v) => v.y);
    return {
      x: Math.min(...xs),
      y: Math.min(...ys),
      w: Math.max(...xs) - Math.min(...xs),
      h: Math.max(...ys) - Math.min(...ys),
    };
  };
  if (!intersection(expand(box(p), 1), expand(box(q), 1))) return false;
  const ends = [p[0], p[p.length - 1], q[0], q[q.length - 1]] as Vec2[];
  // Strokes are sampled about every 8 px; every other point is plenty to find a crossing.
  const thin = (pts: readonly Vec2[]) => pts.filter((_, i) => i % 2 === 0 || i === pts.length - 1);
  const a = thin(p);
  const b = thin(q);
  for (let i = 0; i + 1 < a.length; i++) {
    for (let j = 0; j + 1 < b.length; j++) {
      const x = segmentsCross(a[i] as Vec2, a[i + 1] as Vec2, b[j] as Vec2, b[j + 1] as Vec2);
      if (x && ends.every((e) => Math.hypot(x.x - e.x, x.y - e.y) > near)) return true;
    }
  }
  return false;
}

/** The shortest move (up, down, left or right, staying inside the frame) that takes box `a` clear
 * of box `b`, in world px under a camera `zoom`. */
export function clearHint(a: Rect, b: Rect, zoom = 1): IssueFix | undefined {
  const gap = FIT_MARGIN / 2;
  const inner = expand(CANVAS_RECT, -FIT_MARGIN);
  const moves = [
    { dx: b.x + b.w + gap - a.x, dy: 0 },
    { dx: b.x - gap - (a.x + a.w), dy: 0 },
    { dx: 0, dy: b.y + b.h + gap - a.y },
    { dx: 0, dy: b.y - gap - (a.y + a.h) },
  ].filter((m) => contains(inner, { x: a.x + m.dx, y: a.y + m.dy, w: a.w, h: a.h }, 0));
  if (!moves.length) return undefined;
  const best = moves.reduce((p, m) => (Math.hypot(m.dx, m.dy) < Math.hypot(p.dx, p.dy) ? m : p));
  const z = Math.max(0.01, zoom);
  return { dx: roundTo(best.dx / z, 1), dy: roundTo(best.dy / z, 1) };
}
/** A connector may touch a label's edge (e.g. an arrow ending at `label.left`), not run through it. */
const CONNECTOR_CLEARANCE = 6;
/** How far a rerouted connector keeps from everything it does not join. */
const ROUTE_CLEARANCE = 10;

function sampleTimes(cs: CompiledScene): number[] {
  const set = new Set<number>();
  const add = (t: number) => {
    if (t >= 0 && t <= cs.duration) set.add(roundTo(t, 3));
  };
  add(Math.min(0.05, cs.duration));
  add(cs.duration);
  for (const b of cs.beats) add(b.end - 0.05);
  for (const a of Object.values(cs.actions)) add(a.end + 0.02);
  for (let t = 1; t < cs.duration; t += 1) add(t);
  return [...set].sort((a, b) => a - b).slice(0, 80);
}

const scaleOf = (m: readonly number[]) => Math.hypot(m[0] ?? 1, m[1] ?? 0);

export function validateLayout(cs: CompiledScene): ValidationIssue[] {
  const theme = getTheme(cs.themeId);
  const out: ValidationIssue[] = [];
  const seen = new Set<string>();
  const pathOf = new Map(cs.objects.map((o) => [o.id, o.id]));
  const report = (
    code: string,
    key: string,
    message: string,
    t: number,
    severity: "error" | "warning",
    suggestion?: string,
    fix?: IssueFix,
  ) => {
    const k = `${code}|${key}`;
    if (seen.has(k)) return;
    seen.add(k);
    out.push(
      issue(code, `/objects/${pathOf.get(key.split("|")[0] ?? "") ?? ""}`, message, {
        severity,
        source: "layout",
        time: roundTo(t, 2),
        ...(suggestion ? { suggestion } : {}),
        ...(fix ? { fix } : {}),
      }),
    );
  };
  const parentOf = new Map(cs.objects.map((o) => [o.id, o.parent ?? o.links[0]?.group]));
  const followTarget = new Map(
    cs.objects.map((o) => [
      o.id,
      o.followPlace?.kind === "relative"
        ? o.followPlace.relativeTo
        : o.followPlace?.kind === "pin"
          ? o.followPlace.at.split(".")[0]
          : undefined,
    ]),
  );
  // Declared params (anchor references such as "bank.left" before they are resolved to points).
  const declared = new Map(cs.objects.map((o) => [o.id, o.params]));
  const placedOn = new Map(cs.objects.map((o) => [o.id, o.placedOn ?? followTarget.get(o.id)]));
  // Attached on purpose: grouped together, or one placed against / pinned to the other.
  const related = (a: string, b: string) =>
    placedOn.get(a) === b ||
    placedOn.get(b) === a ||
    parentOf.get(a) === b ||
    parentOf.get(b) === a ||
    (parentOf.get(a) !== undefined && parentOf.get(a) === parentOf.get(b));
  let clutterMax = 0;
  let clutterT = 0;

  for (const t of sampleTimes(cs)) {
    const frame = evaluate(cs, t);
    const cam = cameraMatrix(frame.camera);
    const settled = frame.objects.filter((o) => o.presence >= 0.999 && o.alpha >= 0.5);
    const screen = new Map<string, Rect>();
    for (const o of settled) screen.set(o.id, o.layer === "overlay" ? o.bbox : transformRect(cam, o.bbox));
    const spec = (o: FrameObject) => getSpec(o.type);
    const counted = settled.filter((o) => !(spec(o)?.decorative ?? false) && o.type !== "group");
    if (counted.length > clutterMax) {
      clutterMax = counted.length;
      clutterT = t;
    }

    for (const o of settled) {
      const r = screen.get(o.id) as Rect;
      const sp = spec(o);
      if (r.w <= 0 || r.h <= 0) continue;
      // Off-screen content: outside the world canvas is an error; cropped by a
      // deliberate camera move is only a warning.
      const outside = (box: Rect) => {
        const inside = intersection(box, CANVAS_RECT);
        return 1 - (inside ? area(inside) : 0) / Math.max(1, area(box));
      };
      const worldOut = o.layer === "overlay" ? outside(r) : outside(o.bbox);
      const screenOut = outside(r);
      // Clipped words are unreadable: text, labels and equations must be fully inside.
      const textual = (sp?.labelLike ?? false) || o.type === "text" || o.type === "equation";
      if (worldOut > (textual ? 0.01 : 0.1)) {
        const fix = fitHint(o.layer === "overlay" ? r : o.bbox);
        const by = fix ? ` (about ${fix.dx}, ${fix.dy} px${fix.scale ? `, at ${fix.scale}x the size` : ""})` : "";
        report(
          "OUT_OF_BOUNDS",
          o.id,
          `'${o.id}' is ${Math.round(worldOut * 100)}% outside the frame at ${roundTo(t, 2)}s.`,
          t,
          "error",
          `Move it into the stage${by}: use a slot, or make it smaller.`,
          fix,
        );
      } else if (screenOut > 0.1) {
        report(
          "CAMERA_CROPS",
          o.id,
          `The camera leaves '${o.id}' ${Math.round(screenOut * 100)}% out of view at ${roundTo(t, 2)}s.`,
          t,
          "warning",
          "Fine if intended; otherwise widen the camera focus.",
        );
      } else if (!(sp?.decorative ?? false) && !contains(CANVAS_RECT, r, -12)) {
        // The sure fix: move it just inside the safe margin (screen px, in world px under the camera).
        const zoom = o.layer === "overlay" ? 1 : frame.camera.zoom;
        const inward = fitHint(r);
        report(
          "NEAR_EDGE",
          o.id,
          `'${o.id}' touches the edge of the frame at ${roundTo(t, 2)}s.`,
          t,
          "warning",
          "Keep content inside the safe margins.",
          inward && !inward.scale
            ? { dx: roundTo(inward.dx / Math.max(0.01, zoom), 1), dy: roundTo(inward.dy / Math.max(0.01, zoom), 1) }
            : undefined,
        );
      }
      // Text size at 1080p.
      const minSize = sp?.minTextSize?.(o.params, theme);
      if (minSize) {
        const zoom = o.layer === "overlay" ? 1 : frame.camera.zoom;
        const px = minSize * scaleOf(o.matrix) * zoom * EXPORT_SCALE;
        if (px < theme.typography.minSizePx1080) {
          report(
            "TEXT_TOO_SMALL",
            o.id,
            `Text in '${o.id}' renders at ${roundTo(px, 1)} px at 1080p (minimum ${theme.typography.minSizePx1080}).`,
            t,
            px < theme.typography.minSizePx1080 * 0.8 ? "error" : "warning",
            "Use a larger text size or scale.",
          );
        }
      }
      // Contrast of text drawn directly on the background.
      if (sp?.bareText?.(o.params)) {
        const ratio = contrastRatio(o.text, frame.background.color);
        if (ratio < theme.minContrast) {
          report(
            "LOW_CONTRAST",
            o.id,
            `'${o.id}' text contrast is ${roundTo(ratio, 2)}:1 against the background (minimum ${theme.minContrast}:1).`,
            t,
            "warning",
            "Use the 'text' or 'muted' role, or put the text on a pill.",
          );
        }
      }
      // Hidden under the caption bar.
      if (cs.captions && frame.caption && o.layer !== "overlay" && !(sp?.decorative ?? false)) {
        const hidden = intersection(r, CAPTION_BAR);
        if (hidden && area(hidden) > 0.25 * area(r)) {
          // The sure fix: lift it until its bottom clears the bar, if it then stays below the title.
          const up = CAPTION_BAR.y - FIT_MARGIN / 2 - (r.y + r.h);
          const fits = r.y + up >= cs.stage.y;
          report(
            "UNDER_CAPTION",
            o.id,
            `'${o.id}' is behind the caption bar at ${roundTo(t, 2)}s.`,
            t,
            "warning",
            "Keep the stage content above the caption area (use stage slots).",
            fits ? { dx: 0, dy: roundTo(up / Math.max(0.01, frame.camera.zoom), 1) } : undefined,
          );
        }
      }
    }

    // Stage content covering the scene's title (titles are text on the overlay layer). Label
    // overlaps are checked below; this catches everything else (gauges, cards, diagrams).
    const titles = settled.filter((o) => o.layer === "overlay" && o.type === "text");
    for (const title of titles) {
      const rt = screen.get(title.id) as Rect;
      for (const o of counted) {
        if (o === title || o.layer === "overlay" || spec(o)?.labelLike) continue;
        const ov = intersection(rt, screen.get(o.id) as Rect);
        // A real cover reaches into the title's line of text; a box corner grazing the edge of
        // the title's box does not (bounding boxes include empty space).
        if (ov && ov.h >= 0.3 * rt.h && ov.w >= 24) {
          // The sure fix: move it down until its top clears the title (world px under the camera).
          const rb = screen.get(o.id) as Rect;
          const down = roundTo((rt.y + rt.h + FIT_MARGIN / 2 - rb.y) / Math.max(0.01, frame.camera.zoom), 1);
          report(
            "TITLE_COVERED",
            `${o.id}|${title.id}`,
            `'${o.id}' covers the title '${title.id}' at ${roundTo(t, 2)}s.`,
            t,
            "error",
            `Keep stage content below the title bar (inside the stage, y from 116 to 736): about ${down} px lower.`,
            down > 0 ? { dx: 0, dy: down } : undefined,
          );
        }
      }
    }

    // Overlaps between label-like objects.
    const labels = counted.filter((o) => spec(o)?.labelLike);
    for (let i = 0; i < labels.length; i++) {
      for (let j = i + 1; j < labels.length; j++) {
        const a = labels[i] as FrameObject;
        const b = labels[j] as FrameObject;
        if (parentOf.get(a.id) && parentOf.get(a.id) === parentOf.get(b.id)) continue;
        const ra = screen.get(a.id) as Rect;
        const rb = screen.get(b.id) as Rect;
        const ov = intersection(ra, rb);
        if (!ov) continue;
        const frac = area(ov) / Math.max(1, Math.min(area(ra), area(rb)));
        if (frac > 0.12) {
          const zoom = a.layer === "overlay" ? 1 : frame.camera.zoom;
          report(
            "LABEL_OVERLAP",
            `${a.id}|${b.id}`,
            `'${a.id}' and '${b.id}' overlap at ${roundTo(t, 2)}s.`,
            t,
            "error",
            "Move one of them (different slot or side), or show them at different times.",
            clearHint(ra, rb, zoom),
          );
        }
      }
    }
    // Labels and panels (equations) covering other (non-connector) objects they are not attached to.
    const covering = [...labels, ...counted.filter((o) => o.type === "equation")];
    for (const a of covering) {
      const ra = screen.get(a.id) as Rect;
      for (const b of counted) {
        if (b === a || spec(b)?.labelLike || spec(b)?.worldSpace || b.type === "equation") continue;
        if (followTarget.get(a.id) === b.id || parentOf.get(a.id) === b.id || parentOf.get(b.id) === a.id) continue;
        const rb = screen.get(b.id) as Rect;
        const ov = intersection(ra, rb);
        if (ov && (area(ov) > 0.35 * area(ra) || area(ov) > 0.25 * area(rb))) {
          // A label sitting inside an object (a name written on a box) is meant; one straddling the
          // object's edge is not, and has a sure fix: move it clear.
          const inside = area(ov) / Math.max(1, area(ra));
          const zoom = a.layer === "overlay" ? 1 : frame.camera.zoom;
          report(
            "LABEL_OCCLUDES",
            `${a.id}|${b.id}`,
            `'${a.id}' covers part of '${b.id}' at ${roundTo(t, 2)}s.`,
            t,
            "warning",
            "Place the label beside the object instead of on top of it.",
            inside < 0.9 ? clearHint(ra, rb, zoom) : undefined,
          );
        }
      }
    }
    // Separate objects of similar size overlapping, so one hides part of the other (two magnets drawn
    // into each other). Not flagged: parts of one drawing (grouped, pinned, or sharing an id prefix:
    // cart_body / cart_wheel), a light touch (a wheel touching a cart), or a small thing on a large
    // region (a shoe on the ground). The sure fix moves the smaller one clear.
    const solids = counted.filter((o) => {
      const sp = spec(o);
      const r = screen.get(o.id) as Rect;
      return (
        !sp?.labelLike &&
        !sp?.strokeLine &&
        !sp?.worldSpace &&
        !NOT_SOLID.has(o.type) &&
        !(o.type === "path" && o.params.closed !== true) &&
        o.type !== "equation" &&
        area(r) < 0.25 * cs.stage.w * cs.stage.h
      );
    });
    // `frame.objects` is in drawing order: a later object covers an earlier one.
    const drawnAt = new Map(frame.objects.map((o, i) => [o.id, i]));
    for (let i = 0; i < solids.length; i++) {
      for (let j = i + 1; j < solids.length; j++) {
        const a = solids[i] as FrameObject;
        const b = solids[j] as FrameObject;
        if (sameDrawing(a.id, b.id) || related(a.id, b.id)) continue;
        const [lower, upper] = (drawnAt.get(a.id) ?? 0) < (drawnAt.get(b.id) ?? 0) ? [a, b] : [b, a];
        const rl = screen.get(lower.id) as Rect;
        const ru = screen.get(upper.id) as Rect;
        const ov = intersection(rl, ru);
        if (!ov) continue;
        // Content drawn on a shape (an icon in a circle, a chloroplast in a cell) sits inside it: fine.
        // Otherwise the covered share of the lower object decides: a wheel under a cart's edge (a
        // touch) is fine, a box drawn over half a wheel, or a magnet drawn into another, is not.
        if (area(ov) >= 0.9 * area(ru) || area(ov) < 0.25 * area(rl)) continue;
        const zoom = upper.layer === "overlay" ? 1 : frame.camera.zoom;
        report(
          "OBJECT_OVERLAP",
          `${upper.id}|${lower.id}`,
          `'${upper.id}' covers part of '${lower.id}' at ${roundTo(t, 2)}s.`,
          t,
          "warning",
          "Keep separate objects apart (32 px or more); parts of one drawing share an id prefix (cart_body, cart_wheel).",
          clearHint(ru, rl, zoom),
        );
      }
    }
    // Connectors (arrows, vectors, dimensions, brackets): running through a label's text area, through
    // another object (in one side and out the other), or across another connector. An arrow with a
    // `curve` gets a sure fix: the smallest bend that clears everything.
    const stageArea = cs.stage.w * cs.stage.h;
    const byId = new Map(frame.objects.map((o) => [o.id, o]));
    const connectors = counted.flatMap((c) => {
      const line = spec(c)?.strokeLine;
      if (!line) return [];
      const m = c.layer === "overlay" ? c.matrix : multiply(cam, c.matrix);
      const stroke = (params: Record<string, unknown>) => line(params, theme).map((p) => apply(m, p));
      return [{ c, stroke, pts: stroke(c.params) }];
    });
    const obstacles = counted.filter((o) => {
      const sp = spec(o);
      const r = screen.get(o.id) as Rect;
      return !sp?.labelLike && !sp?.strokeLine && !sp?.worldSpace && area(r) < 0.4 * stageArea;
    });
    const unrelated = (c: FrameObject, o: FrameObject) =>
      o !== c && parentOf.get(o.id) !== c.id && parentOf.get(c.id) !== o.id && followTarget.get(c.id) !== o.id;
    const crossesLabel = (pts: Vec2[], b: FrameObject) => {
      const inner = expand(screen.get(b.id) as Rect, -CONNECTOR_CLEARANCE);
      return inner.w > 0 && inner.h > 0 && pts.filter((p) => inRect(p, inner)).length >= 2;
    };
    const throughObject = (pts: Vec2[], o: FrameObject) => {
      const r = screen.get(o.id) as Rect;
      const inner = expand(r, -CONNECTOR_CLEARANCE);
      const ends = [pts[0], pts[pts.length - 1]].filter((p): p is Vec2 => p !== undefined);
      if (inner.w <= 0 || inner.h <= 0 || ends.some((p) => inRect(p, expand(r, 4)))) return false;
      return pts.filter((p) => inRect(p, inner)).length >= 3;
    };
    const problems = (c: FrameObject, pts: Vec2[], others: typeof connectors) =>
      labels.filter((b) => unrelated(c, b) && crossesLabel(pts, b)).length +
      obstacles.filter((o) => unrelated(c, o) && throughObject(pts, o)).length +
      others.filter((k) => k.c !== c && polylinesCross(pts, k.pts)).length;
    const bendFix = (k: (typeof connectors)[number]): IssueFix | undefined => {
      const c0 = k.c.params.curve;
      if (typeof c0 !== "number") return undefined;
      // A new route must keep clear of everything but the two things it joins: no point within
      // ROUTE_CLEARANCE of another label or object (grazing an edge is not clear), no crossing.
      const refs0 = declared.get(k.c.id);
      const joined = new Set(
        [refs0?.from, refs0?.to].map((v) => (typeof v === "string" ? v.split(".")[0] : undefined)),
      );
      const clear = (params: Record<string, unknown>) => {
        const pts = k.stroke(params);
        if (pts.some((p) => !inRect(p, CANVAS_RECT))) return false;
        const inner = pts.slice(2, -2); // the ends touch the objects they join
        const near = (o: FrameObject) =>
          !joined.has(o.id) &&
          unrelated(k.c, o) &&
          inner.some((p) => inRect(p, expand(screen.get(o.id) as Rect, ROUTE_CLEARANCE)));
        if (labels.some(near) || obstacles.some(near)) return false;
        return !connectors.some((other) => other.c !== k.c && polylinesCross(pts, other.pts));
      };
      const steps = [0, 40, 80, 120, 160, 220, 300, 400].flatMap((s) => (s ? [s, -s] : [0]));
      // Candidates, smallest change first: the same ends bent around what is in the way, or both ends
      // on top / at the bottom / on one side (an arc between two things in a row then goes over or
      // under its neighbours instead of through them). Moving the ends costs about a 60 px bend.
      type Candidate = { cost: number; draw: Record<string, unknown>; fix: Record<string, number | string> };
      const candidates: Candidate[] = steps
        .filter((s) => s !== 0 && Math.abs(c0 + s) <= 600)
        .map((s) => ({ cost: Math.abs(s), draw: { ...k.c.params, curve: c0 + s }, fix: { curve: c0 + s } }));
      const refs = declared.get(k.c.id);
      const [a, b] = [refs?.from, refs?.to].map((v) => (typeof v === "string" ? v.split(".")[0] : undefined));
      const boxA = a ? byId.get(a)?.bbox : undefined;
      const boxB = b ? byId.get(b)?.bbox : undefined;
      if (a && b && boxA && boxB) {
        for (const side of ["top", "bottom", "left", "right"] as const) {
          for (const curve of steps) {
            candidates.push({
              cost: 60 + Math.abs(curve),
              draw: { ...k.c.params, from: boxAnchor(boxA, side), to: boxAnchor(boxB, side), curve },
              fix: { from: `${a}.${side}`, to: `${b}.${side}`, curve },
            });
          }
        }
      }
      candidates.sort((x, y) => x.cost - y.cost);
      const best = candidates.find((cand) => clear(cand.draw));
      return best ? { dx: 0, dy: 0, params: best.fix } : undefined;
    };
    connectors.forEach((k, i) => {
      const { c, pts } = k;
      let fix: IssueFix | undefined | null = null; // computed once, on the first problem
      const fixFor = () => {
        if (fix === null) fix = bendFix(k);
        return fix;
      };
      for (const b of labels) {
        if (!unrelated(c, b) || !crossesLabel(pts, b) || seen.has(`CONNECTOR_CROSSES_LABEL|${c.id}|${b.id}`)) continue;
        report(
          "CONNECTOR_CROSSES_LABEL",
          `${c.id}|${b.id}`,
          `'${c.id}' runs across '${b.id}' at ${roundTo(t, 2)}s.`,
          t,
          "warning",
          "Route it around the label (curve, gaps or other anchors), or give the labels matching widths.",
          fixFor(),
        );
      }
      for (const o of obstacles) {
        if (!unrelated(c, o) || !throughObject(pts, o) || seen.has(`CONNECTOR_CROSSES_OBJECT|${c.id}|${o.id}`))
          continue;
        report(
          "CONNECTOR_CROSSES_OBJECT",
          `${c.id}|${o.id}`,
          `'${c.id}' runs through '${o.id}' at ${roundTo(t, 2)}s.`,
          t,
          "warning",
          "Route it around the object: bend it (curve), connect other anchors, or move the object out of its way.",
          fixFor(),
        );
      }
      for (const other of connectors.slice(i + 1)) {
        const key = `${c.id}|${other.c.id}`;
        if (seen.has(`CONNECTORS_CROSS|${key}`) || !polylinesCross(pts, other.pts)) continue;
        report(
          "CONNECTORS_CROSS",
          key,
          `'${c.id}' crosses '${other.c.id}' at ${roundTo(t, 2)}s.`,
          t,
          "warning",
          "Bend one of them (curve) or connect other anchors so the arrows do not cross.",
          fixFor(),
        );
      }
    });
  }

  if (clutterMax > LIMITS.maxVisibleObjects) {
    out.push(
      issue(
        "CLUTTER",
        "/objects",
        `${clutterMax} objects are visible at once at ${roundTo(clutterT, 2)}s (limit ${LIMITS.maxVisibleObjects}).`,
        {
          severity: "warning",
          source: "layout",
          time: roundTo(clutterT, 2),
          suggestion: "Remove objects that are no longer needed; one new idea per beat.",
        },
      ),
    );
  }
  return out;
}
