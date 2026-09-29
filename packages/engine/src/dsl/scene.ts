/**
 * The canonical Scene DSL. Built from the component catalog, so every
 * registered component type contributes its own typed `params` variant.
 */
import { z } from "zod";
import { allSpecs } from "../catalog";
import { LAYOUT_IDS } from "../layout/templates";
import { THEME_IDS } from "../theme/themes";
import { DSL_VERSION } from "../version";
import { Action, TimeRef } from "./actions";
import { ColorRef, Id, MAX_SCENE_DURATION, Place, Style, Vec } from "./primitives";
import { SolverDef } from "./solvers";

export const LIMITS = {
  maxObjects: 60,
  maxActions: 150,
  maxSolvers: 8,
  maxBeats: 20,
  maxCues: 40,
  maxValues: 40,
  maxKeyframesPerAction: 60,
  maxDuration: MAX_SCENE_DURATION,
  maxTextLength: 400,
  maxJsonBytes: 256 * 1024,
  maxVisibleObjects: 12,
  maxRawPlacementsWarn: 6,
} as const;

export const LAYERS = ["background", "main", "foreground", "overlay"] as const;

const ObjectCommon = {
  id: Id,
  place: Place.optional(),
  rotation: z.number().min(-3600).max(3600).default(0).describe("Degrees, clockwise"),
  scale: z.number().min(0).max(20).default(1),
  opacity: z.number().default(1).describe("0..1"),
  style: Style.optional(),
  layer: z.enum(LAYERS).default("main").describe("'overlay' is pinned to the screen and ignores the camera"),
  z: z.number().int().min(-100).max(100).default(0),
  persist: z.boolean().default(true).describe("Carry into the next scene (default true)"),
  decorative: z.boolean().optional().describe("Excluded from clutter and overlap checks"),
  parent: Id.optional().describe("A group object this object belongs to"),
  metadata: z
    .strictObject({
      name: z.string().max(80).optional(),
      description: z.string().max(240).optional().describe("Shown when a viewer hovers the object"),
      concept: z.string().max(80).optional(),
    })
    .optional(),
};

export const Beat = z
  .strictObject({
    id: Id,
    text: z.string().min(1).max(400).describe("Narration sentence (shown as the caption). **bold** emphasizes."),
    at: TimeRef.optional().describe("Start of the beat; defaults to an even split between known beats"),
  })
  .meta({ id: "Beat" });

export const Cue = z
  .strictObject({ id: Id, at: TimeRef, beat: Id.optional().describe("Beat this cue marks the start of") })
  .meta({ id: "Cue" });

export const SceneValue = z
  .strictObject({
    id: Id,
    value: z.number().optional().describe("A given input number"),
    expr: z.string().max(240).optional().describe("Or an expression computed by the engine"),
    unit: z.string().max(16).optional(),
    description: z.string().max(160).optional(),
  })
  .meta({ id: "SceneValue" });

export const Checkpoint = z
  .strictObject({
    id: Id,
    at: TimeRef,
    question: z.string().min(3).max(200),
    choices: z.array(z.string().min(1).max(100)).min(2).max(4),
    answer: z.number().int().min(0).max(3).describe("Index of the correct choice"),
    explain: z.string().max(300).describe("Shown after answering"),
  })
  .meta({ id: "Checkpoint", description: "A quick question that pauses the player (not part of exported video)." });

export const TransitionIn = z
  .strictObject({
    type: z.enum(["continue", "cut", "fade", "wipe"]).default("continue"),
    duration: z.number().min(0).max(5).optional(),
    keep: z.array(Id).max(30).optional().describe("With cut/fade/wipe: objects that stay on stage"),
    fresh: z
      .boolean()
      .optional()
      .meta({
        description:
          "The scene stands on its own: it is compiled without the previous scene's state, so earlier " +
          "objects and ids do not apply (scenes generated independently; set by the pipeline)",
        "x-llm-omit": true,
      }),
  })
  .meta({ id: "TransitionIn" });

export const Background = z
  .strictObject({
    style: z.enum(["plain", "gradient", "grid", "dots", "paper"]).default("gradient"),
    color: ColorRef.optional(),
  })
  .meta({ id: "Background" });

