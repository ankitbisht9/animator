/**
 * compile(scene, prevState) -> CompiledScene.
 *
 * normalize -> semantic validation -> world (carried + declared + synthesized)
 * -> dependency order -> prepare -> static layout -> solvers -> timing ->
 * action expansion (in time order) -> values -> dynamic segment end values.
 * Deterministic: same input, same output, byte for byte.
 */
import { getSpec } from "../catalog/registry";
import type { ComponentSpec } from "../catalog/spec";
import type { ActionT } from "../dsl/actions";
import type { PlaceT, VecT } from "../dsl/primitives";
import type { ObjectDefT, SceneT } from "../dsl/scene";
import { cameraAt, type EvalContext, evaluateObjects } from "../evaluate/evaluate";
import { type Expr, evalExpr, exprRefs, parseExpr } from "../expr/expr";
import type { Easing } from "../interp/easing";
import { DEFAULT_GAP, pinnedOrigin, relativeOrigin } from "../layout/placement";
import { getLayout, type LayoutTemplate } from "../layout/templates";
import { apply, invert, type Mat, multiply, rotation, scaling, transformRect } from "../math/matrix";
import { clamp, rad2deg, roundTo } from "../math/num";
import { hashString } from "../math/prng";
import { center, type Rect, unionAll } from "../math/rect";
import type { Vec2 } from "../math/vec";
import { validateContinuity } from "../scene-state/state";
import type { SceneState } from "../scene-state/types";
import { getSolver } from "../solvers";
import { stripMarkup } from "../text/layout";
import { motionDefault, resolveColor, type Theme } from "../theme/theme";
import { getTheme } from "../theme/themes";
import { currentSegment, type SampleEnv, sampleChannel } from "../timeline/channel";
import { resolveTiming, type TimingResult, UNTIL_SCENE_END } from "../timeline/timing";
import type { Channel, ChannelValue, CompiledObject, CompiledScene, CompiledSolver, Segment } from "../timeline/types";
import { hasErrors, issue, type ValidationIssue } from "../validation/issues";
import { validateLayout } from "../validation/layout";
import { normalizeScene } from "../validation/normalize";
import { validateSemantics, worldObjects } from "../validation/semantic";
import { CANVAS, DSL_VERSION, ENGINE_VERSION } from "../version";
import { buildMorph } from "./morph";
import { channel, compileCarried, compileObjectDef } from "./world";

export interface CompileOptions {
  prev?: SceneState | null;
}

export interface CompileResult {
  ok: boolean;
  /** Present whenever compilation got far enough to build a scene (even with errors, for debugging). */
  compiled?: CompiledScene;
  issues: ValidationIssue[];
}

const FOREVER = 1e9;

const STATIC_ENV: SampleEnv = {
  solverValue: () => 0,
  exprValue: () => 0,
  anchorOf: () => null,
};

const DYNAMIC = new Set(["solver", "expr", "follow"]);

export function compileScene(input: SceneT, opts: CompileOptions = {}): CompileResult {
  const issues: ValidationIssue[] = [];
  const { scene, issues: norm } = normalizeScene(input);
  issues.push(...norm);
  issues.push(...validateSemantics(scene, opts.prev));
  issues.push(...validateContinuity(opts.prev ?? null, scene));
  if (hasErrors(issues)) return { ok: false, issues };
  const compiler = new Compiler(scene, opts.prev ?? null, issues);
  let compiled: CompiledScene;
  try {
    compiled = compiler.run();
  } catch (e) {
    issues.push(issue("COMPILE_FAILED", "", `Internal compile error: ${(e as Error).message}`));
    return { ok: false, issues };
  }
  if (!hasErrors(issues)) issues.push(...validateLayout(compiled));
  return { ok: !hasErrors(issues), compiled, issues };
}

class Compiler {
  private readonly theme: Theme;
  private readonly layout: LayoutTemplate;
  private readonly cs: CompiledScene;
  private readonly byId = new Map<string, CompiledObject>();
  private readonly defs = new Map<string, ObjectDefT>();
  private readonly labelText = new Map<string, unknown>();
  private timing!: TimingResult;

  constructor(
    private readonly scene: SceneT,
    private readonly prev: SceneState | null,
    private readonly issues: ValidationIssue[],
  ) {
    this.theme = getTheme(scene.theme);
    this.layout = getLayout(scene.layout);
    const cam = scene.camera;
    const prevCam = prev?.camera;
    this.cs = {
      engineVersion: ENGINE_VERSION,
      dslVersion: DSL_VERSION,
      id: scene.id,
      title: scene.title,
      duration: scene.duration,
      seed: scene.seed ?? hashString(scene.id),
      themeId: this.theme.id,
      layout: this.layout.id,
      background: {
        style: scene.background.style,
        color: resolveColor(this.theme, scene.background.color ?? "background"),
      },
      captions: scene.captions,
      objects: [],
      camera: {
        ch: {
          cx: channel(cam?.center?.x ?? prevCam?.cx ?? CANVAS.width / 2),
          cy: channel(cam?.center?.y ?? prevCam?.cy ?? CANVAS.height / 2),
          zoom: channel(cam?.zoom ?? prevCam?.zoom ?? 1),
        },
      },
      solvers: [],
      values: {},
      cues: {},
      beats: [],
      actions: {},
      transitions: [],
      transitionIn: { type: scene.transition_in.type, duration: scene.transition_in.duration ?? 0.8 },
      interactions: [],
      stage: this.layout.regions.stage ??
        this.layout.regions.hero ??
        this.layout.regions.left ?? { x: 0, y: 0, w: CANVAS.width, h: CANVAS.height },
    };
  }

  run(): CompiledScene {
    this.buildWorld();
    this.timing = resolveTiming(this.scene, (a) => this.durationOf(a));
    this.issues.push(...this.timing.issues);
    this.orderObjects();
    this.presencePrescan();
    this.prepare();
    this.placeAll();
    this.precomputeSolvers();
    this.computeValues();
    this.expandActions();
    this.computeValues();
    this.finalizeDynamicEnds();
    this.finishMeta();
    return this.cs;
  }

  // ---------------------------------------------------------------- world

