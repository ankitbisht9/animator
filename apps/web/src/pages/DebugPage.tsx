/**
 * /debug/:id: the whole generation trace (spec Section 18): request, plan, each scene's brief,
 * attempts with engine issues and critic notes, final DSL (openable in the Playground), and every
 * LLM call with tokens, cost, latency and retries.
 */
import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { navigate } from "../router";

interface Attempt {
  n: number;
  kind: string;
  stage: string;
  model: string | null;
  ok: boolean;
  errors: { code: string; path: string; message: string; suggestion?: string | null }[];
  warnings: number;
  llm_error: string | null;
  critic: { verdict: string; issues: { severity: string; problem: string; fix: string }[] } | null;
  seconds: number;
}

interface SceneRec {
  index: number;
  id: string;
  title: string;
  status: string;
  duration: number | null;
  brief: Record<string, unknown>;
  dsl: unknown;
  attempts: Attempt[];
  warnings: { code: string; message: string }[];
  degraded_reason: string | null;
}

interface Full {
  id: string;
  status: string;
  error: string | null;
  request: Record<string, unknown>;
  plan: Record<string, unknown> | null;
  scenes: SceneRec[];
  metrics: Record<string, unknown>;
  models: Record<string, string[]>;
}

type Call = {
  ts: number;
  stage: string;
  label: string;
  model: string | null;
  status: string;
  usage: { input_tokens: number; cached_input_tokens: number; output_tokens: number; reasoning_tokens: number };
  cost_usd: number | null;
  latency_s: number;
  retries: number;
  error: string | null;
};

function Json({ value }: { value: unknown }) {
  return <pre className="json">{JSON.stringify(value, null, 2)}</pre>;
}

function openInPlayground(title: string, scenes: unknown[]) {
  try {
    localStorage.setItem("animator.playground", JSON.stringify({ title, scenes }, null, 2));
  } catch {
    /* storage unavailable */
  }
  navigate("/playground");
}

export function DebugPage({ id }: { id: string }) {
  const [gen, setGen] = useState<Full | null>(null);
  const [calls, setCalls] = useState<Call[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const load = () =>
      Promise.all([api.get(id), api.calls(id)]).then(
        ([g, c]) => {
          setGen(g as unknown as Full);
          setCalls(c as unknown as Call[]);
        },
        (e: unknown) => setError(e instanceof ApiError ? e.message : String(e)),
      );
    void load();
    const h = window.setInterval(load, 5000);
    return () => window.clearInterval(h);
  }, [id]);

  if (error) return <div className="page narrow">{error}</div>;
  if (!gen) return <div className="page narrow">Loading…</div>;
  const totalCost = calls.reduce((a, c) => a + (c.cost_usd ?? 0), 0);
  const dsls = gen.scenes.map((s) => s.dsl).filter(Boolean);

  return (
    <div className="page wide debug">
      <h1>Debug · {gen.id}</h1>
      <p className="lead">
        Status <b>{gen.status}</b>
        {gen.error && <> · {gen.error}</>} · <a href={`/g/${gen.id}`}>open the lesson</a>
      </p>

      <section>
        <h2>Request, models and metrics</h2>
        <div className="debug-grid">
          <Json value={gen.request} />
          <Json value={gen.models} />
          <Json value={gen.metrics} />
        </div>
      </section>

      <section>
        <h2>Educational plan</h2>
        {gen.plan ? (
          <details>
            <summary>{String(gen.plan.title)}</summary>
            <Json value={gen.plan} />
          </details>
        ) : (
          <p>Not planned yet.</p>
        )}
      </section>

      <section>
        <h2>Scenes ({gen.scenes.length})</h2>
        {gen.scenes.map((s) => (
          <details key={s.id} className={`debug-scene ${s.status}`}>
            <summary>
              <b>
                {s.index + 1}. {s.title}
              </b>{" "}
              · {s.status} · {s.attempts.length} attempt(s)
              {s.duration != null && <> · {s.duration.toFixed(1)} s</>}
              {s.degraded_reason && <> · {s.degraded_reason}</>}
            </summary>
            <h3>Storyboard brief</h3>
            <Json value={s.brief} />
            <h3>Attempts</h3>
            <table className="debug-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>kind</th>
                  <th>stage / model</th>
                  <th>ok</th>
                  <th>engine errors · critic · LLM error</th>
                  <th>s</th>
                </tr>
              </thead>
              <tbody>
                {s.attempts.map((a) => (
                  <tr key={a.n}>
                    <td>{a.n}</td>
                    <td>{a.kind}</td>
                    <td>
                      {a.stage}
                      <br />
                      <small>{a.model ?? "-"}</small>
                    </td>
                    <td>{a.ok ? "yes" : "no"}</td>
                    <td>
                      {a.errors.map((e) => (
                        <div key={`${e.code}${e.path}${e.message}`}>
                          <code>{e.code}</code> {e.path}: {e.message}
                        </div>
                      ))}
                      {a.warnings > 0 && <div className="muted">{a.warnings} warning(s)</div>}
                      {a.critic && (
                        <div>
                          critic: <b>{a.critic.verdict}</b>
                          {a.critic.issues.map((i) => (
                            <div key={i.problem}>
                              [{i.severity}] {i.problem} → {i.fix}
                            </div>
                          ))}
                        </div>
                      )}
                      {a.llm_error && <div className="bad">{a.llm_error}</div>}
                    </td>
                    <td>{a.seconds}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {s.warnings.length > 0 && (
              <>
                <h3>Engine warnings on the final version</h3>
                {s.warnings.map((w) => (
                  <div key={`${w.code}${w.message}`}>
                    <code>{w.code}</code> {w.message}
                  </div>
                ))}
              </>
            )}
            {s.dsl != null && (
              <>
                <h3>
                  Scene DSL{" "}
                  <button
                    type="button"
                    className="btn small"
                    onClick={() => openInPlayground(s.title, dsls.slice(0, s.index + 1))}
                  >
                    Open in Playground
                  </button>
                </h3>
                <Json value={s.dsl} />
              </>
            )}
          </details>
        ))}
      </section>

      <section>
        <h2>
          LLM calls ({calls.length}) · ${totalCost.toFixed(4)}
        </h2>
        <table className="debug-table">
          <thead>
            <tr>
              <th>time</th>
              <th>stage</th>
              <th>label</th>
              <th>model</th>
              <th>status</th>
              <th>in / cached / out / reasoning</th>
              <th>cost</th>
              <th>latency</th>
              <th>retries</th>
            </tr>
          </thead>
          <tbody>
            {calls.map((c) => (
              <tr key={`${c.ts}${c.label}`} className={c.status === "ok" ? "" : "bad"}>
                <td>{new Date(c.ts * 1000).toLocaleTimeString()}</td>
                <td>{c.stage}</td>
                <td>{c.label}</td>
                <td>{c.model ?? "-"}</td>
                <td title={c.error ?? ""}>{c.status}</td>
                <td>
                  {c.usage.input_tokens} / {c.usage.cached_input_tokens} / {c.usage.output_tokens} /{" "}
                  {c.usage.reasoning_tokens}
                </td>
                <td>{c.cost_usd == null ? "?" : `$${c.cost_usd.toFixed(4)}`}</td>
                <td>{c.latency_s.toFixed(1)} s</td>
                <td>{c.retries}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
