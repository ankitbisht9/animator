/**
 * DSL Playground: paste or edit scene JSON (one scene, or { scenes: [...] }),
 * see validation issues immediately, and play/scrub the result. The main
 * debugging tool: it runs exactly the engine code the worker and exporter run.
 */
import { json } from "@codemirror/lang-json";
import { basicSetup, EditorView } from "codemirror";
import { useEffect, useMemo, useRef, useState } from "react";
import { FIXTURE_LESSONS } from "../lesson/fixtures";
import { buildLesson, type Lesson } from "../lesson/lesson";
import { Player } from "../player/Player";

const STORAGE_KEY = "animator.playground";

const STARTER = {
  dsl_version: "1.0",
  id: "hello",
  title: "Hello, engine",
  duration: 6,
  narration: {
    beats: [
      { id: "b1", text: "A shape **pops** in." },
      { id: "b2", text: "Then it moves and gets a label." },
    ],
  },
  objects: [
    {
      id: "title",
      type: "text",
      params: { text: "Hello, **engine**", size: "title", weight: "bold" },
      place: { slot: "title_bar" },
    },
    { id: "ball", type: "circle", params: { radius: 60 }, place: { slot: "stage.left" }, style: { fill: "object_a" } },
  ],
  timeline: [
    { action: "write", target: "title" },
    { action: "create", target: "ball", start: { at: 0.4 } },
    { action: "move", target: "ball", to: { slot: "stage.right" }, path: "arc", start: { at: "beat:b2" } },
    { action: "label", target: "ball", text: "a ball", side: "below" },
  ],
};

function parse(text: string): { lesson: Lesson | null; error: string | null } {
  try {
    const data = JSON.parse(text) as { scenes?: unknown[]; title?: string };
    const scenes = Array.isArray(data.scenes) ? data.scenes : [data];
    return { lesson: buildLesson(data.title ?? "Playground", scenes), error: null };
  } catch (e) {
    return { lesson: null, error: (e as Error).message };
  }
}

function initialText(): string {
  try {
    return localStorage.getItem(STORAGE_KEY) ?? JSON.stringify(STARTER, null, 2);
  } catch {
    return JSON.stringify(STARTER, null, 2);
  }
}

export function Playground() {
  const [text, setText] = useState(initialText);
  const [debounced, setDebounced] = useState(text);
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);

  // biome-ignore lint/correctness/useExhaustiveDependencies: the editor owns the document after mount
  useEffect(() => {
    if (!host.current) return;
    view.current = new EditorView({
      doc: text,
      extensions: [
        basicSetup,
        json(),
        EditorView.updateListener.of((u) => u.docChanged && setText(u.state.doc.toString())),
      ],
      parent: host.current,
    });
    return () => view.current?.destroy();
  }, []);

  useEffect(() => {
    const h = setTimeout(() => setDebounced(text), 350);
    try {
      localStorage.setItem(STORAGE_KEY, text);
    } catch {
      // storage unavailable: fine
    }
    return () => clearTimeout(h);
  }, [text]);

  const { lesson, error } = useMemo(() => parse(debounced), [debounced]);
  const issues = lesson?.scenes.flatMap((s) => s.issues.map((i) => ({ scene: s.id, ...i }))) ?? [];

  const load = (value: string) => {
    view.current?.dispatch({ changes: { from: 0, to: view.current.state.doc.length, insert: value } });
  };

  return (
    <div className="playground">
      <div className="pg-editor">
        <div className="pg-toolbar">
          <select
            aria-label="Load example"
            defaultValue=""
            onChange={(e) => {
              const v = e.target.value;
              if (v === "starter") load(JSON.stringify(STARTER, null, 2));
              const f = FIXTURE_LESSONS.find((l) => l.slug === v);
              if (f) load(JSON.stringify({ title: f.meta.title, scenes: f.scenes }, null, 2));
              e.target.value = "";
            }}
          >
            <option value="" disabled>
              Load example…
            </option>
            <option value="starter">Starter scene</option>
            {FIXTURE_LESSONS.map((l) => (
              <option key={l.slug} value={l.slug}>
                {l.meta.title}
              </option>
            ))}
          </select>
          <span className="pg-hint">One scene, or {'{ "scenes": [...] }'}</span>
        </div>
        <div className="pg-code" ref={host} />
      </div>
      <div className="pg-preview">
        {error && <div className="pg-error">JSON error: {error}</div>}
        {lesson && <Player key={debounced} lesson={lesson} />}
        <div className="pg-issues">
          <div className="pg-issues-title">
            Validation · {issues.filter((i) => i.severity === "error").length} errors ·{" "}
            {issues.filter((i) => i.severity === "warning").length} warnings
          </div>
          {issues.length === 0 && <div className="pg-ok">No issues.</div>}
          {issues.map((i) => (
            <div key={`${i.scene}${i.path}${i.code}${i.message}`} className={`pg-issue ${i.severity}`}>
              <code>{i.code}</code>{" "}
              <span className="pg-path">
                {i.scene}
                {i.path}
              </span>
              <div>{i.message}</div>
              {i.suggestion && <div className="pg-sugg">{i.suggestion}</div>}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