  private buildWorld() {
    const world = worldObjects(this.scene, this.prev);
    const declared = new Map(this.scene.objects.map((d, i) => [d.id, { d, i }]));
    for (const e of world.values()) {
      if (e.source === "carried") {
        const s = this.prev?.objects[e.id];
        if (!s) continue;
        const re = declared.get(e.id);
        if (re) {
          const o = compileObjectDef(re.d, this.theme, `/objects/${re.i}`, this.issues);
          o.carried = true;
          if (!re.d.place) {
            o.ch.x.base = s.x;
            o.ch.y.base = s.y;
          }
          o.ch.presence.base = s.visible ? 1 : 0;
          this.add(o);
          this.defs.set(e.id, re.d);
        } else {
          this.add(compileCarried(s));
        }
      }
    }
    this.scene.objects.forEach((d, i) => {
      if (this.byId.has(d.id)) return;
      this.add(compileObjectDef(d, this.theme, `/objects/${i}`, this.issues));
      this.defs.set(d.id, d);
    });
    // Objects created by actions.
    this.scene.timeline.forEach((a, i) => {
      if (a.action === "label") {
        const id = a.labelId ?? `${a.id}_label`;
        if (this.byId.has(id)) return;
        const params: Record<string, unknown> = {
          text: a.text,
          style: a.style ?? "pill",
          ...(a.color ? { color: a.color } : {}),
          ...(a.leader ? { pointTo: `${a.target}.center` } : {}),
        };
        const labelSpec = getSpec("label");
        const parsed = labelSpec ? labelSpec.params.safeParse(params) : null;
        const def: ObjectDefT = {
          id,
          type: "label",
          params: parsed?.success ? (parsed.data as Record<string, unknown>) : params,
          place: {
            relativeTo: a.target,
            side: a.side ?? "above",
            ...(a.gap !== undefined ? { gap: a.gap } : {}),
            follow: true,
          },
          rotation: 0,
          scale: 1,
          opacity: 1,
          layer: "foreground",
          z: 5,
          persist: true,
        };
        this.labelText.set(id, a.text);
        this.add(compileObjectDef(def, this.theme, `/timeline/${i}`, this.issues));
        this.defs.set(id, def);
      }
      if (a.action === "group" && !this.byId.has(a.target)) {
        const def: ObjectDefT = {
          id: a.target,
          type: "group",
          params: {},
          rotation: 0,
          scale: 1,
          opacity: 1,
          layer: "main",
          z: 0,
          persist: true,
        };
        this.add(compileObjectDef(def, this.theme, `/timeline/${i}`, this.issues));
        this.defs.set(a.target, def);
      }
    });
  }

  private add(o: CompiledObject) {
    this.byId.set(o.id, o);
    this.cs.objects.push(o);
  }

  private obj(id: string): CompiledObject | undefined {
    return this.byId.get(id);
  }

  // ---------------------------------------------------------------- ordering

  private orderObjects() {
    const deps = new Map<string, Set<string>>();
    const need = (from: string, on: string | undefined) => {
      if (!on || on === from || !this.byId.has(on)) return;
      const s = deps.get(from) ?? new Set<string>();
      deps.set(from, s);
      s.add(on);
    };
    const childrenOf = new Map<string, string[]>();
    for (const o of this.cs.objects)
      if (o.parent) childrenOf.set(o.parent, [...(childrenOf.get(o.parent) ?? []), o.id]);
    for (const a of this.scene.timeline) {
      if (a.action === "group")
        for (const m of a.members) childrenOf.set(a.target, [...(childrenOf.get(a.target) ?? []), m]);
    }
    const needBox = (from: string, on: string) => {
      need(from, on);
      for (const c of childrenOf.get(on) ?? []) need(from, c);
    };
    for (const o of this.cs.objects) {
      const spec = getSpec(o.type);
      need(o.id, o.parent);
      const place = this.defs.get(o.id)?.place;
      if (place && "relativeTo" in place) needBox(o.id, place.relativeTo);
      if (place && "at" in place) need(o.id, place.at.split(".")[0]);
      if (o.followPlace?.kind === "relative") needBox(o.id, o.followPlace.relativeTo);
      if (o.followPlace?.kind === "pin") need(o.id, o.followPlace.at.split(".")[0]);
      for (const k of spec?.pointParams ?? []) {
        const v = o.params[k];
        if (typeof v === "string") need(o.id, v.split(".")[0]);
      }
      for (const k of spec?.objectParams ?? []) {
        const v = o.params[k];
        if (typeof v === "string") needBox(o.id, v);
      }
    }
    for (const a of this.scene.timeline) {
      if (a.action === "follow") need(a.target, a.leader);
      if (a.action === "group") for (const m of a.members) need(m, a.target);
    }
    // Kahn's algorithm, stable by original order.
    const order: CompiledObject[] = [];
    const indeg = new Map(this.cs.objects.map((o) => [o.id, 0]));
    const rev = new Map<string, string[]>();
    for (const [from, set] of deps) {
      indeg.set(from, set.size);
      for (const on of set) rev.set(on, [...(rev.get(on) ?? []), from]);
    }
    const ready = this.cs.objects.filter((o) => (indeg.get(o.id) ?? 0) === 0).map((o) => o.id);
    const pos = new Map(this.cs.objects.map((o, i) => [o.id, i]));
    while (ready.length) {
      ready.sort((a, b) => (pos.get(a) ?? 0) - (pos.get(b) ?? 0));
      const id = ready.shift() as string;
      order.push(this.byId.get(id) as CompiledObject);
      for (const next of rev.get(id) ?? []) {
        const d = (indeg.get(next) ?? 0) - 1;
        indeg.set(next, d);
        if (d === 0) ready.push(next);
      }
    }
    if (order.length < this.cs.objects.length) {
      const stuck = this.cs.objects.filter((o) => !order.includes(o)).map((o) => o.id);
      this.issues.push(
        issue("DEPENDENCY_CYCLE", "/objects", `Objects depend on each other in a cycle: ${stuck.join(", ")}.`, {
          suggestion: "Break the cycle: place one of them in a slot or at raw coordinates.",
        }),
      );
      order.push(...this.cs.objects.filter((o) => !order.includes(o)));
    }
    this.cs.objects = order;
  }

  // ---------------------------------------------------------------- presence

  private sortedActions(): { a: ActionT; i: number; start: number; end: number }[] {
    return this.scene.timeline
      .map((a, i) => {
        const t = a.id ? this.timing.actions.get(a.id) : undefined;
        return { a, i, start: t?.start ?? 0, end: t?.end ?? 0 };
      })
      .sort((x, y) => x.start - y.start || x.i - y.i);
  }

