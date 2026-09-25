/**
 * Placement math shared by the compiler (static placement, `move to`) and the
 * evaluator (placements that follow a moving reference). All functions return
 * the world position of an object's origin.
 */
import { center, type Rect } from "../math/rect";
import type { Vec2 } from "../math/vec";

export const DEFAULT_GAP: Record<string, number> = {
  above: 24,
  below: 24,
  left: 24,
  right: 24,
  center: 0,
  inside_top: 16,
  inside_bottom: 16,
  inside_left: 16,
  inside_right: 16,
};

/**
 * Origin such that the object's (transformed, origin-relative) bounds `local`
 * sit on `side` of `ref`, separated by `gap`, aligned start/center/end.
 */
export function relativeOrigin(local: Rect, ref: Rect, side: string, gap: number, align: string): Vec2 {
  const alignX = (): number => {
    if (align === "start") return ref.x;
    if (align === "end") return ref.x + ref.w - local.w;
    return ref.x + (ref.w - local.w) / 2;
  };
  const alignY = (): number => {
    if (align === "start") return ref.y;
    if (align === "end") return ref.y + ref.h - local.h;
    return ref.y + (ref.h - local.h) / 2;
  };
  let bx: number;
  let by: number;
  switch (side) {
    case "above":
      bx = alignX();
      by = ref.y - gap - local.h;
      break;
    case "below":
      bx = alignX();
      by = ref.y + ref.h + gap;
      break;
    case "left":
      bx = ref.x - gap - local.w;
      by = alignY();
      break;
    case "right":
      bx = ref.x + ref.w + gap;
      by = alignY();
      break;
    case "inside_top":
      bx = alignX();
      by = ref.y + gap;
      break;
    case "inside_bottom":
      bx = alignX();
      by = ref.y + ref.h - gap - local.h;
      break;
    case "inside_left":
      bx = ref.x + gap;
      by = alignY();
      break;
    case "inside_right":
      bx = ref.x + ref.w - gap - local.w;
      by = alignY();
      break;
    default: {
      const c = center(ref);
      bx = c.x - local.w / 2;
      by = c.y - local.h / 2;
    }
  }
  return { x: bx - local.x, y: by - local.y };
}

/** Origin such that the local anchor point lands on `target`. */
export const pinnedOrigin = (localAnchor: Vec2, target: Vec2): Vec2 => ({
  x: target.x - localAnchor.x,
  y: target.y - localAnchor.y,
});
