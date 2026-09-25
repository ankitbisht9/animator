/** Precomputes point correspondences for `morph` so playback only interpolates. */
import { polygonPoints } from "../catalog/primitives";
import { alignOutlines, resampleOutline, shapeOutline } from "../geom/shapes";
import { apply, invert, objectMatrix } from "../math/matrix";
import { roundTo } from "../math/num";
import type { CompiledObject } from "../timeline/types";

export interface MorphPrep {
  into: string;
  /** Flattened [x0, y0, x1, y1, ...] in the target's local frame. */
  from: number[];
  to: number[];
}

const POINTS = 96;

const base = (o: CompiledObject, name: string, d: number) => {
  const v = o.ch[name]?.base;
  return typeof v === "number" ? v : d;
};

export function buildMorph(target: CompiledObject, into: CompiledObject, _t0: number, _t1: number): MorphPrep | null {
  const a = shapeOutline(target.type, target.params, polygonPoints);
  const b = shapeOutline(into.type, into.params, polygonPoints);
  if (!a || !b) return null;
  const from = resampleOutline(a, POINTS);
  // Bring the destination outline into the target's local frame.
  const mTarget = objectMatrix(
    base(target, "x", 0),
    base(target, "y", 0),
    base(target, "rotation", 0),
    base(target, "scale", 1),
  );
  const mInto = objectMatrix(base(into, "x", 0), base(into, "y", 0), base(into, "rotation", 0), base(into, "scale", 1));
  const toLocal = invert(mTarget);
  const to = alignOutlines(
    from,
    resampleOutline(b, POINTS).map((p) => apply(toLocal, apply(mInto, p))),
  );
  return {
    into: into.id,
    from: from.flatMap((p) => [roundTo(p.x, 3), roundTo(p.y, 3)]),
    to: to.flatMap((p) => [roundTo(p.x, 3), roundTo(p.y, 3)]),
  };
}

export function morphPath(m: MorphPrep, u: number): string {
  let d = "";
  for (let i = 0; i < m.from.length; i += 2) {
    const x = (m.from[i] as number) + ((m.to[i] as number) - (m.from[i] as number)) * u;
    const y = (m.from[i + 1] as number) + ((m.to[i + 1] as number) - (m.from[i + 1] as number)) * u;
    d += `${i === 0 ? "M" : "L"}${roundTo(x, 2)} ${roundTo(y, 2)}`;
  }
  return `${d}Z`;
}