  /** Objects whose first presence event reveals them start hidden. */
  private presencePrescan() {
    const decided = new Set<string>();
    for (const o of this.cs.objects) if (o.carried) decided.add(o.id);
    const raise = (id: string) => {
      if (decided.has(id)) return;
      decided.add(id);
      const o = this.obj(id);
      if (o) o.ch.presence.base = 0;
    };
    const lower = (id: string) => decided.add(id);
    for (const { a } of this.sortedActions()) {
      switch (a.action) {
        case "create":
        case "show":
        case "draw":
        case "write":
          raise(a.target);
          break;
        case "label":
          raise(a.labelId ?? `${a.id}_label`);
          break;
        case "transform":
          lower(a.target);
          if (a.into) raise(a.into);
          break;
        case "morph":
          if (!decided.has(a.into)) raise(a.into);
          break;
        case "remove":
        case "hide":
          lower(a.target);
          break;
        default:
          break;
      }
    }
    // Terms revealed by `write` start hidden.
    for (const { a } of this.sortedActions()) {
      if (a.action !== "write" || !a.terms) continue;
      const o = this.obj(a.target);
      if (!o) continue;
      for (const term of a.terms) {
        if (!o.hiddenTerms.includes(term)) {
          o.hiddenTerms.push(term);
          o.ch[`term.${term}`] = channel(0);
        }
      }
    }
  }

  // ---------------------------------------------------------------- prepare + layout

  private prepare() {
    for (const o of this.cs.objects) {
      const spec = getSpec(o.type);
      if (!spec?.prepare) continue;
      try {
        o.prep = spec.prepare(o.params);
      } catch (e) {
        this.issues.push(
          issue("PREPARE_FAILED", this.pathOf(o.id), `Could not prepare ${o.type} '${o.id}': ${(e as Error).message}`),
        );
      }
    }
  }

  private pathOf(id: string): string {
    const i = this.scene.objects.findIndex((d) => d.id === id);
    return i >= 0 ? `/objects/${i}` : "";
  }

  private defaultRect(): Rect {
    return this.cs.stage;
  }

  /** World position for an object's origin given a placement, at the state in `ctx`. */
  private placeOrigin(o: CompiledObject, place: PlaceT, ctx: EvalContext, where: string): Vec2 | null {
    const ev = ctx.objects.get(o.id);
    if (!ev) return null;
    const rot = Number(sampleChannel(o.ch.rotation, ctx.t, ctx.env) ?? 0);
    const scl = Number(sampleChannel(o.ch.scale, ctx.t, ctx.env) ?? 1);
    const rs: Mat = multiply(rotation(rot), scaling(scl));
    const anchorLocal = (name: string | undefined): Vec2 =>
      apply(rs, ev.localAnchors[name ?? "center"] ?? ev.localAnchors.center ?? { x: 0, y: 0 });
    if ("slot" in place) {
      const r = this.layout.slots[place.slot] ?? this.defaultRect();
      const c = center(r);
      return pinnedOrigin(anchorLocal(place.anchor), {
        x: c.x + (place.offset?.x ?? 0),
        y: c.y + (place.offset?.y ?? 0),
      });
    }
    if ("relativeTo" in place) {
      const ref = ctx.bboxOf(place.relativeTo);
      if (!ref) return null;
      return relativeOrigin(
        transformRect(rs, ev.localBBox),
        ref,
        place.side,
        place.gap ?? DEFAULT_GAP[place.side] ?? 24,
        place.align ?? "center",
      );
    }
    if ("at" in place) {
      const [rid = "", an = ""] = place.at.split(".");
      const target = ctx.anchorOf(rid, an);
      if (!target) {
        this.issues.push(issue("UNKNOWN_ANCHOR", where, `Cannot resolve '${place.at}'.`));
        return null;
      }
      return pinnedOrigin(anchorLocal(place.anchor), {
        x: target.x + (place.offset?.x ?? 0),
        y: target.y + (place.offset?.y ?? 0),
      });
    }
    return pinnedOrigin(anchorLocal(place.anchor), { x: place.x, y: place.y });
  }

  /** Convert a world point into the object's parent frame (if it has a static parent). */
  private toParentFrame(o: CompiledObject, p: Vec2, ctx: EvalContext): Vec2 {
    if (!o.parent) return p;
    const parent = ctx.objects.get(o.parent);
    return parent ? apply(invert(parent.rawMatrix), p) : p;
  }

  private placeAll() {
    for (const o of this.cs.objects) {
      const def = this.defs.get(o.id);
      const spec = getSpec(o.type);
      if (spec?.worldSpace || (o.type === "group" && !def?.place)) {
        if (o.type === "group" && !def?.place && !o.carried) {
          const c = center(this.defaultRect());
          o.ch.x.base = c.x;
          o.ch.y.base = c.y;
        }
        continue;
      }
      const place = def?.place;
      if (!place) {
        if (o.carried) continue;
        const c = center(this.defaultRect());
        const ctx = evaluateObjects(this.cs, 0);
        const p = this.placeOrigin(o, { x: c.x, y: c.y }, ctx, this.pathOf(o.id));
        if (p) {
          const local = this.toParentFrame(o, p, ctx);
          o.ch.x.base = local.x;
          o.ch.y.base = local.y;
        }
        continue;
      }
      const follow = "follow" in place && place.follow === true;
      if (follow && "relativeTo" in place) {
        o.followPlace = {
          kind: "relative",
          relativeTo: place.relativeTo,
          side: place.side,
          gap: place.gap ?? DEFAULT_GAP[place.side] ?? 24,
          align: place.align ?? "center",
        };
      } else if (follow && "at" in place) {
        o.followPlace = {
          kind: "pin",
          at: place.at,
          anchor: place.anchor ?? "center",
          dx: place.offset?.x ?? 0,
          dy: place.offset?.y ?? 0,
        };
      }
      const ctx = evaluateObjects(this.cs, 0);
      const p = this.placeOrigin(o, place, ctx, `${this.pathOf(o.id)}/place`);
      if (p) {
        const local = this.toParentFrame(o, p, ctx);
        o.ch.x.base = roundTo(local.x, 4);
        o.ch.y.base = roundTo(local.y, 4);
      }
    }
  }

  // ---------------------------------------------------------------- solvers & values

  private runSolverAt(def: SceneT["solvers"][number], t: number, idx: number) {
    const impl = getSolver(def.type);
    if (!impl) return null;
    const out = impl.run(def, {
      theme: this.theme,
      at: evaluateObjects(this.cs, t),
      path: `/solvers/${idx}`,
      seed: this.cs.seed,
    });
    return out;
  }

  private precomputeSolvers() {
    this.scene.solvers.forEach((def, i) => {
      const out = this.runSolverAt(def, 0, i);
      if (!out) return;
      // A solver that runs later is re-evaluated at its start with the scene as it is then;
      // only report problems from this t=0 pass for solvers that never run.
      if (!this.scene.timeline.some((a) => a.action === "run_solver" && a.target === def.id))
        this.issues.push(...out.issues);
      this.cs.solvers.push({
        id: def.id,
        type: def.type,
        t0: null,
        t1: null,
        timeScale: 1,
        dt: out.dt,
        channels: out.channels,
        constants: out.constants,
        outcome: out.outcome,
      });
    });
  }

