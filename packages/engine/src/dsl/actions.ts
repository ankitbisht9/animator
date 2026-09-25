/**
 * Timeline actions: what the LLM writes. The action compiler expands them into
 * per-object channel segments. Timing is relative by default:
 *   start: { at: 2.5 } | { at: "cue:drop" } | { at: "beat:b2" } | { after: "a1", delay?: 0.3 } | { with: "a1" }
 * An action without `start` begins after the previous action in the list.
 */
import { z } from "zod";
import { ENTRANCE_STYLES, EXIT_STYLES } from "../catalog/spec";
import { AnchorName, ColorRef, EasingSchema, Id, MAX_SCENE_DURATION, Place, Side, TextOrBind, Vec } from "./primitives";

export const TimeRef = z
  .union([z.number().min(0).max(MAX_SCENE_DURATION), z.string().regex(/^(cue|beat|beat_end):[a-z][a-z0-9_]*$/)])
  .describe("Seconds, or 'cue:<id>', 'beat:<id>' (beat start) or 'beat_end:<id>'");

export const Start = z
  .strictObject({
    at: TimeRef.optional(),
    after: Id.optional().describe("Start when this action ends"),
    with: Id.optional().describe("Start together with this action"),
    delay: z.number().min(-30).max(60).optional(),
  })
  .meta({ id: "Start", description: "When the action starts. Use exactly one of at / after / with." });

const Common = {
  id: Id.optional(),
  start: Start.optional(),
  duration: z
    .number()
    .min(0)
    .max(MAX_SCENE_DURATION)
    .optional()
    .describe("Seconds; defaults to the theme's motion duration"),
  easing: EasingSchema.optional(),
};

const target = Id.describe("Id of the object this action affects");

export const HIGHLIGHT_STYLES = ["pulse", "glow", "box", "underline", "circle", "flash", "shake", "color"] as const;

