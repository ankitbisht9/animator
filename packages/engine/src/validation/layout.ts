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
import { brokenWordsIn } from "../text/layout";
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

/** The move that brings a box inside the stage (`margin` in from its edges), or undefined when the box
 * is bigger than the stage or already inside. Never shrinks. */
export function stageFitHint(box: Rect, stage: Rect, margin = 12): IssueFix | undefined {
  const inner = expand(stage, -margin);
  if (box.w > inner.w || box.h > inner.h) return undefined;
  const shift = (lo: number, size: number, min: number, max: number) =>
    lo < min ? min - lo : lo + size > max ? max - (lo + size) : 0;
  const dx = roundTo(shift(box.x, box.w, inner.x, inner.x + inner.w), 1);
  const dy = roundTo(shift(box.y, box.h, inner.y, inner.y + inner.h), 1);
  return dx === 0 && dy === 0 ? undefined : { dx, dy };
}

/** Where content may be: below the title bar, above the caption bar, clear of the frame edges. */
export function safeArea(cs: CompiledScene): Rect {
  // Sides: 40 px (text 24 px from the frame edge still looks cut off); fixes move content to 48 px,
  // the stage's own margin.
  const side = 40;
  const top = cs.layout === "title_card" || cs.layout === "no_title" ? FIT_MARGIN : 104;
  const bottom = cs.captions ? CAPTION_BAR.y : CANVAS_RECT.h - FIT_MARGIN;
  return { x: side, y: top, w: CANVAS_RECT.w - 2 * side, h: bottom - top };
}

/** A row of short items ("1 2 3 ... 16", "A B C D E F") written as one text. */
export function rowLike(text: string): boolean {
  const tokens = text.replace(/\*\*/g, "").split(/\s+/).filter(Boolean);
  return tokens.length >= 5 && tokens.filter((w) => w.length <= 3).length >= 0.7 * tokens.length;
}

