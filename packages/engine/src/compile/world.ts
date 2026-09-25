/**
 * Turning object definitions (and carried-over objects) into CompiledObjects:
 * static params, bound params, base channel values and resolved colors.
 */
import { getSpec } from "../catalog/registry";
import type { ComponentSpec } from "../catalog/spec";
import { isBinding } from "../dsl/primitives";
import type { ObjectDefT } from "../dsl/scene";
import { type ExprError, parseExpr } from "../expr/expr";
import type { ObjectState } from "../scene-state/types";
import { resolveColor, type Theme } from "../theme/theme";
import type { BoundParam, Channel, ChannelValue, CompiledObject } from "../timeline/types";
import { issue, type ValidationIssue } from "../validation/issues";

export const channel = (base: ChannelValue): Channel => ({ base, segs: [] });

/** Categories whose objects cast a soft shadow by default. */
const SHADOWED = new Set(["physics", "ui"]);

function defaultFill(spec: ComponentSpec | undefined): string {
  return spec?.defaultStyle?.fill ?? "object_a";
}

export function extractBindings(
  params: Record<string, unknown>,
  spec: ComponentSpec | undefined,
  path: string,
  issues: ValidationIssue[],
): { params: Record<string, unknown>; bound: Record<string, BoundParam> } {
  const out: Record<string, unknown> = {};
  const bound: Record<string, BoundParam> = {};
  const textParams = new Set(spec?.textParams ?? []);
  for (const [k, v] of Object.entries(params)) {
    if (isBinding(v)) {
      const isText = textParams.has(k);
      try {
        const expr = parseExpr(v.bind);
        bound[k] = { expr, src: v.bind, text: isText, ...(v.format ? { format: v.format } : {}) };
      } catch (e) {
        issues.push(
          issue(
            "EXPR_PARSE",
            `${path}/params/${k}/bind`,
            `Cannot parse expression '${v.bind}': ${(e as ExprError).message}`,
            {
              suggestion: "Use numbers, identifiers like solver.output, + - * / ^ and functions like sqrt(x).",
            },
          ),
        );
      }
      out[k] = isText ? "" : 0;
    } else out[k] = v;
  }
  return { params: out, bound };
}

export function compileObjectDef(
  def: ObjectDefT,
  theme: Theme,
  path: string,
  issues: ValidationIssue[],
): CompiledObject {
  const spec = getSpec(def.type);
  const { params, bound } = extractBindings(def.params, spec, path, issues);
  const style = def.style ?? {};
  const ds = spec?.defaultStyle ?? {};
  const shadow = style.shadow ?? (spec ? SHADOWED.has(spec.category) : false);
  const dashed = style.dashed ?? params.dashed === true;
  const obj: CompiledObject = {
    id: def.id,
    type: def.type,
    layer: def.layer,
    z: def.z,
    persist: def.persist,
    decorative: def.decorative ?? spec?.decorative ?? false,
    params,
    bound,
    ch: {
      x: channel(0),
      y: channel(0),
      rotation: channel(def.rotation),
      scale: channel(def.scale),
      opacity: channel(def.opacity),
      presence: channel(1),
      draw: channel(1),
      reveal: channel(1),
      emph: channel(0),
      fill: channel(resolveColor(theme, style.fill ?? defaultFill(spec))),
      stroke: channel(resolveColor(theme, style.stroke ?? ds.stroke ?? "text")),
      text: channel(resolveColor(theme, style.text ?? ds.text ?? "text")),
      strokeWidth: channel(style.strokeWidth ?? ds.strokeWidth ?? theme.stroke.normal),
      shadow: channel(shadow),
      dashed: channel(dashed),
    },
    links: [],
    hiddenTerms: [],
    carried: false,
  };
  if (def.parent) obj.parent = def.parent;
  if (def.metadata) obj.metadata = def.metadata;
  return obj;
}

/** A carried-over object: its end state becomes the new base values. */
export function compileCarried(s: ObjectState): CompiledObject {
  const obj: CompiledObject = {
    id: s.id,
    type: s.type,
    layer: s.layer,
    z: s.z,
    persist: s.persist,
    decorative: s.decorative,
    params: { ...s.params },
    bound: {},
    ch: {
      x: channel(s.x),
      y: channel(s.y),
      rotation: channel(s.rotation),
      scale: channel(s.scale),
      opacity: channel(s.opacity),
      presence: channel(s.visible ? 1 : 0),
      draw: channel(1),
      reveal: channel(1),
      emph: channel(0),
      fill: channel(s.fill),
      stroke: channel(s.stroke),
      text: channel(s.text),
      strokeWidth: channel(s.strokeWidth),
      shadow: channel(s.shadow),
      dashed: channel(s.dashed),
    },
    links: [],
    hiddenTerms: [],
    carried: true,
  };
  if (s.parent) obj.parent = s.parent;
  if (s.followPlace) obj.followPlace = s.followPlace;
  if (s.metadata) obj.metadata = s.metadata;
  return obj;
}
