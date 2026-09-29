/**
 * Engine worker protocol (JSON-RPC 2.0 over stdio). These Zod schemas validate
 * requests in the worker and generate `contracts/protocol.schema.json`, from
 * which the Python side's Pydantic models are generated. Every call is
 * stateless: a request carries the scene and the previous SceneState.
 */
import { z } from "zod";

export const IssueSchema = z
  .strictObject({
    code: z.string(),
    path: z.string(),
    message: z.string(),
    suggestion: z.string().optional(),
    severity: z.enum(["error", "warning", "info"]),
    source: z.enum(["schema", "semantic", "layout", "critic", "visual", "normalization"]),
    time: z.number().optional(),
    fix: z
      .strictObject({
        dx: z.number(),
        dy: z.number(),
        scale: z.number().optional(),
        params: z.record(z.string(), z.union([z.number(), z.string()])).optional(),
      })
      .optional()
      .describe("A sure fix: move the object by (dx, dy) after scaling it by `scale`, and set `params`"),
  })
  .meta({ id: "ValidationIssue", title: "ValidationIssue" });

const Rect = z.strictObject({ x: z.number(), y: z.number(), w: z.number(), h: z.number() });
const Lifecycle = z.enum(["active", "hidden", "removed"]);

export const ObjectStateSchema = z
  .object({
    id: z.string(),
    type: z.string(),
    params: z.record(z.string(), z.unknown()),
    x: z.number(),
    y: z.number(),
    rotation: z.number(),
    scale: z.number(),
    opacity: z.number(),
    visible: z.boolean(),
    fill: z.string(),
    stroke: z.string(),
    text: z.string(),
    strokeWidth: z.number(),
    shadow: z.boolean(),
    dashed: z.boolean(),
    layer: z.enum(["background", "main", "foreground", "overlay"]),
    z: z.number(),
    persist: z.boolean(),
    decorative: z.boolean(),
    parent: z.string().optional(),
    followPlace: z.record(z.string(), z.unknown()).optional(),
    metadata: z.record(z.string(), z.unknown()).optional(),
    lifecycle: Lifecycle,
    bbox: Rect,
  })
  .meta({ id: "ObjectState", title: "ObjectState" });

export const SceneStateSchema = z
  .object({
    sceneId: z.string(),
    sceneIndex: z.number().int(),
    time: z.number(),
    themeId: z.string(),
    camera: z.object({ cx: z.number(), cy: z.number(), zoom: z.number() }),
    objects: z.record(z.string(), ObjectStateSchema),
    registry: z.record(
      z.string(),
      z.object({ type: z.string(), createdIn: z.string(), lastScene: z.string(), lifecycle: Lifecycle }),
    ),
    solvers: z.record(z.string(), z.record(z.string(), z.number())),
    values: z.record(z.string(), z.number()),
  })
  .meta({ id: "SceneState", title: "SceneState" });

const SceneInput = z.unknown().describe("A scene in LLM-facing or canonical form (it is lifted first)");
const PrevState = SceneStateSchema.nullable().optional().describe("End state of the previous scene (continuity)");

export const VersionResult = z
  .strictObject({ protocolVersion: z.string(), engineVersion: z.string(), dslVersion: z.string() })
  .meta({ id: "VersionResult", title: "VersionResult" });

export const HealthResult = z
  .strictObject({ ok: z.boolean(), uptimeMs: z.number(), rssBytes: z.number() })
  .meta({ id: "HealthResult", title: "HealthResult" });

export const SchemaGetParams = z
  .strictObject({ kind: z.enum(["canonical", "llm"]).default("llm") })
  .meta({ id: "SchemaGetParams", title: "SchemaGetParams" });
export const SchemaGetResult = z
  .strictObject({ kind: z.enum(["canonical", "llm"]), schema: z.record(z.string(), z.unknown()) })
  .meta({ id: "SchemaGetResult", title: "SchemaGetResult" });

export const ComponentInfo = z
  .strictObject({
    type: z.string(),
    category: z.string(),
    description: z.string(),
    params: z.record(z.string(), z.unknown()).describe("JSON Schema of the params"),
    anchors: z.array(z.string()),
    dynamicAnchors: z.boolean(),
    entrance: z.string(),
    draw: z.boolean(),
    write: z.boolean(),
    labelLike: z.boolean(),
    decorative: z.boolean(),
    worldSpace: z.boolean(),
    semanticRoles: z.array(z.string()),
    examples: z.array(z.record(z.string(), z.unknown())),
  })
  .meta({ id: "ComponentInfo", title: "ComponentInfo" });