/** Shapes that hold content drawn on them (an icon in a circle, a word on a box). */
const HOLDERS = new Set(["rectangle", "circle", "ellipse", "polygon", "path", "card", "note", "block", "container"]);

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
  const prepById = new Map(cs.objects.map((o) => [o.id, o.prep]));
  // `object|container` pairs seen fully inside at rest (a block in a tank), and texts checked for wrapping.
  const insideOnce = new Set<string>();
  const wrapChecked = new Set<string>();
  const brokenChecked = new Set<string>();
  // Moving objects are checked at rest (after each move), not in passing (SGA: transient overlaps during
  // a motion are fine, the positions it rests in are not).
  const actions = Object.values(cs.actions);
  const moving = (id: string, t: number) =>
    actions.some(
      (a) =>
        a.start < t &&
        t < a.end &&
        (a.action === "run_solver" || (a.target === id && (a.action === "move" || a.action === "animate"))),
    );

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
        if (parentOf.get(a.id) === b.id || parentOf.get(b.id) === a.id) continue;
        const rb = screen.get(b.id) as Rect;
        const ov = intersection(ra, rb);
        if (!ov) continue;
        // A label straddling a larger object's edge half-covers both, even when pinned to it on purpose
        // ("Down" pinned under a person's feet, but overlapping them).
        const share = area(ov) / Math.max(1, area(ra));
        const straddles = share >= 0.15 && share < 0.9 && area(ra) < area(rb);
        const covers = area(ov) > 0.35 * area(ra) || area(ov) > 0.25 * area(rb);
        if (followTarget.get(a.id) === b.id ? straddles : straddles || covers) {
          // A label sitting inside an object (a name written on a box) is meant; one straddling the
          // object's edge is not, and has a sure fix: move it clear.
          const inside = share;
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
        // See-through shapes (a Venn diagram, a pale "displaced water" region under a ship) hide nothing.
        if (upper.alpha < 0.9 || lower.alpha < 0.9) continue;
        const rl = screen.get(lower.id) as Rect;
        const ru = screen.get(upper.id) as Rect;
        const ov = intersection(rl, ru);
        if (!ov) continue;
        // Content drawn on a shape (an icon in a circle, a chloroplast in a cell) sits inside it: fine.
        // Otherwise the covered share of the lower object decides: a wheel under a cart's edge (a
        // touch) is fine, a box drawn over half a wheel, or a magnet drawn into another, is not.
        // (A small thing across a bigger thing's edge is often a deliberate touch, a sperm reaching an
        // egg, so only the covered share of the lower object counts here.)
        const onTop = area(ov) >= 0.9 * area(ru);
        if (onTop ? HOLDERS.has(lower.type) || area(ov) < 0.15 * area(rl) : area(ov) < 0.25 * area(rl)) continue;
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
    // Two containers drawn into each other (an overflow can over a tank's wall). Containers are glass, so
    // they are left out of the solid overlaps above; one container over another still reads as a glitch.
    const vessels = counted.filter((o) => o.type === "container");
    for (let i = 0; i < vessels.length; i++) {
      for (let j = i + 1; j < vessels.length; j++) {
        const a = vessels[i] as FrameObject;
        const b = vessels[j] as FrameObject;
        if (related(a.id, b.id)) continue;
        const [lower, upper] = (drawnAt.get(a.id) ?? 0) < (drawnAt.get(b.id) ?? 0) ? [a, b] : [b, a];
        const rl = screen.get(lower.id) as Rect;
        const ru = screen.get(upper.id) as Rect;
        const ov = intersection(rl, ru);
        // A catch cup under an overflow can's spout overlaps the can's box (spout included) by about a
        // quarter on purpose; a beaker drawn into a tank's wall overlaps it by nearly half.
        if (!ov || area(ov) < 0.35 * Math.min(area(rl), area(ru))) continue;
        report(
          "OBJECT_OVERLAP",
          `${upper.id}|${lower.id}`,
          `'${upper.id}' covers part of '${lower.id}' at ${roundTo(t, 2)}s.`,
          t,
          "warning",
          "Keep separate containers apart (32 px or more).",
          clearHint(ru, rl, upper.layer === "overlay" ? 1 : frame.camera.zoom),
        );
      }
    }

    // Text hidden under something drawn after it (a ramp over its "height" label, a card over a
    // label). The sure fix moves the lighter of the two: a free annotation, or the smaller one.
    const covers = counted.filter((o) => {
      const sp = spec(o);
      return (
        !sp?.strokeLine &&
        !sp?.worldSpace &&
        !sp?.decorative &&
        !NOT_SOLID.has(o.type) &&
        !(o.type === "path" && o.params.closed !== true) &&
        o.alpha >= 0.9
      );
    });
    // Text: labels and panels, and connectors carrying a label (an arrow marked "motion").
    const texts = [
      ...labels,
      ...counted.filter((o) => spec(o)?.strokeLine && typeof o.params.label === "string" && o.params.label.trim()),
    ];
    for (const txt of texts) {
      const rt = screen.get(txt.id) as Rect;
      for (const u of covers) {
        if (u === txt || (drawnAt.get(u.id) ?? 0) <= (drawnAt.get(txt.id) ?? 0)) continue;
        if (parentOf.get(txt.id) === u.id || parentOf.get(u.id) === txt.id) continue;
        // Two labels overlapping are LABEL_OVERLAP (an error) already.
        if (spec(u)?.labelLike && seen.has(`LABEL_OVERLAP|${txt.id}|${u.id}`)) continue;
        if (spec(u)?.labelLike && seen.has(`LABEL_OVERLAP|${u.id}|${txt.id}`)) continue;
        const ru = screen.get(u.id) as Rect;
        const ov = intersection(rt, ru);
        if (!ov) continue;
        // A connector's box is mostly empty space around its stroke and label: only a cover over half of
        // it surely hides the label.
        const hides = spec(txt)?.strokeLine
          ? area(ov) >= 0.5 * area(rt)
          : area(ov) >= 0.15 * area(rt) || (area(ov) >= 0.4 * area(ru) && area(ov) >= 600);
        if (!hides) continue;
        const moveCover = !!spec(u)?.labelLike || area(ru) <= area(rt);
        const [mover, still] = moveCover ? [u, txt] : [txt, u];
        report(
          "HIDDEN_TEXT",
          `${mover.id}|${still.id}`,
          `'${txt.id}' is hidden under '${u.id}' at ${roundTo(t, 2)}s.`,
          t,
          "warning",
          "Move one of them so the text is fully visible; text must never be drawn under another object.",
          clearHint(
            screen.get(mover.id) as Rect,
            screen.get(still.id) as Rect,
            mover.layer === "overlay" ? 1 : frame.camera.zoom,
          ),
        );
      }
    }

    // Content reaching out of the safe area: into the title bar, behind the caption bar, or against the
    // frame edge (clipped-looking text). Large regions (ground, water), connectors and titles are exempt.
    const safe = safeArea(cs);
    for (const o of counted) {
      const sp = spec(o);
      if (o.layer === "overlay" || sp?.decorative || sp?.strokeLine || sp?.worldSpace) continue;
      if (NOT_SOLID.has(o.type) && o.type !== "container" && !sp?.labelLike) continue;
      const r = screen.get(o.id) as Rect;
      if (r.w > 0.9 * safe.w || r.h > 0.9 * safe.h) continue;
      if (seen.has(`OUT_OF_BOUNDS|${o.id}`) || seen.has(`NEAR_EDGE|${o.id}`)) continue;
      if (sp?.labelLike && r.y + r.h <= safe.y + 4) continue; // a title in the title bar
      // Against a frame edge, text looks cut: strict. Near the caption bar, a little tolerance (the
      // caption is a pill in the middle). Into the title bar only when deep (covering the title itself
      // is TITLE_COVERED).
      const side = Math.max(safe.x - r.x, r.x + r.w - (safe.x + safe.w));
      const below = r.y + r.h - (safe.y + safe.h);
      const above = safe.y - r.y;
      const beyond = Math.max(side, below, above);
      if (side <= 2 && below <= 24 && above <= 60) continue;
      const zoom = frame.camera.zoom;
      const fix = stageFitHint(r, safe, 8);
      report(
        "OUT_OF_STAGE",
        o.id,
        `'${o.id}' reaches ${Math.round(beyond)} px outside the stage at ${roundTo(t, 2)}s.`,
        t,
        "warning",
        `Keep everything inside the stage (x ${safe.x} to ${safe.x + safe.w}, y ${safe.y} to ${safe.y + safe.h}).`,
        fix
          ? { dx: roundTo(fix.dx / Math.max(0.01, zoom), 1), dy: roundTo(fix.dy / Math.max(0.01, zoom), 1) }
          : undefined,
      );
    }

    // A row of short items written as one text that wraps onto a second line: the things placed along
    // the row (boxes, a pointer, a highlight) no longer line up with it. The sure fix widens the text.
    for (const o of labels) {
      if ((o.type !== "text" && o.type !== "label") || wrapChecked.has(o.id)) continue;
      wrapChecked.add(o.id);
      const words = typeof o.params.text === "string" ? o.params.text : "";
      const sp = spec(o);
      if (!sp || !rowLike(words)) continue;
      const ctx = { theme, prep: prepById.get(o.id) };
      const now = sp.geometry(o.params, ctx).bbox;
      const one = sp.geometry({ ...o.params, maxWidth: 1e5 }, ctx).bbox;
      if (now.h <= one.h * 1.4) continue;
      const want = Math.ceil(one.w + 8);
      const limit = o.type === "label" ? 1200 : 1600;
      const fits = want <= Math.min(limit, cs.stage.w - 2 * FIT_MARGIN);
      report(
        "TEXT_WRAPPED",
        o.id,
        `'${o.id}' is a row of items but wraps onto ${Math.round(now.h / Math.max(1, one.h))} lines.`,
        t,
        "warning",
        fits
          ? `Give it maxWidth ${want} so the row stays on one line.`
          : "Too many items for one line: show at most about 16, abbreviating the middle with '…' (1 2 3 … 62 63 64), in one text or one object per item.",
        fits ? { dx: 0, dy: 0, params: { maxWidth: want } } : undefined,
      );
    }

    // A word too long for its box, split across lines ("Deliver / y", "destinati / on"): the sure fix
    // widens the box to the narrowest width that keeps every word whole.
    for (const o of labels) {
      if (brokenChecked.has(o.id)) continue;
      brokenChecked.add(o.id);
      const sp = spec(o);
      if (!sp) continue;
      const ctx = { theme, prep: prepById.get(o.id) };
      const broken = brokenWordsIn(() => sp.geometry(o.params, ctx));
      if (!broken.length) continue;
      const key = "width" in sp.params.shape ? "width" : "maxWidth";
      const now = typeof o.params[key] === "number" ? (o.params[key] as number) : sp.geometry(o.params, ctx).bbox.w;
      let want: number | undefined;
      for (let w = Math.ceil(now * 1.1); w <= 900; w = Math.ceil(w * 1.1)) {
        if (!brokenWordsIn(() => sp.geometry({ ...o.params, [key]: w }, ctx)).length) {
          want = w;
          break;
        }
      }
      report(
        "TEXT_WORD_BROKEN",
        o.id,
        `A word in '${o.id}' is split across lines ("${broken[0]}"): its box is too narrow.`,
        t,
        "warning",
        want ? `Give it ${key} ${want} so no word is split.` : "Use shorter words or a wider box.",
        want ? { dx: 0, dy: 0, params: { [key]: want } } : undefined,
      );
    }

    // Something that was inside a container at rest and later rests partly outside it: a block sunk
    // through the bottom of its tank. Checked at rest only (passing through the rim is fine).
    for (const c of vessels) {
      const rc = screen.get(c.id) as Rect;
      for (const o of counted) {
        const sp = spec(o);
        if (o === c || sp?.labelLike || sp?.strokeLine || sp?.worldSpace || NOT_SOLID.has(o.type)) continue;
        if (moving(o.id, t)) continue;
        const ro = screen.get(o.id) as Rect;
        const ov = intersection(ro, rc);
        const inside = ov ? area(ov) / Math.max(1, area(ro)) : 0;
        const key = `${o.id}|${c.id}`;
        // Through the floor or a wall (not over the rim: a scale's hook dipping in from above is fine).
        const through =
          inside > 0.2 &&
          inside < 0.95 &&
          ro.y > rc.y &&
          (ro.y + ro.h > rc.y + rc.h + 6 || ro.x < rc.x - 6 || ro.x + ro.w > rc.x + rc.w + 6);
        if (inside >= 0.9) insideOnce.add(key);
        if (through || (insideOnce.has(key) && inside > 0.05 && inside < 0.8)) {
          report(
            "LEAVES_CONTAINER",
            key,
            `'${o.id}' sticks out of '${c.id}' at ${roundTo(t, 2)}s.`,
            t,
            "warning",
            `End its motion inside '${c.id}' (above its floor, between its walls).`,
          );
        }
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
    // Plain lines (wires, leaders drawn as lines) running through a label's text: always a glitch. They
    // are not checked against objects (an axis through the Earth is meant).
    for (const ln of counted) {
      if (ln.type !== "line") continue;
      const a = ln.params.from as Vec2 | undefined;
      const b = ln.params.to as Vec2 | undefined;
      if (!a || !b || typeof a.x !== "number" || typeof b.x !== "number") continue;
      const m = ln.layer === "overlay" ? ln.matrix : multiply(cam, ln.matrix);
      const n = Math.max(2, Math.ceil(Math.hypot(b.x - a.x, b.y - a.y) / 8));
      const pts = Array.from({ length: n + 1 }, (_, i) =>
        apply(m, { x: a.x + ((b.x - a.x) * i) / n, y: a.y + ((b.y - a.y) * i) / n }),
      );
      for (const lb of labels) {
        if (!unrelated(ln, lb) || !crossesLabel(pts, lb)) continue;
        report(
          "CONNECTOR_CROSSES_LABEL",
          `${ln.id}|${lb.id}`,
          `'${ln.id}' runs across '${lb.id}' at ${roundTo(t, 2)}s.`,
          t,
          "warning",
          "Move the label beside the line, or route the line around it.",
        );
      }
    }
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