export const CameraInit = z
  .strictObject({ center: Vec.optional(), zoom: z.number().min(0.25).max(6).optional() })
  .meta({ id: "CameraInit" });

let objectDef: z.ZodType | null = null;

const { id: _objectId, ...ObjectRest } = ObjectCommon;

/** Discriminated union of every registered component type. */
export function objectDefSchema() {
  if (!objectDef) {
    const variants = allSpecs().map((spec) =>
      z
        .strictObject({
          // Strict structured outputs generate fields in schema order: identity first.
          id: ObjectCommon.id,
          type: z.literal(spec.type),
          params: spec.params.prefault({}),
          ...ObjectRest,
        })
        .meta({ id: `Object_${spec.type}`, description: spec.description }),
    );
    objectDef = z
      .discriminatedUnion("type", variants as unknown as [z.ZodObject, ...z.ZodObject[]])
      .meta({ id: "Object" });
  }
  return objectDef;
}

let sceneDef: z.ZodObject | null = null;

export function sceneSchema() {
  if (!sceneDef) {
    sceneDef = z
      .strictObject({
        // Field order matters for strict structured outputs (generated in order):
        // intent and narration first, then the objects, then the timeline that uses them.
        dsl_version: z.literal(DSL_VERSION),
        id: Id,
        title: z.string().min(1).max(120),
        educational_purpose: z.string().max(400).optional(),
        duration: z.number().gt(0).max(MAX_SCENE_DURATION).describe("Seconds"),
        theme: z.enum(THEME_IDS).default("educational_clean"),
        layout: z.enum(LAYOUT_IDS).default("stage_with_caption"),
        background: Background.prefault({}),
        camera: CameraInit.optional().describe("Initial camera; omitted = continue from the previous scene"),
        transition_in: TransitionIn.prefault({}),
        narration: z.strictObject({ beats: z.array(Beat).max(LIMITS.maxBeats).default([]) }).prefault({}),
        cues: z.array(Cue).max(LIMITS.maxCues).default([]),
        values: z.array(SceneValue).max(LIMITS.maxValues).default([]),
        objects: z.array(objectDefSchema()).max(LIMITS.maxObjects).default([]),
        solvers: z.array(SolverDef).max(LIMITS.maxSolvers).default([]),
        timeline: z.array(Action).max(LIMITS.maxActions).default([]),
        interactions: z.array(Checkpoint).max(5).default([]),
        captions: z.boolean().default(true),
        seed: z
          .number()
          .int()
          .min(0)
          .max(2 ** 31 - 1)
          .optional(),
      })
      .meta({ id: "Scene", title: "Scene" });
  }
  return sceneDef;
}

// Canonical (post-parse) types. Params stay loosely typed: each component's
// schema validates them, and the compiler reads them through typed accessors.
export interface ObjectDefT {
  id: string;
  type: string;
  params: Record<string, unknown>;
  place?: z.infer<typeof Place>;
  rotation: number;
  scale: number;
  opacity: number;
  style?: z.infer<typeof Style>;
  layer: (typeof LAYERS)[number];
  z: number;
  persist: boolean;
  decorative?: boolean;
  parent?: string;
  metadata?: { name?: string; description?: string; concept?: string };
}

export interface SceneT {
  dsl_version: string;
  id: string;
  title: string;
  duration: number;
  theme: string;
  layout: (typeof LAYOUT_IDS)[number];
  background: z.infer<typeof Background>;
  camera?: z.infer<typeof CameraInit>;
  objects: ObjectDefT[];
  solvers: z.infer<typeof SolverDef>[];
  values: z.infer<typeof SceneValue>[];
  cues: z.infer<typeof Cue>[];
  timeline: z.infer<typeof Action>[];
  transition_in: z.infer<typeof TransitionIn>;
  narration: { beats: z.infer<typeof Beat>[] };
  interactions: z.infer<typeof Checkpoint>[];
  captions: boolean;
  educational_purpose?: string;
  seed?: number;
}

export type BeatT = z.infer<typeof Beat>;
export type CheckpointT = z.infer<typeof Checkpoint>;
