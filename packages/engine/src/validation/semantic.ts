/**
 * Semantic validation: everything the schema cannot express. References, ids,
 * identity continuity, anchors, colors, action/component capabilities,
 * expressions, glyph coverage, limits. Cheap: no compile needed.
 */
import { getSpec } from "../catalog/registry";
import type { ComponentSpec } from "../catalog/spec";
import type { ActionT } from "../dsl/actions";
import { isAnchorRef, isBinding } from "../dsl/primitives";
import { LIMITS, type ObjectDefT, type SceneT } from "../dsl/scene";
import { EXPR_CONSTANTS, exprRefs, parseExpr } from "../expr/expr";
import { getLayout } from "../layout/templates";
import { BOX_ANCHORS } from "../math/rect";
import type { SceneState } from "../scene-state/types";
import { getSolver } from "../solvers/registry";
import { unsupportedChars } from "../text/layout";
import { isColorRef, type Theme } from "../theme/theme";
import { getTheme } from "../theme/themes";
import { didYouMean, issue, type ValidationIssue } from "./issues";

export interface WorldEntry {
  id: string;
  type: string;
  source: "declared" | "carried" | "label" | "group";
  def?: ObjectDefT;
}

/** Ids that exist in this scene: carried-over, declared, and created by label/group actions. */
export function worldObjects(scene: SceneT, prev: SceneState | null | undefined): Map<string, WorldEntry> {
  const world = new Map<string, WorldEntry>();
  const keep = new Set(scene.transition_in.keep ?? []);
  const continuing = scene.transition_in.type === "continue";
  if (prev) {
    for (const s of Object.values(prev.objects)) {
      if (s.lifecycle === "removed") continue;
      if (!continuing && !keep.has(s.id)) continue;
      world.set(s.id, { id: s.id, type: s.type, source: "carried" });
    }
  }
  for (const def of scene.objects) world.set(def.id, { id: def.id, type: def.type, source: "declared", def });
  for (const a of scene.timeline) {
    if (a.action === "label") {
      const id = a.labelId ?? `${a.id}_label`;
      if (!world.has(id)) world.set(id, { id, type: "label", source: "label" });
    }
    if (a.action === "group" && !world.has(a.target))
      world.set(a.target, { id: a.target, type: "group", source: "group" });
  }
  return world;
}

export function anchorNames(spec: ComponentSpec | undefined): string[] {
  return [...BOX_ANCHORS, ...(spec?.anchors ?? [])];
}

function paramNames(spec: ComponentSpec | undefined): string[] {
  return spec ? Object.keys(spec.params.shape) : [];
}

