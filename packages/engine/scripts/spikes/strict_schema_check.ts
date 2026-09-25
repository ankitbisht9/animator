/**
 * Spike (c): is the LLM-facing schema acceptable to OpenAI strict Structured Outputs?
 *
 * Checks the generated schema against the documented strict-mode rules and limits:
 *   - root is an object (not anyOf); every object has additionalProperties:false and
 *     lists every property in `required`
 *   - only supported keywords appear
 *   - <= 5000 object properties in total, <= 10 levels of object nesting
 *   - <= 120,000 characters across property names, $defs names, enum and const values
 *   - <= 1000 enum values in total; enums with > 250 values <= 15,000 characters
 * The live acceptance test (a real request) is scripts/spikes/openai_strict_probe.py.
 *
 *   pnpm --filter @animator/engine exec tsx scripts/spikes/strict_schema_check.ts [--write out.json]
 */
import { writeFileSync } from "node:fs";
import { llmJsonSchema } from "../../src/dsl/llm-schema";

type Json = Record<string, unknown>;

const LIMITS = { properties: 5000, depth: 10, stringChars: 120_000, enumValues: 1000, bigEnumChars: 15_000 };
const ALLOWED = new Set([
  "type",
  "properties",
  "required",
  "additionalProperties",
  "items",
  "enum",
  "anyOf",
  "$ref",
  "$defs",
  "description",
  "$schema",
]);

const schema = llmJsonSchema() as Json;
const defs = (schema.$defs ?? {}) as Record<string, Json>;
const problems: string[] = [];
let propertyCount = 0;
let enumValues = 0;
let stringChars = Object.keys(defs).reduce((n, k) => n + k.length, 0);

const isObj = (v: unknown): v is Json => typeof v === "object" && v !== null && !Array.isArray(v);

function checkNode(node: Json, where: string) {
  for (const k of Object.keys(node)) if (!ALLOWED.has(k)) problems.push(`${where}: unsupported keyword '${k}'`);
  const type = node.type;
  const isObjectType = type === "object" || (Array.isArray(type) && type.includes("object"));
  if (isObjectType) {
    if (node.additionalProperties !== false) problems.push(`${where}: additionalProperties must be false`);
    const props = Object.keys((node.properties ?? {}) as Json);
    const req = new Set((node.required ?? []) as string[]);
    for (const p of props) if (!req.has(p)) problems.push(`${where}: property '${p}' not required`);
    propertyCount += props.length;
    stringChars += props.reduce((n, p) => n + p.length, 0);
  }
  if (Array.isArray(node.enum)) {
    enumValues += node.enum.length;
    const chars = node.enum.reduce((n: number, v) => n + String(v ?? "").length, 0);
    stringChars += chars;
    if (node.enum.length > 250 && chars > LIMITS.bigEnumChars)
      problems.push(`${where}: enum too large (${chars} chars)`);
  }
  if (typeof node.$ref === "string") {
    const name = node.$ref.replace("#/$defs/", "");
    if (!defs[name]) problems.push(`${where}: dangling $ref ${node.$ref}`);
  }
  for (const [k, v] of Object.entries(node)) {
    if (k === "$defs") continue;
    if (k === "properties" && isObj(v))
      for (const [pk, pv] of Object.entries(v)) if (isObj(pv)) checkNode(pv, `${where}.${pk}`);
    if (k === "items" && isObj(v)) checkNode(v, `${where}[]`);
    if (k === "anyOf" && Array.isArray(v)) v.forEach((x, i) => isObj(x) && checkNode(x, `${where}|${i}`));
  }
}

/** Deepest chain of nested object schemas, following $refs (cycle-safe). */
function depth(node: Json, seen: Set<string>): number {
  let best = 0;
  const isObjectType = node.type === "object" || (Array.isArray(node.type) && node.type.includes("object"));
  if (typeof node.$ref === "string") {
    const name = node.$ref.replace("#/$defs/", "");
    if (seen.has(name)) return 0;
    const target = defs[name];
    return target ? depth(target, new Set([...seen, name])) : 0;
  }
  const children: Json[] = [];
  if (isObj(node.properties)) children.push(...(Object.values(node.properties).filter(isObj) as Json[]));
  if (isObj(node.items)) children.push(node.items);
  if (Array.isArray(node.anyOf)) children.push(...(node.anyOf.filter(isObj) as Json[]));
  for (const c of children) best = Math.max(best, depth(c, seen));
  // anyOf wrappers and arrays do not add a level; objects do.
  return best + (isObjectType ? 1 : 0);
}

if (schema.type !== "object" || schema.anyOf) problems.push("root must be an object schema");
checkNode(schema, "Scene");
for (const [name, def] of Object.entries(defs)) checkNode(def, `$defs.${name}`);
const maxDepth = depth(schema, new Set());

if (propertyCount > LIMITS.properties) problems.push(`too many properties: ${propertyCount}`);
if (maxDepth > LIMITS.depth) problems.push(`nesting too deep: ${maxDepth}`);
if (stringChars > LIMITS.stringChars) problems.push(`too many schema characters: ${stringChars}`);
if (enumValues > LIMITS.enumValues) problems.push(`too many enum values: ${enumValues}`);

const report = {
  bytes: JSON.stringify(schema).length,
  defs: Object.keys(defs).length,
  properties: propertyCount,
  maxObjectDepth: maxDepth,
  schemaStringChars: stringChars,
  enumValues,
  limits: LIMITS,
  ok: problems.length === 0,
  problems: problems.slice(0, 50),
};
process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
const outIdx = process.argv.indexOf("--write");
if (outIdx > 0 && process.argv[outIdx + 1]) writeFileSync(process.argv[outIdx + 1] as string, JSON.stringify(schema));
if (problems.length) process.exit(1);