export const CreateAction = z.strictObject({
  ...Common,
  action: z.literal("create"),
  target,
  style: z.enum(ENTRANCE_STYLES).optional().describe("Entrance animation; 'auto' picks one suited to the component"),
});
export const RemoveAction = z.strictObject({
  ...Common,
  action: z.literal("remove"),
  target,
  style: z.enum(EXIT_STYLES).optional(),
});
export const ShowAction = z.strictObject({ ...Common, action: z.literal("show"), target });
export const HideAction = z.strictObject({ ...Common, action: z.literal("hide"), target });
export const MoveAction = z.strictObject({
  ...Common,
  action: z.literal("move"),
  target,
  to: Place.optional().describe("Destination (slot, relative placement, anchor pin or raw x/y)"),
  by: Vec.optional().describe("Relative displacement in pixels"),
  path: z.enum(["straight", "arc"]).optional(),
  arcHeight: z.number().min(-600).max(600).optional(),
});
export const ScaleAction = z.strictObject({
  ...Common,
  action: z.literal("scale"),
  target,
  to: z.number().min(0).max(20),
});
export const RotateAction = z.strictObject({
  ...Common,
  action: z.literal("rotate"),
  target,
  to: z.number().min(-3600).max(3600).optional().describe("Absolute angle in degrees (clockwise)"),
  by: z.number().min(-3600).max(3600).optional(),
});
export const FadeAction = z.strictObject({
  ...Common,
  action: z.literal("fade"),
  target,
  to: z.number().min(0).max(1),
});
export const ColorChangeAction = z.strictObject({
  ...Common,
  action: z.literal("color_change"),
  target,
  property: z.enum(["fill", "stroke", "text"]).optional(),
  to: ColorRef,
});
export const DrawAction = z.strictObject({
  ...Common,
  action: z.literal("draw"),
  target,
  reverse: z.boolean().optional().describe("Un-draw instead of draw"),
});
export const WriteAction = z.strictObject({
  ...Common,
  action: z.literal("write"),
  target,
  terms: z.array(Id).max(24).optional().describe("Equation part ids to reveal, in order"),
  by: z.enum(["char", "word", "term"]).optional(),
});
export const ParamChange = z.strictObject({
  param: z.string().regex(/^[a-zA-Z][a-zA-Z0-9_]{0,31}$/),
  to: z.union([z.number(), z.string().max(64), z.boolean()]),
});
export const TransformAction = z.strictObject({
  ...Common,
  action: z.literal("transform"),
  target,
  changes: z
    .array(ParamChange)
    .max(12)
    .optional()
    .describe("Animate params to new values (numbers and colors interpolate)"),
  into: Id.optional().describe("Replace this object by another (crossfade and fit)"),
});
export const MorphAction = z.strictObject({
  ...Common,
  action: z.literal("morph"),
  target,
  into: Id.describe("A path/shape object whose outline this object morphs into"),
});
export const HighlightAction = z.strictObject({
  ...Common,
  action: z.literal("highlight"),
  target,
  style: z.enum(HIGHLIGHT_STYLES).optional(),
  color: ColorRef.optional(),
  terms: z.array(Id).max(24).optional().describe("For equations: which parts to highlight"),
});
export const LabelAction = z.strictObject({
  ...Common,
  action: z.literal("label"),
  target,
  text: TextOrBind,
  labelId: Id.optional().describe("Id of the created label object (default '<action id>_label')"),
  side: Side.optional(),
  gap: z.number().min(-200).max(400).optional(),
  style: z.enum(["plain", "pill", "tag"]).optional(),
  color: ColorRef.optional(),
  leader: z.boolean().optional().describe("Draw a leader line from the label to the target"),
});
export const FollowAction = z.strictObject({
  ...Common,
  action: z.literal("follow"),
  target,
  leader: Id,
  anchor: AnchorName.optional(),
  offset: Vec.optional(),
});
export const GroupAction = z.strictObject({
  ...Common,
  action: z.literal("group"),
  target: Id.describe("Id of the group; a new group object is created if needed"),
  members: z.array(Id).min(1).max(30),
});
export const UngroupAction = z.strictObject({ ...Common, action: z.literal("ungroup"), target });
export const RunSolverAction = z.strictObject({
  ...Common,
  action: z.literal("run_solver"),
  target: Id.describe("Solver id"),
});
export const BindAction = z.strictObject({
  ...Common,
  action: z.literal("bind"),
  target,
  property: z.string().regex(/^(x|y|rotation|scale|opacity|params\.[a-zA-Z][a-zA-Z0-9_]*)$/),
  bind: z.string().max(240).describe("Expression; may use t (seconds since action start)"),
  format: z.string().max(32).optional().describe("For text params: number format, e.g. 'F = 0.0 N'"),
});
export const CameraMoveAction = z.strictObject({
  ...Common,
  action: z.literal("camera_move"),
  center: Vec.optional(),
  zoom: z.number().min(0.25).max(6).optional(),
  focus: z.array(Id).max(10).optional().describe("Frame these objects"),
  padding: z.number().min(0).max(600).optional(),
  follow: Id.optional().describe("Keep this object centered for the duration"),
});
export const CutAction = z.strictObject({
  ...Common,
  action: z.literal("cut"),
  center: Vec.optional(),
  zoom: z.number().min(0.25).max(6).optional(),
});
export const TransitionAction = z.strictObject({
  ...Common,
  action: z.literal("transition"),
  style: z.enum(["fade_through", "wipe", "flash"]),
  color: ColorRef.optional(),
});
export const Keyframe = z.strictObject({
  time: z.number().min(0).max(MAX_SCENE_DURATION).describe("Seconds after the action start"),
  value: z.union([z.number(), z.string().max(64)]),
  easing: EasingSchema.optional(),
});
export const AnimateAction = z.strictObject({
  ...Common,
  action: z.literal("animate"),
  target,
  property: z.string().regex(/^(x|y|rotation|scale|opacity|fill|stroke|text|params\.[a-zA-Z][a-zA-Z0-9_]*)$/),
  keyframes: z.array(Keyframe).min(1).max(60),
});
export const WaitAction = z.strictObject({ ...Common, action: z.literal("wait") });

export const Action = z
  .discriminatedUnion("action", [
    CreateAction,
    RemoveAction,
    ShowAction,
    HideAction,
    MoveAction,
    ScaleAction,
    RotateAction,
    FadeAction,
    ColorChangeAction,
    DrawAction,
    WriteAction,
    TransformAction,
    MorphAction,
    HighlightAction,
    LabelAction,
    FollowAction,
    GroupAction,
    UngroupAction,
    RunSolverAction,
    BindAction,
    CameraMoveAction,
    CutAction,
    TransitionAction,
    AnimateAction,
    WaitAction,
  ])
  .meta({ id: "Action" });

export type ActionT = z.infer<typeof Action>;
export type ActionType = ActionT["action"];
export type StartT = z.infer<typeof Start>;
export type KeyframeT = z.infer<typeof Keyframe>;

export const ACTION_TYPES = Action.options.map((o) => o.shape.action.value) as ActionType[];
