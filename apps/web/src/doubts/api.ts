/**
 * Doubts (ADR-050): the client of /api/doubts. An answer comes with its own scene (DSL, compiled here
 * by the same engine as the lesson), the picture it continues from (a lesson moment) and its voice.
 */
import type { SceneNarration } from "@animator/components";
import type { SceneState } from "@animator/engine";

export type DoubtKind = "lesson" | "related" | "unrelated" | "unclear";

export interface DoubtAnswer {
  session_id: string;
  n: number;
  question: string;
  kind: DoubtKind;
  depth: "quick" | "standard" | "deep";
  title: string;
  sentences: string[];
  suggestion: string | null;
  visual: { mode: "scene" | "board" | "none"; scene: number | null };
  scene: unknown | null;
  prev_state: SceneState | null;
  narration: SceneNarration | null;
  duration: number | null;
  cap_s: number;
  cost_usd: number;
  model: string | null;
  latency_s: number;
}

export interface Phrase {
  text: string;
  audio: string | null;
  duration?: number;
}

export type Phrases = Partial<Record<"greeting" | "thinking" | "followup" | "resume", Phrase[]>>;

export interface DoubtConfig {
  enabled: boolean;
  voice?: boolean;
  transcribe?: boolean;
  max_turns?: number;
}

export class DoubtApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...init });
  } catch {
    throw new DoubtApiError("The tutor is not reachable right now.", 0);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* not JSON */
    }
    throw new DoubtApiError(detail || `HTTP ${res.status}`, res.status);
  }
  return (await res.json()) as T;
}

export const doubtsApi = {
  config: () => call<DoubtConfig>("/api/doubts/config"),
  phrases: (language: string) => call<Phrases>(`/api/doubts/phrases?language=${encodeURIComponent(language)}`),
  ask: (lessonId: string, body: { question: string; scene_index: number; t: number; session_id?: string | null }) =>
    call<DoubtAnswer>(`/api/doubts/${lessonId}`, { method: "POST", body: JSON.stringify(body) }),
  transcribe: async (audio: Blob, language: string): Promise<string> => {
    const res = await call<{ text: string }>("/api/doubts/transcribe", {
      method: "POST",
      body: audio,
      headers: { "Content-Type": audio.type || "audio/webm", "X-Lesson-Language": language },
    });
    return res.text;
  },
};

export const audioUrl = (name: string) => `/api/tts/${encodeURIComponent(name)}`;
