import type { Renderer } from "../types";
import { CircleR, EllipseR, GroupR, ImageR, LineR, PathR, PolygonR, RectangleR, TextR } from "./primitives";

/** Renderer per catalog type. A test asserts this covers every registered component spec. */
export const RENDERERS: Record<string, Renderer> = {
  circle: CircleR,
  ellipse: EllipseR,
  rectangle: RectangleR,
  polygon: PolygonR,
  line: LineR,
  path: PathR,
  text: TextR,
  group: GroupR,
  image: ImageR,
};
