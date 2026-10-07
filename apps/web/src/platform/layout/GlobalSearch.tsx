/** Search across the learner's lessons and Explore from anywhere (Ctrl+K or /), with the keyboard. */
import { Compass, CornerDownLeft, Search } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { type Lesson, v1 } from "../api";
import { useDebounced, useDismiss } from "../hooks";
import { navigate } from "../nav";
import { subject } from "../ui/brand";

interface Result {
  lesson: Lesson;
  group: "Your lessons" | "Explore";
}

export function GlobalSearch() {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [results, setResults] = useState<Result[]>([]);
  const [busy, setBusy] = useState(false);
  const [cursor, setCursor] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const term = useDebounced(q.trim(), 220);
  const close = useCallback(() => setOpen(false), []);
  useDismiss(wrap, open, close);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = (e.target as HTMLElement)?.closest("input, textarea, select, [contenteditable]");
      if ((e.key === "k" && (e.ctrlKey || e.metaKey)) || (e.key === "/" && !typing)) {
        e.preventDefault();
        input.current?.focus();
        setOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (term.length < 2) {
      setResults([]);
      return;
    }
    let live = true;
    setBusy(true);
    Promise.all([
      v1.myLessons({ q: term, limit: 5 }).catch(() => ({ items: [] as Lesson[] })),
      v1.explore({ q: term, limit: 5 }).catch(() => ({ items: [] as Lesson[] })),
    ])
      .then(([mine, pub]) => {
        if (!live) return;
        const seen = new Set(mine.items.map((l) => l.id));
        setResults([
          ...mine.items.map((lesson) => ({ lesson, group: "Your lessons" as const })),
          ...pub.items.filter((l) => !seen.has(l.id)).map((lesson) => ({ lesson, group: "Explore" as const })),
        ]);
        setCursor(0);
      })
      .finally(() => live && setBusy(false));
    return () => {
      live = false;
    };
  }, [term]);

  const groups = useMemo(() => {
    const g = new Map<string, Result[]>();
    for (const r of results) g.set(r.group, [...(g.get(r.group) ?? []), r]);
    return [...g.entries()];
  }, [results]);

  const choose = (r: Result | undefined) => {
    if (!r) return;
    setOpen(false);
    setQ("");
    input.current?.blur();
    navigate(`/lessons/${r.lesson.id}`);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setCursor((c) => Math.min(results.length - 1, c + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setCursor((c) => Math.max(0, c - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (results.length) choose(results[cursor]);
      else if (term) {
        setOpen(false);
        navigate(`/explore?q=${encodeURIComponent(term)}`);
      }
    } else if (e.key === "Escape") {
      setOpen(false);
      input.current?.blur();
    }
  };

  let index = -1;
  return (
    <div className="gsearch" ref={wrap}>
      <Search size={17} className="gsearch-icon" aria-hidden="true" />
      <input
        ref={input}
        type="search"
        value={q}
        placeholder="Search lessons…"
        aria-label="Search lessons"
        aria-expanded={open && term.length >= 2}
        aria-controls="gsearch-results"
        role="combobox"
        aria-autocomplete="list"
        onFocus={() => setOpen(true)}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
        }}
        onKeyDown={onKeyDown}
      />
      <kbd className="gsearch-kbd">Ctrl K</kbd>
      {open && term.length >= 2 && (
        <div className="gsearch-panel" id="gsearch-results" role="listbox">
          {busy && results.length === 0 && <div className="gsearch-empty">Searching…</div>}
          {!busy && results.length === 0 && (
            <button
              type="button"
              className="gsearch-explore"
              onClick={() => navigate(`/explore?q=${encodeURIComponent(term)}`)}
            >
              <Compass size={16} /> Search Explore for “{term}”
            </button>
          )}
          {groups.map(([group, items]) => (
            <div key={group} className="gsearch-group">
              <div className="gsearch-group-title">{group}</div>
              {items.map((r) => {
                index++;
                const i = index;
                const s = subject(r.lesson.subject);
                return (
                  <button
                    type="button"
                    role="option"
                    aria-selected={i === cursor}
                    key={r.lesson.id}
                    className={`gsearch-item ${i === cursor ? "on" : ""}`}
                    onMouseEnter={() => setCursor(i)}
                    onClick={() => choose(r)}
                  >
                    <span className="gsearch-item-icon" style={{ background: s.tint, color: s.ink }}>
                      <s.icon size={15} />
                    </span>
                    <span className="gsearch-item-text">
                      <b>{r.lesson.title}</b>
                      <small>
                        {s.label}
                        {r.lesson.owner && r.group === "Explore" ? ` · by ${r.lesson.owner.name}` : ""}
                      </small>
                    </span>
                    {i === cursor && <CornerDownLeft size={14} className="gsearch-enter" />}
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