export function validateSemantics(scene: SceneT, prev?: SceneState | null): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  const theme: Theme = getTheme(scene.theme);
  const layout = getLayout(scene.layout);
  const world = worldObjects(scene, prev);
  const worldIds = [...world.keys()];
  const solverIds = new Set(scene.solvers.map((s) => s.id));
  const valueIds = new Set(scene.values.map((v) => v.id));

  const size = JSON.stringify(scene).length;
  if (size > LIMITS.maxJsonBytes) {
    issues.push(
      issue("LIMIT_JSON_SIZE", "", `Scene JSON is ${size} bytes (limit ${LIMITS.maxJsonBytes}).`, {
        suggestion: "Split the scene.",
      }),
    );
  }

  // --- duplicate ids and namespace collisions ---
  const dup = (items: { id?: string }[], base: string, what: string) => {
    const seen = new Set<string>();
    items.forEach((it, i) => {
      if (!it.id) return;
      if (seen.has(it.id))
        issues.push(
          issue("DUPLICATE_ID", `${base}/${i}/id`, `Duplicate ${what} id '${it.id}'.`, {
            suggestion: "Ids must be unique.",
          }),
        );
      seen.add(it.id);
    });
  };
  dup(scene.objects, "/objects", "object");
  dup(scene.timeline, "/timeline", "action");
  dup(scene.cues, "/cues", "cue");
  dup(scene.narration.beats, "/narration/beats", "beat");
  dup(scene.solvers, "/solvers", "solver");
  dup(scene.values, "/values", "value");
  scene.solvers.forEach((s, i) => {
    if (world.has(s.id))
      issues.push(issue("ID_COLLISION", `/solvers/${i}/id`, `Solver id '${s.id}' is also an object id.`));
  });
  scene.values.forEach((v, i) => {
    if (world.has(v.id) || solverIds.has(v.id))
      issues.push(issue("ID_COLLISION", `/values/${i}/id`, `Value id '${v.id}' collides with an object or solver id.`));
  });

  // --- identity continuity ---
  if (prev) {
    scene.objects.forEach((def, i) => {
      const reg = prev.registry[def.id];
      if (reg && reg.type !== def.type) {
        issues.push(
          issue(
            "IDENTITY_TYPE_CHANGE",
            `/objects/${i}/type`,
            `Object '${def.id}' was a ${reg.type} in scene '${reg.createdIn}' and cannot become a ${def.type}.`,
            {
              suggestion: "Use a new id for the new object, and remove or transform the old one.",
            },
          ),
        );
      }
    });
  }

  const objRef = (id: string, where: string, what = "object"): WorldEntry | undefined => {
    const e = world.get(id);
    if (!e) {
      issues.push(
        issue("UNKNOWN_OBJECT", where, `No ${what} with id '${id}'.`, {
          suggestion: didYouMean(id, worldIds) ?? "Declare it in `objects` first.",
        }),
      );
    }
    return e;
  };

  const checkAnchorRef = (ref: string, where: string) => {
    const [oid = "", anchor = ""] = ref.split(".");
    const e = objRef(oid, where);
    if (!e) return;
    const names = anchorNames(getSpec(e.type));
    if (!names.includes(anchor)) {
      issues.push(
        issue("UNKNOWN_ANCHOR", where, `A ${e.type} has no anchor '${anchor}'.`, {
          suggestion: didYouMean(anchor, names) ?? `Anchors: ${names.join(", ")}.`,
        }),
      );
    }
  };

  const checkColor = (ref: unknown, where: string) => {
    if (typeof ref !== "string") return;
    if (!isColorRef(ref, theme)) {
      issues.push(
        issue("COLOR_NOT_ALLOWED", where, `'${ref}' is not a theme color role.`, {
          suggestion:
            didYouMean(ref, [
              "fluid",
              "accent",
              "object_a",
              "object_b",
              "force_gravity",
              "force_buoyant",
              "text",
              "muted",
            ]) ?? "Use a color role such as 'accent', 'fluid' or 'force_buoyant'.",
        }),
      );
    }
  };

  const knownIdent = (name: string): boolean => {
    if (name === "t" || Object.hasOwn(EXPR_CONSTANTS, name) || valueIds.has(name)) return true;
    const dot = name.indexOf(".");
    if (dot < 0) return false;
    const sid = name.slice(0, dot);
    const def = scene.solvers.find((s) => s.id === sid);
    const impl = def ? getSolver(def.type) : undefined;
    return !!impl && impl.outputs.some((o) => o.name === name.slice(dot + 1));
  };

  const checkExpr = (src: string, where: string) => {
    try {
      const ast = parseExpr(src);
      for (const name of exprRefs(ast)) {
        if (!knownIdent(name)) {
          const dot = name.indexOf(".");
          const sid = dot > 0 ? name.slice(0, dot) : "";
          const def = scene.solvers.find((s) => s.id === sid);
          const impl = def ? getSolver(def.type) : undefined;
          const options = impl
            ? impl.outputs.map((o) => `${sid}.${o.name}`)
            : [...valueIds, ...[...solverIds].map((s) => `${s}.<output>`)];
          issues.push(
            issue("EXPR_UNKNOWN_IDENT", where, `Unknown identifier '${name}' in expression.`, {
              suggestion: didYouMean(name, options) ?? `Available: ${options.slice(0, 20).join(", ") || "none"}.`,
            }),
          );
        }
      }
    } catch (e) {
      issues.push(issue("EXPR_PARSE", where, `Cannot parse expression '${src}': ${(e as Error).message}`));
    }
  };

  // --- objects ---
  let rawPlacements = 0;
  scene.objects.forEach((def, i) => {
    const p = `/objects/${i}`;
    const spec = getSpec(def.type);
    if (!spec) return; // schema already rejected unknown types
    if (def.parent) {
      const e = objRef(def.parent, `${p}/parent`);
      if (e && e.type !== "group")
        issues.push(issue("PARENT_NOT_GROUP", `${p}/parent`, `Parent '${def.parent}' is a ${e.type}, not a group.`));
      if (def.parent === def.id)
        issues.push(issue("DEPENDENCY_CYCLE", `${p}/parent`, "An object cannot be its own parent."));
    }
    const place = def.place;
    if (place) {
      if (spec.worldSpace) {
        issues.push(
          issue("PLACE_IGNORED", `${p}/place`, `A ${def.type} is positioned by its endpoints; 'place' is ignored.`, {
            severity: "warning",
            suggestion: "Remove 'place'.",
          }),
        );
      }
      if ("slot" in place && !layout.slots[place.slot]) {
        issues.push(
          issue("UNKNOWN_SLOT", `${p}/place/slot`, `Layout '${layout.id}' has no slot '${place.slot}'.`, {
            suggestion:
              didYouMean(place.slot, Object.keys(layout.slots)) ?? `Slots: ${Object.keys(layout.slots).join(", ")}.`,
          }),
        );
      }
      if ("relativeTo" in place) {
        objRef(place.relativeTo, `${p}/place/relativeTo`);
        if (place.relativeTo === def.id)
          issues.push(
            issue("DEPENDENCY_CYCLE", `${p}/place/relativeTo`, "An object cannot be placed relative to itself."),
          );
      }
      if ("at" in place) checkAnchorRef(place.at, `${p}/place/at`);
      if ("anchor" in place && place.anchor && !anchorNames(spec).includes(place.anchor)) {
        issues.push(
          issue("UNKNOWN_ANCHOR", `${p}/place/anchor`, `A ${def.type} has no anchor '${place.anchor}'.`, {
            suggestion: `Anchors: ${anchorNames(spec).join(", ")}.`,
          }),
        );
      }
      if ("x" in place) rawPlacements++;
    }
    checkColor(def.style?.fill, `${p}/style/fill`);
    checkColor(def.style?.stroke, `${p}/style/stroke`);
    checkColor(def.style?.text, `${p}/style/text`);
    for (const key of spec.pointParams ?? []) {
      const v = def.params[key];
      if (typeof v === "string") {
        if (isAnchorRef(v)) checkAnchorRef(v, `${p}/params/${key}`);
        else
          issues.push(
            issue("BAD_POINT", `${p}/params/${key}`, `'${v}' is not a point: use {x, y} or 'object.anchor'.`),
          );
      }
    }
    for (const key of spec.objectParams ?? []) {
      const v = def.params[key];
      if (typeof v === "string") objRef(v, `${p}/params/${key}`);
    }
    for (const key of spec.colorParams ?? []) checkColor(def.params[key], `${p}/params/${key}`);
    for (const [key, v] of Object.entries(def.params)) {
      if (isBinding(v)) checkExpr(v.bind, `${p}/params/${key}/bind`);
    }
    for (const key of spec.textParams ?? []) {
      const v = def.params[key];
      if (typeof v !== "string") continue;
      const bad = unsupportedChars(v);
      if (bad.length) {
        issues.push(
          issue(
            "UNSUPPORTED_GLYPH",
            `${p}/params/${key}`,
            `The font cannot draw: ${bad.map((c) => JSON.stringify(c)).join(" ")}.`,
            { severity: "warning", suggestion: "Rephrase with plain characters, or use an equation for symbols." },
          ),
        );
      }
    }
    for (const problem of spec.validate?.(def.params) ?? []) {
      issues.push(
        issue(
          problem.code,
          `${p}/params/${problem.param}`,
          problem.message,
          problem.suggestion ? { suggestion: problem.suggestion } : {},
        ),
      );
    }
    if (def.type === "image") {
      issues.push(
        issue(
          "IMAGE_ASSET_MISSING",
          `${p}/params/asset`,
          `No local asset '${String(def.params.asset)}' exists (v1 has no asset library).`,
          { severity: "warning", suggestion: "Draw the idea with shapes and components instead." },
        ),
      );
    }
  });
  if (rawPlacements > LIMITS.maxRawPlacementsWarn) {
    issues.push(
      issue("RAW_COORDS_OVERUSE", "/objects", `${rawPlacements} objects use raw x/y placement.`, {
        severity: "warning",
        suggestion: "Prefer layout slots and relative placement.",
      }),
    );
  }

  // --- values ---
  scene.values.forEach((v, i) => {
    if ((v.value === undefined) === (v.expr === undefined)) {
      issues.push(issue("VALUE_SHAPE", `/values/${i}`, `Value '${v.id}' needs exactly one of 'value' or 'expr'.`));
    }
    if (v.expr) checkExpr(v.expr, `/values/${i}/expr`);
  });

  // --- solvers ---
  scene.solvers.forEach((s, i) => {
    const impl = getSolver(s.type);
    if (!impl) {
      issues.push(
        issue(
          "SOLVER_NOT_IMPLEMENTED",
          `/solvers/${i}/type`,
          `Solver type '${s.type}' is not available in this engine build.`,
        ),
      );
      return;
    }
    for (const ref of impl.objectRefs(s)) {
      const e = objRef(ref.id, `/solvers/${i}/${ref.role}`);
      if (e && ref.types.length && !ref.types.includes(e.type)) {
        issues.push(
          issue(
            "SOLVER_OBJECT_TYPE",
            `/solvers/${i}/${ref.role}`,
            `Solver '${s.id}' needs ${ref.role} to be one of ${ref.types.join(", ")}, got ${e.type}.`,
          ),
        );
      }
    }
  });

  // --- actions ---
  scene.timeline.forEach((a, i) => validateAction(a, i));

  function spec(id: string): ComponentSpec | undefined {
    const e = world.get(id);
    return e ? getSpec(e.type) : undefined;
  }

  function validateAction(a: ActionT, i: number) {
    const p = `/timeline/${i}`;
    const needsObject = !["run_solver", "camera_move", "cut", "transition", "wait"].includes(a.action);
    if (needsObject && "target" in a && a.action !== "group") objRef(a.target, `${p}/target`);
    switch (a.action) {
      case "run_solver":
        if (!solverIds.has(a.target))
          issues.push(
            issue("UNKNOWN_SOLVER", `${p}/target`, `No solver with id '${a.target}'.`, {
              suggestion: didYouMean(a.target, [...solverIds]),
            }),
          );
        break;
      case "draw":
        if (world.has(a.target) && !spec(a.target)?.draw)
          issues.push(
            issue("ACTION_UNSUPPORTED", `${p}/action`, `A ${world.get(a.target)?.type} cannot be drawn.`, {
              suggestion: "Use create with style 'fade' or 'pop'.",
            }),
          );
        break;
      case "write": {
        const e = world.get(a.target);
        if (e && !spec(a.target)?.write)
          issues.push(
            issue("ACTION_UNSUPPORTED", `${p}/action`, `A ${e.type} cannot be written.`, {
              suggestion: "write works on text-like components and equations.",
            }),
          );
        if (a.terms && e?.def) checkTerms(e.def, a.terms, `${p}/terms`);
        break;
      }
      case "highlight":
        checkColor(a.color, `${p}/color`);
        if (a.terms) {
          const e = world.get(a.target);
          if (e?.def) checkTerms(e.def, a.terms, `${p}/terms`);
        }
        break;
      case "color_change":
        checkColor(a.to, `${p}/to`);
        break;
      case "transform": {
        const s = spec(a.target);
        if (!a.changes && !a.into) issues.push(issue("TRANSFORM_EMPTY", p, "transform needs 'changes' or 'into'."));
        a.changes?.forEach((c, j) => {
          const names = paramNames(s);
          if (s && !names.includes(c.param))
            issues.push(
              issue(
                "UNKNOWN_PARAM",
                `${p}/changes/${j}/param`,
                `A ${world.get(a.target)?.type} has no param '${c.param}'.`,
                { suggestion: didYouMean(c.param, names) ?? `Params: ${names.join(", ")}.` },
              ),
            );
          if (s?.colorParams?.includes(c.param)) checkColor(c.to, `${p}/changes/${j}/to`);
        });
        if (a.into) objRef(a.into, `${p}/into`);
        break;
      }
      case "morph":
        objRef(a.into, `${p}/into`);
        for (const [id, field] of [
          [a.target, "target"],
          [a.into, "into"],
        ] as const) {
          const e = world.get(id);
          if (e && !["path", "polygon", "circle", "rectangle", "ellipse"].includes(e.type)) {
            issues.push(
              issue("ACTION_UNSUPPORTED", `${p}/${field}`, `morph works between shapes and paths, not a ${e.type}.`),
            );
          }
        }
        break;
      case "label":
        checkColor(a.color, `${p}/color`);
        if (typeof a.text === "string" && unsupportedChars(a.text).length) {
          issues.push(
            issue("UNSUPPORTED_GLYPH", `${p}/text`, `The font cannot draw: ${unsupportedChars(a.text).join(" ")}.`, {
              severity: "warning",
            }),
          );
        }
        if (isBinding(a.text)) checkExpr(a.text.bind, `${p}/text/bind`);
        break;
      case "follow":
        objRef(a.leader, `${p}/leader`);
        if (a.anchor && world.has(a.leader) && !anchorNames(spec(a.leader)).includes(a.anchor)) {
          issues.push(issue("UNKNOWN_ANCHOR", `${p}/anchor`, `No anchor '${a.anchor}' on '${a.leader}'.`));
        }
        break;
      case "group":
        a.members.forEach((m, j) => objRef(m, `${p}/members/${j}`));
        if (world.get(a.target)?.type !== "group")
          issues.push(issue("NOT_A_GROUP", `${p}/target`, `'${a.target}' exists and is not a group.`));
        break;
      case "ungroup":
        if (world.has(a.target) && world.get(a.target)?.type !== "group")
          issues.push(issue("NOT_A_GROUP", `${p}/target`, `'${a.target}' is not a group.`));
        break;
      case "bind":
      case "animate": {
        const prop = a.property;
        if (prop.startsWith("params.")) {
          const names = paramNames(spec(a.target));
          const name = prop.slice(7);
          if (world.has(a.target) && !names.includes(name))
            issues.push(
              issue("UNKNOWN_PARAM", `${p}/property`, `No param '${name}' on '${a.target}'.`, {
                suggestion: didYouMean(name, names),
              }),
            );
        }
        if (a.action === "bind") checkExpr(a.bind, `${p}/bind`);
        if (a.action === "animate" && ["fill", "stroke", "text"].includes(prop))
          a.keyframes.forEach((k, j) => checkColor(k.value, `${p}/keyframes/${j}/value`));
        break;
      }
      case "camera_move":
        a.focus?.forEach((id, j) => objRef(id, `${p}/focus/${j}`));
        if (a.follow) objRef(a.follow, `${p}/follow`);
        break;
      case "transition":
        checkColor(a.color, `${p}/color`);
        break;
      default:
        break;
    }
  }

  function checkTerms(def: ObjectDefT, terms: string[], where: string) {
    const parts = def.params.parts as { id: string }[] | undefined;
    if (!Array.isArray(parts)) {
      issues.push(issue("TERMS_UNSUPPORTED", where, `'${def.id}' has no terms (only equations do).`));
      return;
    }
    const ids = parts.map((x) => x.id);
    for (const t of terms)
      if (!ids.includes(t))
        issues.push(
          issue("UNKNOWN_TERM", where, `Equation '${def.id}' has no part '${t}'.`, {
            suggestion: didYouMean(t, ids) ?? `Parts: ${ids.join(", ")}.`,
          }),
        );
  }

  if (scene.narration.beats.length === 0) {
    issues.push(
      issue("NARRATION_MISSING", "/narration/beats", "The scene has no narration beats, so no captions.", {
        severity: "warning",
        suggestion: "Add at least one beat.",
      }),
    );
  }
  return issues;
}