  private computeValues() {
    const values: Record<string, number> = {};
    const solvers = new Map(this.cs.solvers.map((s) => [s.id, s]));
    const pending = new Map(this.scene.values.map((v, i) => [v.id, { v, i }]));
    const visiting = new Set<string>();
    const resolve = (name: string): number | undefined => {
      if (name in values) return values[name];
      if (pending.has(name)) return compute(name);
      const dot = name.indexOf(".");
      if (dot > 0) {
        const s = solvers.get(name.slice(0, dot));
        if (!s) return undefined;
        const key = name.slice(dot + 1);
        const arr = s.channels[key];
        return arr ? arr[arr.length - 1] : s.constants[key];
      }
      return undefined;
    };
    const compute = (id: string): number => {
      const entry = pending.get(id);
      if (!entry) return Number.NaN;
      if (visiting.has(id)) {
        this.issues.push(issue("VALUE_CYCLE", `/values/${entry.i}`, `Value '${id}' depends on itself.`));
        return Number.NaN;
      }
      visiting.add(id);
      let v = entry.v.value ?? Number.NaN;
      if (entry.v.expr) {
        try {
          v = evalExpr(parseExpr(entry.v.expr), resolve);
        } catch (e) {
          this.issues.push(issue("VALUE_EVAL", `/values/${entry.i}/expr`, (e as Error).message));
        }
      }
      visiting.delete(id);
      pending.delete(id);
      values[id] = v;
      return v;
    };
    for (const v of this.scene.values) compute(v.id);
    this.cs.values = values;
  }

  // ---------------------------------------------------------------- timing helpers

  private writeDuration(targetId: string): number {
    const w = this.theme.motion.write.duration;
    const def = this.defs.get(targetId) ?? this.scene.objects.find((d) => d.id === targetId);
    const text = def?.params.text ?? this.labelText.get(targetId);
    const parts = def?.params.parts;
    let chars = 12;
    if (typeof text === "string") chars = stripMarkup(text).length;
    else if (Array.isArray(parts)) chars = parts.length * 6;
    return clamp(0.3 + chars * 0.035, w, 3.5);
  }

  private durationOf(a: ActionT): number {
    if (a.duration !== undefined) return a.duration;
    const md = (c: keyof Theme["motion"]) => this.theme.motion[c].duration;
    switch (a.action) {
      case "create": {
        const style =
          a.style && a.style !== "auto" ? a.style : (getSpec(this.worldType(a.target) ?? "")?.entrance ?? "fade");
        if (style === "write") return this.writeDuration(a.target);
        if (style === "draw") return md("draw");
        return md("enter");
      }
      case "remove":
        return md("exit");
      case "show":
      case "hide":
      case "fade":
        return md("fade");
      case "move":
        return md("move");
      case "scale":
      case "rotate":
      case "transform":
      case "morph":
        return md("transform");
      case "color_change":
        return md("color");
      case "draw":
        return md("draw");
      case "write":
        return this.writeDuration(a.target);
      case "highlight":
        return md("emphasis");
      case "label":
        return md("label");
      case "follow":
      case "bind":
        return UNTIL_SCENE_END;
      case "group":
      case "ungroup":
      case "cut":
        return 0;
      case "run_solver": {
        const def = this.scene.solvers.find((s) => s.id === a.target);
        const impl = def ? getSolver(def.type) : undefined;
        return def && impl ? impl.naturalDuration(def) : 3;
      }
      case "camera_move":
        return md("camera");
      case "transition":
        return 0.8;
      case "animate":
        return Math.max(0, ...a.keyframes.map((k) => k.time));
      case "wait":
        return 1;
    }
  }

  private worldType(id: string): string | undefined {
    return this.byId.get(id)?.type ?? this.scene.objects.find((d) => d.id === id)?.type;
  }

  // ---------------------------------------------------------------- segments

  private channelOf(o: CompiledObject, name: string): Channel {
    let ch = o.ch[name];
    if (!ch) {
      let base: ChannelValue = 0;
      if (name.startsWith("params.")) {
        const v = o.params[name.slice(7)];
        const spec = getSpec(o.type);
        if (typeof v === "string" && spec?.colorParams?.includes(name.slice(7))) base = resolveColor(this.theme, v);
        else if (typeof v === "number" || typeof v === "string" || typeof v === "boolean") base = v;
      }
      ch = channel(base);
      o.ch[name] = ch;
    }
    return ch;
  }

  private addSeg(ch: Channel, seg: Segment, label: string, where: string) {
    for (const s of ch.segs) {
      const zeroA = seg.t1 === seg.t0;
      const zeroB = s.t1 === s.t0;
      const overlap =
        (!zeroA && !zeroB && seg.t0 < s.t1 && s.t0 < seg.t1) ||
        (zeroA && !zeroB && seg.t0 > s.t0 && seg.t0 < s.t1) ||
        (zeroB && !zeroA && s.t0 > seg.t0 && s.t0 < seg.t1) ||
        (zeroA && zeroB && seg.t0 === s.t0);
      if (overlap) {
        this.issues.push(
          issue(
            "TRACK_CONFLICT",
            where,
            `Actions '${s.src}' and '${seg.src}' both animate ${label} between ${roundTo(Math.max(s.t0, seg.t0), 3)}s and ${roundTo(Math.min(s.t1, seg.t1), 3)}s.`,
            { suggestion: "Run them one after the other (start: {after: ...}), or combine them into one action." },
          ),
        );
        return;
      }
    }
    ch.segs.push(seg);
    ch.segs.sort((a, b) => a.t0 - b.t0 || a.t1 - b.t1);
  }

  private valueAt(ch: Channel, t: number): ChannelValue {
    const seg = currentSegment(ch, t);
    const env = seg && DYNAMIC.has(seg.k) ? evaluateObjects(this.cs, t).env : STATIC_ENV;
    return sampleChannel(ch, t, env) ?? ch.base;
  }

  private num(o: CompiledObject, name: string, t: number): number {
    const v = this.valueAt(this.channelOf(o, name), t);
    return typeof v === "number" ? v : Number(v) || 0;
  }

  private ease(a: ActionT, cat: keyof Theme["motion"]): Easing {
    return a.easing ?? motionDefault(this.theme, cat).easing;
  }

  // ---------------------------------------------------------------- actions

