/**
 * SceneState: the engine-computed end state of a scene, which is where the next
 * scene starts (P6). The LLM never reports it; it gets a compact summary.
 */
import type { FollowPlace } from "../timeline/types";

export type Lifecycle = "active" | "hidden" | "removed";

export interface ObjectState {
  id: string;
  type: string;
  /** Static params at scene end (animated and bound values frozen). */
  params: Record<string, unknown>;
  x: number;
  y: number;
  rotation: number;
  scale: number;
  opacity: number;
  visible: boolean;
  fill: string;
  stroke: string;
  text: string;
  strokeWidth: number;
  shadow: boolean;
  dashed: boolean;
  layer: "background" | "main" | "foreground" | "overlay";
  z: number;
  persist: boolean;
  decorative: boolean;
  parent?: string;
  followPlace?: FollowPlace;
  metadata?: { name?: string; description?: string; concept?: string };
  lifecycle: Lifecycle;
  /** World bounds at scene end (for summaries). */
  bbox: { x: number; y: number; w: number; h: number };
}

export interface RegistryEntry {
  type: string;
  createdIn: string;
  lastScene: string;
  lifecycle: Lifecycle;
}

export interface SceneState {
  sceneId: string;
  /** Index of the scene in its lesson (0-based), informational. */
  t: number;
  themeId: string;
  camera: { cx: number; cy: number; zoom: number };
  objects: Record<string, ObjectState>;
  /** Every object id ever created in the lesson so far. */
  registry: Record<string, RegistryEntry>;
  /** Final solver outputs of the scene, by solver id. */
  solvers: Record<string, Record<string, number>>;
  values: Record<string, number>;
}
