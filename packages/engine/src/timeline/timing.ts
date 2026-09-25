/**
 * Resolves cues, narration beats and action start/end times to absolute seconds.
 * Relative timing (`after` / `with` another action, `cue:` / `beat:` references,
 * delays) means retiming one action never requires rewriting the rest. Cycles
 * are reported, not looped on.
 */
import type { ActionT } from "../dsl/actions";
import type { SceneT } from "../dsl/scene";
import { roundTo } from "../math/num";
import { didYouMean, issue, type ValidationIssue } from "../validation/issues";
import type { ResolvedBeat } from "./types";

export interface ActionTime {
  start: number;
  end: number;
}

export interface TimingResult {
  cues: Record<string, number>;
  beats: ResolvedBeat[];
  actions: Map<string, ActionTime>;
  issues: ValidationIssue[];
  /** Resolve a TimeRef (number, cue:, beat:, beat_end:) to seconds. */
  at: (ref: number | string, where: string) => number;
}

/** Returned by `durationOf` for actions that last until the end of the scene. */
export const UNTIL_SCENE_END = -1;

const r6 = (v: number) => roundTo(v, 6);

export function resolveTiming(scene: SceneT, durationOf: (a: ActionT, index: number) => number): TimingResult {
  const issues: ValidationIssue[] = [];
  const beatsById = new Map(scene.narration.beats.map((b, i) => [b.id, { b, i }]));
  const cuesById = new Map(scene.cues.map((c, i) => [c.id, { c, i }]));
  const cueForBeat = new Map<string, string>();
  for (const c of scene.cues) if (c.beat) cueForBeat.set(c.beat, c.id);

  const cueTimes = new Map<string, number>();
  const beatTimes = new Map<string, number>();
  const visiting = new Set<string>();

  const isAnchored = (id: string) => {
    const entry = beatsById.get(id);
    return !!entry && (entry.b.at !== undefined || cueForBeat.has(id));
  };

  function resolveRef(ref: number | string, where: string): number {
    if (typeof ref === "number") return ref;
    const [kind, id = ""] = ref.split(":") as [string, string];
    if (kind === "cue") {
      if (!cuesById.has(id)) {
        issues.push(
          issue("UNKNOWN_CUE", where, `Unknown cue '${id}'.`, { suggestion: didYouMean(id, [...cuesById.keys()]) }),
        );
        return 0;
      }
      return cueTime(id);
    }
    if (!beatsById.has(id)) {
      issues.push(
        issue("UNKNOWN_BEAT", where, `Unknown beat '${id}'.`, { suggestion: didYouMean(id, [...beatsById.keys()]) }),
      );
      return 0;
    }
    if (kind === "beat_end") return beatEnd(id);
    return beatStart(id);
  }

  function cueTime(id: string): number {
    const hit = cueTimes.get(id);
    if (hit !== undefined) return hit;
    const key = `cue:${id}`;
    const entry = cuesById.get(id);
    if (!entry) return 0;
    if (visiting.has(key)) {
      issues.push(issue("TIMING_CYCLE", `/cues/${entry.i}/at`, `Cue '${id}' depends on itself.`));
      return 0;
    }
    visiting.add(key);
    const t = r6(resolveRef(entry.c.at, `/cues/${entry.i}/at`));
    visiting.delete(key);
    cueTimes.set(id, t);
    return t;
  }

  function anchoredBeatTime(id: string): number {
    const hit = beatTimes.get(id);
    if (hit !== undefined) return hit;
    const entry = beatsById.get(id);
    if (!entry) return 0;
    const key = `beat:${id}`;
    if (visiting.has(key)) {
      issues.push(issue("TIMING_CYCLE", `/narration/beats/${entry.i}/at`, `Beat '${id}' depends on itself.`));
      return 0;
    }
    visiting.add(key);
    let t: number;
    if (entry.b.at !== undefined) t = resolveRef(entry.b.at, `/narration/beats/${entry.i}/at`);
    else t = cueTime(cueForBeat.get(id) as string);
    visiting.delete(key);
    t = r6(t);
    beatTimes.set(id, t);
    return t;
  }

  // Unanchored beats are spread evenly between their anchored neighbours.
  let distributed = false;
  function distribute() {
    if (distributed) return;
    distributed = true;
    const beats = scene.narration.beats;
    let i = 0;
    while (i < beats.length) {
      if (isAnchored((beats[i] as { id: string }).id)) {
        anchoredBeatTime((beats[i] as { id: string }).id);
        i++;
        continue;
      }
      let j = i;
      while (j < beats.length && !isAnchored((beats[j] as { id: string }).id)) j++;
      const prev = i > 0 ? anchoredBeatTime((beats[i - 1] as { id: string }).id) : null;
      const next = j < beats.length ? anchoredBeatTime((beats[j] as { id: string }).id) : scene.duration;
      const n = j - i;
      for (let k = 0; k < n; k++) {
        const t = prev === null ? (next * k) / n : prev + ((next - prev) * (k + 1)) / (n + 1);
        beatTimes.set((beats[i + k] as { id: string }).id, r6(t));
      }
      i = j;
    }
  }

  function beatStart(id: string): number {
    if (isAnchored(id)) return anchoredBeatTime(id);
    distribute();
    return beatTimes.get(id) ?? 0;
  }

  function beatEnd(id: string): number {
    const entry = beatsById.get(id);
    if (!entry) return 0;
    const next = scene.narration.beats[entry.i + 1];
    return next ? beatStart(next.id) : scene.duration;
  }

  for (const c of scene.cues) cueTime(c.id);
  distribute();
  const beats: ResolvedBeat[] = scene.narration.beats.map((b) => ({
    id: b.id,
    text: b.text,
    start: beatStart(b.id),
    end: beatEnd(b.id),
  }));
  beats.forEach((b, i) => {
    const prev = beats[i - 1];
    if (prev && b.start < prev.start) {
      issues.push(
        issue("BEAT_ORDER", `/narration/beats/${i}`, `Beat '${b.id}' starts before the previous beat.`, {
          suggestion: "Order beats by time, or fix their `at` references.",
        }),
      );
    }
  });

  // --- actions ---
  const actionIndex = new Map<string, number>();
  scene.timeline.forEach((a, i) => {
    if (a.id) actionIndex.set(a.id, i);
  });
  const times = new Map<string, ActionTime>();
  const actVisiting = new Set<string>();

  function actionTime(id: string): ActionTime {
    const hit = times.get(id);
    if (hit) return hit;
    const idx = actionIndex.get(id) as number;
    const a = scene.timeline[idx] as ActionT;
    const where = `/timeline/${idx}/start`;
    if (actVisiting.has(id)) {
      issues.push(
        issue("TIMING_CYCLE", where, `Action '${id}' depends on itself through after/with.`, {
          suggestion: "Break the cycle by giving one of the actions an absolute `at` time.",
        }),
      );
      return { start: 0, end: 0 };
    }
    actVisiting.add(id);
    const s = a.start ?? {};
    const modes = [s.at !== undefined, s.after !== undefined, s.with !== undefined].filter(Boolean).length;
    if (modes > 1) {
      issues.push(
        issue("START_AMBIGUOUS", where, "Use exactly one of at / after / with.", {
          suggestion: "Remove the extra fields.",
        }),
      );
    }
    let base = 0;
    const refAction = (ref: string, field: string) => {
      if (!actionIndex.has(ref)) {
        issues.push(
          issue("UNKNOWN_ACTION_REF", `${where}/${field}`, `No action with id '${ref}'.`, {
            suggestion: didYouMean(ref, [...actionIndex.keys()]),
          }),
        );
        return null;
      }
      return actionTime(ref);
    };
    if (s.after !== undefined) base = refAction(s.after, "after")?.end ?? 0;
    else if (s.with !== undefined) base = refAction(s.with, "with")?.start ?? 0;
    else if (s.at !== undefined) base = resolveRef(s.at, `${where}/at`);
    const start = r6(base + (s.delay ?? 0));
    const d = durationOf(a, idx);
    const end = r6(d === UNTIL_SCENE_END ? Math.max(start, scene.duration) : start + d);
    actVisiting.delete(id);
    const t = { start, end };
    times.set(id, t);
    return t;
  }

  scene.timeline.forEach((a, i) => {
    if (!a.id) return;
    const t = actionTime(a.id);
    if (t.start < 0) {
      issues.push(
        issue("NEGATIVE_START", `/timeline/${i}/start`, `Action '${a.id}' starts at ${t.start}s.`, {
          suggestion: "Reduce the negative delay.",
        }),
      );
    }
    if (t.end > scene.duration + 1e-6) {
      issues.push(
        issue(
          "ACTION_EXCEEDS_SCENE",
          `/timeline/${i}`,
          `Action '${a.id}' ends at ${roundTo(t.end, 3)}s but the scene lasts ${scene.duration}s.`,
          {
            suggestion: `Increase scene duration to at least ${Math.ceil(t.end * 10) / 10}s or shorten/start the action earlier.`,
          },
        ),
      );
    }
  });

  return {
    cues: Object.fromEntries(cueTimes),
    beats,
    actions: times,
    issues,
    at: (ref, where) => r6(resolveRef(ref, where)),
  };
}
