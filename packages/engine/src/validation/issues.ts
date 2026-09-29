/**
 * Machine-readable validation issues. Every validator (engine or Python-side
 * critics) reports in this shape so the correction loop can hand a single list
 * back to the LLM.
 */
import type { z } from "zod";

export type IssueSeverity = "error" | "warning" | "info";
export type IssueSource = "schema" | "semantic" | "layout" | "critic" | "visual" | "normalization";

export interface ValidationIssue {
  code: string;
  /** JSON pointer into the scene, e.g. "/timeline/3/start/after". */
  path: string;
  message: string;
  suggestion?: string;
  severity: IssueSeverity;
  source: IssueSource;
  /** Scene time (seconds) the issue was observed at, for layout issues. */
  time?: number;
  /** A sure fix, when the engine knows one: move the object by (dx, dy) world px, after scaling it
   * by `scale`, and set `params` (e.g. an arrow's `curve` that routes it clear). Applied by the
   * pipeline without another model call. */
  fix?: IssueFix;
}

export interface IssueFix {
  dx: number;
  dy: number;
  scale?: number;
  params?: Record<string, number | string>;
}

export const toPointer = (path: readonly PropertyKey[]): string =>
  path.length === 0 ? "" : `/${path.map((p) => String(p).replace(/~/g, "~0").replace(/\//g, "~1")).join("/")}`;

export function issue(
  code: string,
  path: string,
  message: string,
  opts: { suggestion?: string; severity?: IssueSeverity; source?: IssueSource; time?: number; fix?: IssueFix } = {},
): ValidationIssue {
  const out: ValidationIssue = {
    code,
    path,
    message,
    severity: opts.severity ?? "error",
    source: opts.source ?? "semantic",
  };
  if (opts.suggestion) out.suggestion = opts.suggestion;
  if (opts.time !== undefined) out.time = opts.time;
  if (opts.fix) out.fix = opts.fix;
  return out;
}

export const hasErrors = (issues: readonly ValidationIssue[]): boolean => issues.some((i) => i.severity === "error");

/** Levenshtein distance, for "did you mean" suggestions. */
export function editDistance(a: string, b: string): number {
  const dp = Array.from({ length: b.length + 1 }, (_, j) => j);
  for (let i = 1; i <= a.length; i++) {
    let prev = dp[0] as number;
    dp[0] = i;
    for (let j = 1; j <= b.length; j++) {
      const tmp = dp[j] as number;
      dp[j] = Math.min((dp[j] as number) + 1, (dp[j - 1] as number) + 1, prev + (a[i - 1] === b[j - 1] ? 0 : 1));
      prev = tmp;
    }
  }
  return dp[b.length] as number;
}

export function closest(name: string, options: readonly string[], max = 3): string[] {
  return [...options]
    .map((o) => ({ o, d: editDistance(name, o) }))
    .filter((x) => x.d <= Math.max(2, Math.floor(name.length / 3)))
    .sort((a, b) => a.d - b.d)
    .slice(0, max)
    .map((x) => x.o);
}

export function didYouMean(name: string, options: readonly string[]): string | undefined {
  const c = closest(name, options);
  return c.length ? `Did you mean ${c.map((x) => `'${x}'`).join(" or ")}?` : undefined;
}

type ZodIssue = z.core.$ZodIssue;

/** Convert Zod issues into schema ValidationIssues with actionable messages. */
export function fromZodIssues(issues: readonly ZodIssue[], root: unknown): ValidationIssue[] {
  const out: ValidationIssue[] = [];
  for (const zi of issues) {
    const path = toPointer(zi.path as PropertyKey[]);
    if (zi.code === "invalid_union" && "discriminator" in zi && zi.discriminator) {
      const got = valueAt(root, [...(zi.path as PropertyKey[])]);
      const options = ((zi as { options?: unknown[] }).options ?? []).map(String);
      const code =
        zi.discriminator === "type"
          ? "UNKNOWN_TYPE"
          : zi.discriminator === "action"
            ? "UNKNOWN_ACTION"
            : "INVALID_DISCRIMINATOR";
      out.push(
        issue(code, path, `Unknown ${zi.discriminator} ${JSON.stringify(got)}.`, {
          source: "schema",
          suggestion:
            (typeof got === "string" ? didYouMean(got, options) : undefined) ??
            `Use one of: ${options.slice(0, 40).join(", ")}${options.length > 40 ? ", ..." : ""}.`,
        }),
      );
      continue;
    }
    if (zi.code === "unrecognized_keys") {
      const keys = (zi as { keys: string[] }).keys;
      out.push(
        issue("UNKNOWN_FIELD", path, `Unknown field(s): ${keys.join(", ")}.`, {
          source: "schema",
          suggestion: "Remove the field(s); only documented fields are allowed.",
        }),
      );
      continue;
    }
    out.push(issue("SCHEMA", path, zi.message, { source: "schema" }));
  }
  return out;
}

function valueAt(root: unknown, path: PropertyKey[]): unknown {
  let cur = root as Record<PropertyKey, unknown> | undefined;
  for (const k of path) {
    if (cur === null || typeof cur !== "object") return undefined;
    cur = cur[k] as Record<PropertyKey, unknown> | undefined;
  }
  return cur;
}