  private expandActions() {
    for (const [id, t] of this.timing.actions) {
      const a = this.scene.timeline.find((x) => x.id === id);
      this.cs.actions[id] = {
        start: t.start,
        end: t.end,
        action: a?.action ?? "",
        ...(a && "target" in a && a.target ? { target: a.target } : {}),
      };
    }
    for (const { a, i, start, end } of this.sortedActions()) {
      try {
        this.expand(a, `/timeline/${i}`, start, end);
      } catch (e) {
        this.issues.push(
          issue("ACTION_FAILED", `/timeline/${i}`, `Could not compile action '${a.id}': ${(e as Error).message}`),
        );
      }
    }
  }

  private tween(
    o: CompiledObject,
    name: string,
    a: ActionT,
    where: string,
    start: number,
    end: number,
    to: number,
    ease: Easing,
    arc?: number,
  ) {
    const from = this.num(o, name, start);
    const seg: Segment = { k: "tween", t0: start, t1: end, from, to, ease, src: a.id ?? "?" };
    if (arc) seg.arc = arc;
    this.addSeg(this.channelOf(o, name), seg, `${o.id}.${name}`, where);
  }

  private presence(
    o: CompiledObject,
    a: ActionT,
    where: string,
    start: number,
    end: number,
    to: number,
    style: string,
    ease: Easing,
  ) {
    const from = this.num(o, "presence", start);
    this.addSeg(
      o.ch.presence as Channel,
      { k: "presence", t0: start, t1: end, from, to, ease, style, src: a.id ?? "?" },
      `${o.id}.presence`,
      where,
    );
  }

  private ensurePresent(o: CompiledObject, a: ActionT, where: string, t: number) {
    if (this.num(o, "presence", t) < 0.999) {
      this.addSeg(
        o.ch.presence as Channel,
        { k: "step", t0: t, t1: t, value: 1, src: a.id ?? "?" },
        `${o.id}.presence`,
        where,
      );
    }
  }

  private expand(a: ActionT, where: string, start: number, end: number) {
    const src = a.id ?? "?";
    const target = "target" in a ? this.obj(a.target) : undefined;
    const spec: ComponentSpec | undefined = target ? getSpec(target.type) : undefined;
    switch (a.action) {
      case "create": {
        if (!target) return;
        const style = a.style && a.style !== "auto" ? a.style : (spec?.entrance ?? "fade");
        if (this.num(target, "presence", start) >= 0.999) {
          this.issues.push(
            issue("CREATE_EXISTING", where, `'${target.id}' is already visible at ${roundTo(start, 3)}s.`, {
              severity: "warning",
              suggestion: "Use highlight to draw attention to it, or remove it first.",
            }),
          );
          return;
        }
        const ease: Easing =
          a.easing ??
          (style === "pop" || style === "zoom"
            ? "back_out"
            : style === "write"
              ? motionDefault(this.theme, "write").easing
              : style === "draw"
                ? motionDefault(this.theme, "draw").easing
                : "ease_out");
        this.presence(target, a, where, start, end, 1, style, ease);
        return;
      }
      case "remove":
        if (!target) return;
        this.presence(target, a, where, start, end, 0, a.style ?? "fade", this.ease(a, "exit"));
        target.removedAt = end;
        return;
      case "show":
        if (target) this.presence(target, a, where, start, end, 1, "fade", this.ease(a, "fade"));
        return;
      case "hide":
        if (target) this.presence(target, a, where, start, end, 0, "fade", this.ease(a, "fade"));
        return;
      case "move": {
        if (!target) return;
        const ctx = evaluateObjects(this.cs, start);
        let dest: Vec2 | null = null;
        if (a.to) {
          const world = this.placeOrigin(target, a.to, ctx, `${where}/to`);
          dest = world ? this.toParentFrame(target, world, ctx) : null;
        } else if (a.by) {
          dest = { x: this.num(target, "x", start) + a.by.x, y: this.num(target, "y", start) + a.by.y };
        } else {
          this.issues.push(issue("MOVE_EMPTY", where, "move needs 'to' or 'by'."));
        }
        if (!dest) return;
        if (target.followPlace) {
          this.issues.push(
            issue("MOVE_FOLLOWING", where, `'${target.id}' follows another object; moving it has no effect.`, {
              severity: "warning",
            }),
          );
          return;
        }
        const ease = this.ease(a, "move");
        const x0 = this.num(target, "x", start);
        const y0 = this.num(target, "y", start);
        const arc =
          a.path === "arc" ? -(a.arcHeight ?? Math.max(60, Math.hypot(dest.x - x0, dest.y - y0) * 0.3)) : undefined;
        this.tween(target, "x", a, where, start, end, dest.x, ease);
        this.tween(target, "y", a, where, start, end, dest.y, ease, arc);
        return;
      }
      case "scale":
        if (target) this.tween(target, "scale", a, where, start, end, a.to, this.ease(a, "transform"));
        return;
      case "rotate":
        if (target)
          this.tween(
            target,
            "rotation",
            a,
            where,
            start,
            end,
            a.to ?? this.num(target, "rotation", start) + (a.by ?? 0),
            this.ease(a, "transform"),
          );
        return;
      case "fade":
        if (target) this.tween(target, "opacity", a, where, start, end, a.to, this.ease(a, "fade"));
        return;
      case "color_change": {
        if (!target) return;
        const prop = a.property ?? (spec?.labelLike ? "text" : "fill");
        const ch = this.channelOf(target, prop);
        const from = String(this.valueAt(ch, start));
        this.addSeg(
          ch,
          {
            k: "color",
            t0: start,
            t1: end,
            from,
            to: resolveColor(this.theme, a.to),
            ease: this.ease(a, "color"),
            src,
          },
          `${target.id}.${prop}`,
          where,
        );
        return;
      }
      case "draw":
        if (!target) return;
        this.ensurePresent(target, a, where, start);
        this.addSeg(
          this.channelOf(target, "draw"),
          {
            k: "tween",
            t0: start,
            t1: end,
            from: a.reverse ? 1 : 0,
            to: a.reverse ? 0 : 1,
            ease: this.ease(a, "draw"),
            src,
          },
          `${target.id}.draw`,
          where,
        );
        return;
      case "write": {
        if (!target) return;
        this.ensurePresent(target, a, where, start);
        const ease = this.ease(a, "write");
        if (a.terms?.length) {
          const n = a.terms.length;
          const d = (end - start) / n;
          a.terms.forEach((term, k) => {
            this.addSeg(
              this.channelOf(target, `term.${term}`),
              { k: "tween", t0: start + k * d, t1: start + (k + 1) * d, from: 0, to: 1, ease, src },
              `${target.id}.term.${term}`,
              where,
            );
          });
        } else {
          this.addSeg(
            this.channelOf(target, "reveal"),
            { k: "tween", t0: start, t1: end, from: 0, to: 1, ease, src },
            `${target.id}.reveal`,
            where,
          );
        }
        return;
      }
      case "transform":
        if (target) this.expandTransform(a, target, spec, where, start, end);
        return;
      case "morph": {
        if (!target) return;
        const into = this.obj(a.into);
        if (!into) return;
        const m = buildMorph(target, into, start, end);
        if (!m) {
          this.issues.push(issue("MORPH_FAILED", where, `Cannot build a morph from '${target.id}' to '${into.id}'.`));
          return;
        }
        target.prep = { ...(target.prep as object | undefined), morph: m };
        this.addSeg(
          this.channelOf(target, "morph"),
          { k: "tween", t0: start, t1: end, from: 0, to: 1, ease: this.ease(a, "transform"), src },
          `${target.id}.morph`,
          where,
        );
        const fill = this.channelOf(target, "fill");
        this.addSeg(
          fill,
          {
            k: "color",
            t0: start,
            t1: end,
            from: String(this.valueAt(fill, start)),
            to: String(into.ch.fill?.base ?? fill.base),
            ease: this.ease(a, "transform"),
            src,
          },
          `${target.id}.fill`,
          where,
        );
        return;
      }
      case "highlight":
        if (!target) return;
        this.addSeg(
          target.ch.emph as Channel,
          {
            k: "pulse",
            t0: start,
            t1: end,
            style: a.style ?? "pulse",
            color: resolveColor(this.theme, a.color ?? "highlight"),
            src,
            ...(a.terms ? { terms: a.terms } : {}),
          },
          `${target.id}.emphasis`,
          where,
        );
        return;
      case "label": {
        const label = this.obj(a.labelId ?? `${a.id}_label`);
        if (label)
          this.presence(label, a, where, start, end, 1, getSpec("label")?.entrance ?? "pop", this.ease(a, "label"));
        return;
      }
      case "follow": {
        if (!target) return;
        const ctx = evaluateObjects(this.cs, start);
        const anchor = a.anchor ?? "center";
        const lead = ctx.anchorOf(a.leader, anchor);
        if (!lead) return;
        const ox = a.offset?.x ?? this.num(target, "x", start) - lead.x;
        const oy = a.offset?.y ?? this.num(target, "y", start) - lead.y;
        this.addSeg(
          target.ch.x as Channel,
          { k: "follow", t0: start, t1: end, leader: a.leader, anchor, axis: "x", offset: ox, src },
          `${target.id}.x`,
          where,
        );
        this.addSeg(
          target.ch.y as Channel,
          { k: "follow", t0: start, t1: end, leader: a.leader, anchor, axis: "y", offset: oy, src },
          `${target.id}.y`,
          where,
        );
        return;
      }
      case "group":
        this.expandGroup(a, where, start);
        return;
      case "ungroup":
        this.expandUngroup(a.target, a, where, start);
        return;
      case "run_solver":
        this.expandSolver(a, where, start, end);
        return;
      case "bind": {
        if (!target) return;
        let expr: Expr;
        try {
          expr = parseExpr(a.bind);
        } catch (e) {
          this.issues.push(issue("EXPR_PARSE", `${where}/bind`, (e as Error).message));
          return;
        }
        this.addSeg(
          this.channelOf(target, a.property),
          {
            k: "expr",
            t0: start,
            t1: end,
            src: a.id ?? "?",
            expr,
            ...(a.format !== undefined ? { format: a.format } : {}),
          },
          `${target.id}.${a.property}`,
          where,
        );
        return;
      }
      case "camera_move":
        this.expandCamera(a, where, start, end);
        return;
      case "cut": {
        const ch = this.cs.camera.ch;
        if (a.center) {
          this.addSeg(ch.cx, { k: "step", t0: start, t1: start, value: a.center.x, src }, "camera.cx", where);
          this.addSeg(ch.cy, { k: "step", t0: start, t1: start, value: a.center.y, src }, "camera.cy", where);
        }
        if (a.zoom !== undefined)
          this.addSeg(ch.zoom, { k: "step", t0: start, t1: start, value: a.zoom, src }, "camera.zoom", where);
        return;
      }
      case "transition":
        this.cs.transitions.push({
          t0: start,
          t1: end,
          style: a.style,
          color: resolveColor(this.theme, a.color ?? "background"),
        });
        return;
      case "animate":
        if (target) this.expandAnimate(a, target, where, start);
        return;
      case "wait":
        return;
    }
  }

