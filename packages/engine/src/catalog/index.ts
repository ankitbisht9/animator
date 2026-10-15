// Importing a spec module registers its components.
import "./primitives";
import "./annotation";
import "./math";
import "./physics";
import "./ui";
import "./legibility";
import "./figure";
import "./diagram";

export * from "./annotation";
export * from "./common";
export {
  DiagramEdge,
  DiagramLane,
  DiagramNode,
  diagramNodeOutline,
  diagramScale,
  diagramSolidParts,
  diagramTerms,
  layoutOf as diagramLayoutOf,
} from "./diagram";
export { FIGURE_MAX_PATH_CHARS, type FigureArtT, type FigureShapeT, figureAnchors, figureSize } from "./figure";
export * from "./materials";
export * from "./math";
export * from "./physics";
export { pathBBox, polygonPoints } from "./primitives";
export * from "./registry";
export * from "./spec";
export * from "./ui";
