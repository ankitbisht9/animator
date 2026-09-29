/**
 * Client for the Python API (proxied at /api by Vite in dev). Scene DSL arrives as data and is
 * validated and compiled in the browser by the same engine the worker runs (P2, P4).
 */

export type GenerationStatus = "queued" | "planning" | "generating" | "complete" | "failed" | "cancelled" | "paused";
export const TERMINAL: GenerationStatus[] = ["complete", "failed", "cancelled"];
export type SceneStatus = "planned" | "generating" | "validating" | "correcting" | "ready" | "degraded" | "failed";

export interface SceneSummary {
  index: number;
  id: string;
  title: string;
  chapter_id: string;
  status: SceneStatus;
  duration: number;
  attempts: number;
  degraded_reason: string | null;
}

export interface GenerationSummary {
  id: string;
  created_at: number;
  updated_at: number;
  request: {
    topic: string;
    audience: string;
    duration_min: number;
    style: string | null;
    language: string;
    paced: boolean;
  };
  status: GenerationStatus;
  error: string | null;
  title: string | null;
  subtitle: string | null;
  planned_duration_s: number;
  chapters: { id: string; title: string; storyboarded: boolean }[];
  scenes: SceneSummary[];
  metrics: {
    llm_calls: number;
    cost_usd: number;
    input_tokens: number;
    cached_input_tokens: number;
    output_tokens: number;
    reasoning_tokens: number;
    corrections: number;
    degraded: number;
    first_play_s: number | null;
    generation_s: number | null;
    slow_scenes: string[];
  };
  version: number;
}

export interface PlayableScene {
  index: number;
  id: string;
  status: SceneStatus;
  duration: number | null;
  dsl: unknown;
}

/** `final`: the last event of the stream (the run is over and every cost is counted). */
export type GenerationEvent =
  | { type: "snapshot"; generation: GenerationSummary; scenes: PlayableScene[]; final?: boolean }
  | { type: "update"; generation: GenerationSummary; final?: boolean }
  | ({ type: "scene" } & PlayableScene);

export interface ApiConfig {
  openai_key_configured: boolean;
  stages: Record<string, { models: string[]; effort: string }>;
  budget_usd_per_generation: number;
  max_correction_attempts: number;
  llm_critic: boolean;
  visual_critic: boolean;
  storage: string;
}

export class ApiError extends Error {
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
    throw new ApiError("The API server is not reachable. Start it with `make dev`.", 0);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) detail = body.detail.map((d: { msg?: string }) => d.msg).join("; ");
    } catch {
      /* not JSON */
    }
    throw new ApiError(detail || `HTTP ${res.status}`, res.status);
  }
  return (res.status === 204 ? undefined : await res.json()) as T;
}

export const api = {
  config: () => call<ApiConfig>("/api/config"),
  list: () => call<GenerationSummary[]>("/api/generations"),
  get: (id: string) => call<Record<string, unknown> & { summary: GenerationSummary }>(`/api/generations/${id}`),
  create: (body: { topic: string; audience?: string; duration_min?: number; style?: string | null; paced?: boolean }) =>
    call<GenerationSummary>("/api/generations", { method: "POST", body: JSON.stringify(body) }),
  cancel: (id: string) => call<GenerationSummary>(`/api/generations/${id}/cancel`, { method: "POST" }),
  resume: (id: string) => call<GenerationSummary>(`/api/generations/${id}/resume`, { method: "POST" }),
  playhead: (id: string, t: number) =>
    call<void>(`/api/generations/${id}/playhead`, { method: "POST", body: JSON.stringify({ t }) }),
  calls: (id: string) => call<Record<string, unknown>[]>(`/api/generations/${id}/calls`),
  startExport: (id: string) => call<ExportJob>(`/api/generations/${id}/export`, { method: "POST", body: "{}" }),
  exportStatus: (id: string) => call<ExportJob>(`/api/generations/${id}/export`),
};

export interface ExportJob {
  generation_id: string;
  state: "running" | "done" | "failed";
  log: string[];
  video: string | null;
  error: string | null;
}

/** Live progress. Reconnects on network errors; resolves nothing, call the returned function to stop. */
export function subscribe(id: string, onEvent: (e: GenerationEvent) => void, onError?: () => void): () => void {
  const source = new EventSource(`/api/generations/${id}/events`);
  source.onmessage = (m) => {
    let event: GenerationEvent;
    try {
      event = JSON.parse(m.data) as GenerationEvent;
    } catch {
      return; // ignore malformed frames
    }
    onEvent(event);
    // The server ends the stream after its final event (every cost counted, including drafts that
    // finish after the lesson is ready); do not let EventSource reconnect.
    if (event.type !== "scene" && event.final) source.close();
  };
  source.onerror = () => onError?.();
  return () => source.close();
}