export const SolverInfo = z
  .strictObject({
    type: z.string(),
    outputs: z.array(z.strictObject({ name: z.string(), unit: z.string(), description: z.string() })),
    dynamicOutputs: z.boolean(),
  })
  .meta({ id: "SolverInfo", title: "SolverInfo" });

export const CatalogResult = z
  .strictObject({
    engineVersion: z.string(),
    dslVersion: z.string(),
    components: z.array(ComponentInfo),
    actions: z.array(z.string()),
    easings: z.array(z.string()),
    colorRoles: z.array(z.string()),
    colorVariants: z.array(z.string()),
    layouts: z.array(z.strictObject({ id: z.string(), slots: z.array(z.string()) })),
    solvers: z.array(SolverInfo),
    themes: z.array(z.string()),
    entranceStyles: z.array(z.string()),
    exitStyles: z.array(z.string()),
    highlightStyles: z.array(z.string()),
    limits: z.record(z.string(), z.number()),
  })
  .meta({ id: "CatalogResult", title: "CatalogResult" });

export const LiftParams = z.strictObject({ scene: SceneInput }).meta({ id: "LiftParams", title: "LiftParams" });
export const LiftResult = z
  .strictObject({ ok: z.boolean(), scene: z.record(z.string(), z.unknown()).optional(), issues: z.array(IssueSchema) })
  .meta({ id: "LiftResult", title: "LiftResult" });

export const SceneParams = z
  .strictObject({ scene: SceneInput, prevState: PrevState })
  .meta({ id: "SceneParams", title: "SceneParams" });

export const ValidateResult = z
  .strictObject({
    ok: z.boolean(),
    issues: z.array(IssueSchema),
    scene: z.record(z.string(), z.unknown()).optional().describe("Canonical scene, when it lifted"),
  })
  .meta({ id: "ValidateResult", title: "ValidateResult" });

export const CompileResult = z
  .strictObject({
    ok: z.boolean(),
    issues: z.array(IssueSchema),
    compiled: z.record(z.string(), z.unknown()).optional().describe("CompiledScene (opaque to Python)"),
    endState: SceneStateSchema.optional(),
    stateSummary: z.string().optional().describe("Compact end-state summary for the next LLM call"),
    duration: z.number().optional(),
    beats: z.array(z.strictObject({ id: z.string(), text: z.string(), start: z.number(), end: z.number() })).optional(),
  })
  .meta({ id: "CompileResult", title: "CompileResult" });

export const StateAtParams = z
  .strictObject({ scene: SceneInput, prevState: PrevState, t: z.number().min(0) })
  .meta({ id: "StateAtParams", title: "StateAtParams" });
export const StateAtResult = z
  .strictObject({ ok: z.boolean(), issues: z.array(IssueSchema), state: SceneStateSchema.optional() })
  .meta({ id: "StateAtResult", title: "StateAtResult" });

export const SampleParams = z
  .strictObject({ scene: SceneInput, prevState: PrevState, times: z.array(z.number().min(0)).min(1).max(240) })
  .meta({ id: "SampleParams", title: "SampleParams" });
export const SampleResult = z
  .strictObject({
    ok: z.boolean(),
    issues: z.array(IssueSchema),
    frames: z.array(z.record(z.string(), z.unknown())).describe("FrameState per requested time"),
  })
  .meta({ id: "SampleResult", title: "SampleResult" });

/** Root object whose properties reference every message type (for JSON Schema / codegen). */
export const ProtocolSchema = z
  .object({
    version: VersionResult.optional(),
    health: HealthResult.optional(),
    schemaGetParams: SchemaGetParams.optional(),
    schemaGetResult: SchemaGetResult.optional(),
    catalog: CatalogResult.optional(),
    liftParams: LiftParams.optional(),
    liftResult: LiftResult.optional(),
    sceneParams: SceneParams.optional(),
    validateResult: ValidateResult.optional(),
    compileResult: CompileResult.optional(),
    stateAtParams: StateAtParams.optional(),
    stateAtResult: StateAtResult.optional(),
    sampleParams: SampleParams.optional(),
    sampleResult: SampleResult.optional(),
  })
  .meta({ title: "EngineProtocol" });

export const METHODS = [
  "version",
  "health.ping",
  "schema.get",
  "catalog.get",
  "scene.lift",
  "scene.validate",
  "scene.compile",
  "scene.stateAt",
  "scene.sample",
] as const;
