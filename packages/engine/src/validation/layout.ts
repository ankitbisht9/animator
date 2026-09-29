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
        report(
          "NEAR_EDGE",
          o.id,
          `'${o.id}' touches the edge of the frame at ${roundTo(t, 2)}s.`,
          t,
          "warning",
          "Keep content inside the safe margins.",
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
          report(
            "UNDER_CAPTION",
            o.id,
            `'${o.id}' is behind the caption bar at ${roundTo(t, 2)}s.`,
            t,
            "warning",
            "Keep the stage content above the caption area (use stage slots).",
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
          report(
            "LABEL_OCCLUDES",
            `${a.id}|${b.id}`,
            `'${a.id}' covers part of '${b.id}' at ${roundTo(t, 2)}s.`,
            t,
            "warning",
            "Place the label beside the object instead of on top of it.",
          );
        }
      }
    }
    // Connectors (arrows, vectors, dimensions, brackets) running through a label's text area.
    for (const c of counted) {
      const line = spec(c)?.strokeLine;
      if (!line) continue;
      const m = c.layer === "overlay" ? c.matrix : multiply(cam, c.matrix);
      const pts = line(c.params, theme).map((p) => apply(m, p));
      for (const b of labels) {
        if (b === c || parentOf.get(b.id) === c.id) continue;
        const inner = expand(screen.get(b.id) as Rect, -CONNECTOR_CLEARANCE);
        if (inner.w <= 0 || inner.h <= 0) continue;
        const hits = pts.filter(
          (p) => p.x > inner.x && p.x < inner.x + inner.w && p.y > inner.y && p.y < inner.y + inner.h,
        );
        if (hits.length >= 2) {
          report(
            "CONNECTOR_CROSSES_LABEL",
            `${c.id}|${b.id}`,
            `'${c.id}' runs across '${b.id}' at ${roundTo(t, 2)}s.`,
            t,
            "warning",
            "Route it around the label (curve, gaps or other anchors), or give the labels matching widths.",
          );
        }
      }
    }
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