  private expandTransform(
    a: Extract<ActionT, { action: "transform" }>,
    target: CompiledObject,
    spec: ComponentSpec | undefined,
    where: string,
    start: number,
    end: number,
  ) {
    const ease = this.ease(a, "transform");
    const src = a.id ?? "?";
    for (const c of a.changes ?? []) {
      const name = `params.${c.param}`;
      const ch = this.channelOf(target, name);
      if (typeof c.to === "number") {
        this.tween(target, name, a, where, start, end, c.to, ease);
      } else if (typeof c.to === "string" && spec?.colorParams?.includes(c.param)) {
        this.addSeg(
          ch,
          {
            k: "color",
            t0: start,
            t1: end,
            from: resolveColor(this.theme, String(this.valueAt(ch, start))),
            to: resolveColor(this.theme, c.to),
            ease,
            src,
          },
          `${target.id}.${name}`,
          where,
        );
      } else {
        this.addSeg(
          ch,
          { k: "step", t0: (start + end) / 2, t1: (start + end) / 2, value: c.to, src },
          `${target.id}.${name}`,
          where,
        );
      }
    }
    if (a.into) {
      const into = this.obj(a.into);
      if (!into) return;
      const ctx = evaluateObjects(this.cs, start);
      const tb = ctx.bboxOf(target.id);
      const ib = ctx.bboxOf(into.id);
      if (!tb || !ib) return;
      const k = clamp(Math.min(ib.w / Math.max(1, tb.w), ib.h / Math.max(1, tb.h)), 0.2, 5);
      const tc = center(tb);
      const ic = center(ib);
      const fade = this.ease(a, "fade");
      this.presence(target, a, where, start, end, 0, "fade", fade);
      this.presence(into, a, where, start, end, 1, "fade", fade);
      const tx = this.num(target, "x", start);
      const ty = this.num(target, "y", start);
      const ts = this.num(target, "scale", start);
      this.tween(target, "x", a, where, start, end, tx + (ic.x - tc.x), ease);
      this.tween(target, "y", a, where, start, end, ty + (ic.y - tc.y), ease);
      this.tween(target, "scale", a, where, start, end, ts * k, ease);
      const ix = this.num(into, "x", start);
      const iy = this.num(into, "y", start);
      const is = this.num(into, "scale", start);
      const intoX = this.channelOf(into, "x");
      const intoY = this.channelOf(into, "y");
      const intoS = this.channelOf(into, "scale");
      this.addSeg(
        intoX,
        { k: "tween", t0: start, t1: end, from: ix - (ic.x - tc.x), to: ix, ease, src },
        `${into.id}.x`,
        where,
      );
      this.addSeg(
        intoY,
        { k: "tween", t0: start, t1: end, from: iy - (ic.y - tc.y), to: iy, ease, src },
        `${into.id}.y`,
        where,
      );
      this.addSeg(
        intoS,
        { k: "tween", t0: start, t1: end, from: is / k, to: is, ease, src },
        `${into.id}.scale`,
        where,
      );
    }
  }

