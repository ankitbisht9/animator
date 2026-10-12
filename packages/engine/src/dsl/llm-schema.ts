/**
 * Projection of the canonical DSL JSON Schema into the LLM-facing schema that
 * OpenAI strict Structured Outputs accepts:
 *   - every object: all properties required, additionalProperties: false;
 *     originally-optional properties become nullable (null = "use the default")
 *   - oneOf -> anyOf, const -> single-value enum
 *   - range/length/pattern/format/default keywords removed (the engine validates them)
 *   - `x-llm` metadata replaces a node (e.g. ColorRef -> enum of theme roles)
 *   - properties marked `x-llm-omit` (owned by the pipeline, e.g. `transition_in.fresh`) are left out
 * `lift()` maps the LLM form back to canonical (drops nulls, fills defaults).
 */
import { z } from "zod";
import { allSpecs } from "../catalog/registry";
import { sceneSchema } from "./scene";

type Json = { [k: string]: unknown };

const DROP = new Set([
  "default",
  "minimum",
  "maximum",
  "exclusiveMinimum",
  "exclusiveMaximum",
  "minLength",
  "maxLength",
  "pattern",
  "format",
  "minItems",
  "maxItems",
  "multipleOf",
  "title",
  "$schema",
  "id",
]);

const isObj = (v: unknown): v is Json => typeof v === "object" && v !== null && !Array.isArray(v);

const SCALARS = new Set(["string", "number", "integer", "boolean"]);

function nullable(schema: Json): Json {
  if (typeof schema.type === "string" && SCALARS.has(schema.type) && !schema.anyOf && !schema.$ref) {
    const out: Json = { ...schema, type: [schema.type, "null"] };
    if (Array.isArray(schema.enum)) out.enum = [...schema.enum, null];
    return out;
  }
  if (typeof schema.$ref === "string") {
    // A $ref must stand alone in strict mode: keep its description on the wrapper instead.
    const wrapped: Json = { anyOf: [{ $ref: schema.$ref }, { type: "null" }] };
    if (typeof schema.description === "string") wrapped.description = schema.description;
    return wrapped;
  }
  return { anyOf: [schema, { type: "null" }] };
}

/** Strict mode rejects any keyword next to `$ref` (OpenAI: "$ref cannot have keywords"). */
function bareRefs(node: unknown): unknown {
  if (Array.isArray(node)) return node.map(bareRefs);
  if (!isObj(node)) return node;
  if (typeof node.$ref === "string") return { $ref: node.$ref };
  return Object.fromEntries(Object.entries(node).map(([k, v]) => [k, bareRefs(v)]));
}

function strictify(node: unknown): unknown {
  if (Array.isArray(node)) return node.map(strictify);
  if (!isObj(node)) return node;
  const override = node["x-llm"];
  if (isObj(override))
    return strictify({ ...override, ...(node.description ? { description: node.description } : {}) });

  const out: Json = {};
  for (const [k, v] of Object.entries(node)) {
    if (DROP.has(k) || k.startsWith("x-")) continue;
    if (k === "oneOf") out.anyOf = strictify(v);
    else if (k === "const") out.enum = [v];
    else if (k === "properties" || k === "$defs") {
      out[k] = Object.fromEntries(
        Object.entries(v as Json)
          // Fields the pipeline owns (`x-llm-omit`) are not the model's to write.
          .filter(([, pv]) => !(k === "properties" && isObj(pv) && pv["x-llm-omit"] === true))
          .map(([pk, pv]) => [pk, strictify(pv)]),
      );
    } else out[k] = strictify(v);
  }
  if (out.type === "object" || out.properties) {
    const props = (out.properties as Json | undefined) ?? {};
    const required = new Set((node.required as string[] | undefined) ?? []);
    for (const key of Object.keys(props)) {
      if (!required.has(key)) props[key] = nullable(props[key] as Json);
    }
    out.type = "object";
    out.properties = props;
    out.required = Object.keys(props);
    out.additionalProperties = false;
  }
  return out;
}

/** Canonical DSL JSON Schema (draft 2020-12, input form: fields with defaults are optional). */
export function canonicalJsonSchema(): Json {
  const raw = z.toJSONSchema(sceneSchema(), { io: "input", target: "draft-2020-12", unrepresentable: "any" }) as Json;
  // The Scene schema carries a meta id, so Zod emits the root as a $ref into $defs.
  // Strict mode requires an object root: inline it.
  const ref = raw.$ref;
  if (typeof ref === "string") {
    const defs = { ...(raw.$defs as Record<string, Json>) };
    const name = ref.replace("#/$defs/", "");
    const root = defs[name];
    delete defs[name];
    return { $schema: raw.$schema, ...root, $defs: defs };
  }
  return raw;
}

/** The object types the model never writes (`hidden` specs, e.g. the pipeline's `figure`) are left out. */
function withoutHidden(schema: Json): Json {
  const hidden = new Set(
    allSpecs()
      .filter((s) => s.hidden)
      .map((s) => `Object_${s.type}`),
  );
  if (hidden.size === 0) return schema;
  const defs = { ...(schema.$defs as Record<string, Json>) };
  for (const name of hidden) delete defs[name];
  const union = defs.Object;
  if (union && Array.isArray(union.anyOf)) {
    const kept = union.anyOf.filter((v) => !(isObj(v) && hidden.has(String(v.$ref).replace("#/$defs/", ""))));
    defs.Object = { ...union, anyOf: kept };
  }
  return { ...schema, $defs: defs };
}

/** LLM-facing, strict-mode compatible JSON Schema. */
export function llmJsonSchema(): Json {
  const canonical = canonicalJsonSchema();
  const strict = withoutHidden(bareRefs(strictify(canonical)) as Json);
  return { $schema: "https://json-schema.org/draft/2020-12/schema", ...strict };
}
