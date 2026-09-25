import type { FrameObject, Theme } from "@animator/engine";
import type { ReactNode } from "react";

export interface RenderProps {
  o: FrameObject;
  theme: Theme;
  /** Scene time in seconds, for ambient motion (always a pure function of t). */
  t: number;
  /** Scene seed for stateless randomness. */
  seed: number;
  /** Compile-time prepared data for this object (e.g. typeset equation). */
  prep: unknown;
}

/** Draws an object in its local frame (the SceneView applies its matrix and opacity). */
export type Renderer = (p: RenderProps) => ReactNode;