  private expandGroup(a: Extract<ActionT, { action: "group" }>, where: string, start: number) {
    const g = this.obj(a.target);
    if (!g) return;
    let ctx = evaluateObjects(this.cs, start);
    const synthesized = !this.scene.objects.some((d) => d.id === g.id) && g.ch.x.segs.length === 0;
    if (synthesized) {
      const box = unionAll(a.members.map((m) => ctx.bboxOf(m)).filter((b): b is Rect => !!b));
      if (box) {
        const c = center(box);
        g.ch.x.base = c.x;
        g.ch.y.base = c.y;
        ctx = evaluateObjects(this.cs, start);
      }
    }
    const G = ctx.objects.get(g.id)?.rawMatrix;
    if (!G) return;
    const inv = invert(G) as [number, number, number, number, number, number];
    for (const m of a.members) {
      const o = this.obj(m);
      if (!o) continue;
      if (o.parent) {
        this.issues.push(issue("GROUP_PARENTED", where, `'${m}' already has a parent group.`));
        continue;
      }
      if (o.links.some((l) => l.t1 > start)) {
        this.issues.push(issue("GROUP_TWICE", where, `'${m}' is already in a group at ${roundTo(start, 3)}s.`));
        continue;
      }
      o.links.push({ t0: start, t1: FOREVER, group: g.id, inv });
    }
  }

  private expandUngroup(groupId: string, a: ActionT, where: string, start: number) {
    const ctx = evaluateObjects(this.cs, start);
    for (const o of this.cs.objects) {
      const link = o.links.find((l) => l.group === groupId && l.t0 <= start && start < l.t1);
      if (!link) continue;
      const M = ctx.objects.get(o.id)?.rawMatrix;
      link.t1 = start;
      if (!M) continue;
      const scale = Math.hypot(M[0], M[1]);
      const rot = rad2deg(Math.atan2(M[1], M[0]));
      const src = a.id ?? "?";
      for (const [name, value] of [
        ["x", M[4]],
        ["y", M[5]],
        ["rotation", rot],
        ["scale", scale],
      ] as const) {
        this.addSeg(
          this.channelOf(o, name),
          { k: "step", t0: start, t1: start, value: roundTo(value, 6), src },
          `${o.id}.${name}`,
          where,
        );
      }
    }
  }

  private expandSolver(a: Extract<ActionT, { action: "run_solver" }>, where: string, start: number, end: number) {
    const idx = this.scene.solvers.findIndex((s) => s.id === a.target);
    const def = this.scene.solvers[idx];
    if (!def) return;
    const out = this.runSolverAt(def, start, idx);
    if (!out) return;
    this.issues.push(...out.issues);
    const span = end - start;
    const timeScale = span > 0 ? out.duration / span : 1;
    const compiled: CompiledSolver = {
      id: def.id,
      type: def.type,
      t0: start,
      t1: end,
      timeScale,
      dt: out.dt,
      channels: out.channels,
      constants: out.constants,
      outcome: out.outcome,
    };
    const i = this.cs.solvers.findIndex((s) => s.id === def.id);
    if (i >= 0) {
      if (this.cs.solvers[i]?.t0 !== null) {
        this.issues.push(issue("SOLVER_RUN_TWICE", where, `Solver '${def.id}' is already run in this scene.`));
        return;
      }
      this.cs.solvers[i] = compiled;
    } else this.cs.solvers.push(compiled);
    for (const att of out.attach) {
      const o = this.obj(att.object);
      if (!o) continue;
      // Before the run starts, attached channels show the solver's initial state
      // (e.g. a spring scale already reads the weight of the body it holds).
      const target = this.channelOf(o, att.channel);
      const first = out.channels[att.source]?.[0];
      if (first !== undefined && !target.segs.some((sg) => sg.t0 < start))
        target.base = first * (att.gain ?? 1) + (att.offset ?? 0);
      this.addSeg(
        this.channelOf(o, att.channel),
        {
          k: "solver",
          t0: start,
          t1: end,
          solver: def.id,
          channel: att.source,
          timeScale,
          gain: att.gain ?? 1,
          offset: att.offset ?? 0,
          src: a.id ?? "?",
        },
        `${o.id}.${att.channel}`,
        where,
      );
    }
  }

  private expandCamera(a: Extract<ActionT, { action: "camera_move" }>, where: string, start: number, end: number) {
    const ch = this.cs.camera.ch;
    const ctx = evaluateObjects(this.cs, start);
    const cur = cameraAt(this.cs, start, ctx.env);
    const ease = this.ease(a, "camera");
    const src = a.id ?? "?";
    let cx = a.center?.x ?? cur.cx;
    let cy = a.center?.y ?? cur.cy;
    let zoom = a.zoom ?? cur.zoom;
    if (a.focus?.length) {
      const box = unionAll(a.focus.map((id) => ctx.bboxOf(id)).filter((b): b is Rect => !!b));
      if (box) {
        const pad = a.padding ?? 90;
        const c = center(box);
        cx = c.x;
        cy = c.y;
        if (a.zoom === undefined)
          zoom = clamp(Math.min(CANVAS.width / (box.w + 2 * pad), CANVAS.height / (box.h + 2 * pad)), 0.5, 3);
      }
    }
    if (a.follow) {
      // Smoothed follow, baked at compile time: pure lookups at playback.
      const dt = 1 / 30;
      const n = Math.max(1, Math.ceil((end - start) / dt));
      const tau = 0.35;
      const k = 1 - Math.exp(-dt / tau);
      const xs: number[] = [cur.cx];
      const ys: number[] = [cur.cy];
      for (let i = 1; i <= n; i++) {
        const t = Math.min(end, start + i * dt);
        const b = evaluateObjects(this.cs, t).bboxOf(a.follow);
        const c = b ? center(b) : { x: xs[i - 1] as number, y: ys[i - 1] as number };
        xs.push(roundTo((xs[i - 1] as number) + (c.x - (xs[i - 1] as number)) * k, 3));
        ys.push(roundTo((ys[i - 1] as number) + (c.y - (ys[i - 1] as number)) * k, 3));
      }
      this.addSeg(ch.cx, { k: "sampled", t0: start, t1: end, dt, values: xs, src }, "camera.cx", where);
      this.addSeg(ch.cy, { k: "sampled", t0: start, t1: end, dt, values: ys, src }, "camera.cy", where);
    } else {
      this.addSeg(
        ch.cx,
        { k: "tween", t0: start, t1: end, from: cur.cx, to: roundTo(cx, 4), ease, src },
        "camera.cx",
        where,
      );
      this.addSeg(
        ch.cy,
        { k: "tween", t0: start, t1: end, from: cur.cy, to: roundTo(cy, 4), ease, src },
        "camera.cy",
        where,
      );
    }
    if (zoom !== cur.zoom)
      this.addSeg(
        ch.zoom,
        { k: "tween", t0: start, t1: end, from: cur.zoom, to: roundTo(zoom, 4), ease, src },
        "camera.zoom",
        where,
      );
  }

  private expandAnimate(
    a: Extract<ActionT, { action: "animate" }>,
    target: CompiledObject,
    where: string,
    start: number,
  ) {
    const src = a.id ?? "?";
    const ch = this.channelOf(target, a.property);
    const isColor =
      ["fill", "stroke", "text"].includes(a.property) ||
      (a.property.startsWith("params.") && getSpec(target.type)?.colorParams?.includes(a.property.slice(7)));
    const kfs = a.keyframes;
    if (isColor) {
      let prevT = start;
      let prevV = String(this.valueAt(ch, start));
      for (const kf of kfs) {
        const t = start + kf.time;
        const v = resolveColor(this.theme, String(kf.value));
        if (t > prevT)
          this.addSeg(
            ch,
            { k: "color", t0: prevT, t1: t, from: prevV, to: v, ease: kf.easing ?? "ease_in_out", src },
            `${target.id}.${a.property}`,
            where,
          );
        else this.addSeg(ch, { k: "step", t0: t, t1: t, value: v, src }, `${target.id}.${a.property}`, where);
        prevT = t;
        prevV = v;
      }
      return;
    }
    const times: number[] = [];
    const values: number[] = [];
    const eases: (Easing | null)[] = [];
    if ((kfs[0]?.time ?? 0) > 0) {
      times.push(start);
      values.push(this.num(target, a.property, start));
      eases.push(null);
    }
    for (const kf of kfs) {
      times.push(start + kf.time);
      values.push(Number(kf.value));
      eases.push(kf.easing ?? null);
    }
    this.addSeg(
      ch,
      { k: "keys", t0: times[0] as number, t1: times[times.length - 1] as number, times, values, eases, src },
      `${target.id}.${a.property}`,
      where,
    );
  }

  // ---------------------------------------------------------------- finalize

  /** Dynamic segments (expr, follow) hold their t1 value afterwards: compute it now. */
  private finalizeDynamicEnds() {
    const pending: { seg: Segment & { end?: number }; ch: Channel }[] = [];
    for (const o of this.cs.objects) {
      for (const ch of Object.values(o.ch))
        for (const seg of ch.segs) if (seg.k === "expr" || seg.k === "follow") pending.push({ seg, ch });
    }
    pending.sort((a, b) => a.seg.t1 - b.seg.t1);
    for (const { seg, ch } of pending) {
      const ctx = evaluateObjects(this.cs, seg.t1);
      // End values are raw numbers (formatted expressions format them when read).
      const v =
        seg.k === "expr" ? ctx.env.exprValue(seg.expr, seg.t1 - seg.t0, seg.t1) : sampleChannel(ch, seg.t1, ctx.env);
      seg.end = typeof v === "number" && Number.isFinite(v) ? roundTo(v, 6) : 0;
    }
  }

  /**
   * Narration must not contradict the physics: if every buoyancy solver in the
   * scene says "sinks", a narration that only talks about floating is an error
   * (and vice versa). Mixed or negated statements are left to the LLM critic.
   */
  private checkNarrationAgainstSolvers() {
    const states = this.cs.solvers.filter((x) => x.type === "buoyancy").map((x) => String(x.outcome.state ?? ""));
    if (!states.length) return;
    const allSink = states.every((x) => x === "sinks");
    const allFloat = states.every((x) => x === "floats");
    const negated = (word: string) =>
      new RegExp(String.raw`\b(not|never|won't|doesn't|does not|cannot|can't|didn't)\s+(\w+\s+)?${word}`);
    this.scene.narration.beats.forEach((b, i) => {
      const text = b.text.toLowerCase().replace(/\*\*/g, "");
      const saysFloat = /\bfloat(s|ing|ed)?\b/.test(text) && !negated("float").test(text);
      const saysSink = /\b(sink(s|ing)?|sank|sunk)\b/.test(text) && !negated("sink").test(text);
      if ((allSink && saysFloat && !saysSink) || (allFloat && saysSink && !saysFloat)) {
        this.issues.push(
          issue(
            "NARRATION_CONTRADICTS_SOLVER",
            `/narration/beats/${i}/text`,
            `The narration says the body ${saysFloat ? "floats" : "sinks"}, but with these densities it ${allSink ? "sinks" : "floats"}.`,
            {
              suggestion:
                "Fix the narration, or change bodyDensity/fluidDensity (or the material) so the physics matches.",
            },
          ),
        );
      }
    });
  }

  private finishMeta() {
    this.checkNarrationAgainstSolvers();
    this.cs.cues = this.timing.cues;
    this.cs.beats = this.timing.beats;
    this.cs.interactions = this.scene.interactions.map((q, i) => ({
      id: q.id,
      at: this.timing.at(q.at, `/interactions/${i}/at`),
      question: q.question,
      choices: q.choices,
      answer: q.answer,
      explain: q.explain,
    }));
    const tin = this.scene.transition_in;
    if (tin.type === "fade" || tin.type === "wipe") {
      this.cs.transitions.unshift({
        t0: 0,
        t1: tin.duration ?? 0.8,
        style: tin.type === "fade" ? "fade_in" : "wipe_in",
        color: this.cs.background.color,
      });
    }
    // Validate expression references in values against the final solver set.
    for (const [i, v] of this.scene.values.entries()) {
      if (!v.expr) continue;
      try {
        for (const ref of exprRefs(parseExpr(v.expr))) {
          if (!(ref in this.cs.values) && !ref.includes("."))
            this.issues.push(issue("EXPR_UNKNOWN_IDENT", `/values/${i}/expr`, `Unknown identifier '${ref}'.`));
        }
      } catch {
        // already reported
      }
    }
  }
}

export type { VecT };
