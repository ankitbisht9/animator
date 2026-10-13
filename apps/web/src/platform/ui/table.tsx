/**
 * Lists as tables (ADR-057): choosing rows (shift-click chooses a range), the bar of actions on what is
 * chosen, loading rows that keep the table's shape, and the pager. Shared by the admin lists, so every
 * list selects, waits and pages the same way.
 */
import { ChevronLeft, ChevronRight, X } from "lucide-react";
import { type ChangeEvent, type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button } from "./core";

export interface Selection {
  /** The chosen ids, in the order they are shown. */
  ids: string[];
  count: number;
  has: (id: string) => boolean;
  /** Choose or unchoose one; with `range`, everything from the last one clicked to it, the same way. */
  toggle: (id: string, range?: boolean) => void;
  toggleAll: () => void;
  clear: () => void;
  all: boolean;
  some: boolean;
}

/** Rows chosen among `ids` (what is shown). When what is shown changes, only what is still shown
 * stays chosen: another page, other filters or a reload never act on rows out of sight. */
export function useSelection(ids: string[]): Selection {
  const [chosen, setChosen] = useState<ReadonlySet<string>>(() => new Set());
  const anchor = useRef<string | null>(null);
  const key = ids.join("\n");
  // biome-ignore lint/correctness/useExhaustiveDependencies: `key` stands for `ids`
  useEffect(() => {
    setChosen((prev) => {
      const shown = new Set(ids);
      const kept = [...prev].filter((id) => shown.has(id));
      return kept.length === prev.size ? prev : new Set(kept);
    });
    if (anchor.current && !ids.includes(anchor.current)) anchor.current = null;
  }, [key]);

  const toggle = useCallback(
    (id: string, range = false) => {
      // Read before the update: React may run the updater later, after the anchor has moved.
      const from = range && anchor.current ? ids.indexOf(anchor.current) : -1;
      anchor.current = id;
      setChosen((prev) => {
        const next = new Set(prev);
        const on = !prev.has(id);
        const to = ids.indexOf(id);
        if (range && from >= 0 && to >= 0) {
          const [a, b] = from < to ? [from, to] : [to, from];
          for (const x of ids.slice(a, b + 1)) {
            if (on) next.add(x);
            else next.delete(x);
          }
        } else if (on) next.add(id);
        else next.delete(id);
        return next;
      });
    },
    [ids],
  );

  const all = ids.length > 0 && ids.every((id) => chosen.has(id));
  const toggleAll = useCallback(() => {
    setChosen((prev) => (ids.length > 0 && ids.every((id) => prev.has(id)) ? new Set() : new Set(ids)));
    anchor.current = null;
  }, [ids]);
  const clear = useCallback(() => {
    setChosen(new Set());
    anchor.current = null;
  }, []);

  return useMemo(() => {
    const list = ids.filter((id) => chosen.has(id));
    return {
      ids: list,
      count: list.length,
      has: (id: string) => chosen.has(id),
      toggle,
      toggleAll,
      clear,
      all,
      some: list.length > 0 && !all,
    };
  }, [ids, chosen, toggle, toggleAll, clear, all]);
}

/** Whether the click that changed a checkbox held Shift (to choose a range). */
function shiftHeld(e: ChangeEvent<HTMLInputElement>): boolean {
  const native = e.nativeEvent as Event & { shiftKey?: boolean };
  return Boolean(native.shiftKey);
}

/** A row's checkbox. */
export function RowCheck({ selection, id, label }: { selection: Selection; id: string; label: string }) {
  return (
    <input
      type="checkbox"
      className="row-check"
      aria-label={label}
      checked={selection.has(id)}
      onChange={(e) => selection.toggle(id, shiftHeld(e))}
    />
  );
}

/** The header's checkbox: every row shown, or none (partly checked when some are). With `text`, a
 * label beside it that can be clicked too. */
export function AllCheck({
  selection,
  label = "Choose every row shown",
  text,
}: {
  selection: Selection;
  label?: string;
  text?: ReactNode;
}) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = selection.some;
  }, [selection.some]);
  if (text === undefined)
    return (
      <input
        ref={ref}
        type="checkbox"
        className="row-check"
        aria-label={label}
        checked={selection.all}
        onChange={selection.toggleAll}
      />
    );
  return (
    <label className="check-label">
      <input ref={ref} type="checkbox" className="row-check" checked={selection.all} onChange={selection.toggleAll} />
      <span>{text}</span>
    </label>
  );
}

/** What can be done with the chosen rows: a bar that floats over the list while any are chosen. */
export function BulkBar({
  count,
  noun,
  onClear,
  children,
}: {
  count: number;
  noun: [string, string];
  onClear: () => void;
  children: ReactNode;
}) {
  if (count === 0) return null;
  return (
    <section className="bulk-bar" aria-label="Actions on the chosen rows">
      <span className="bulk-count">
        <b>{count}</b> {count === 1 ? noun[0] : noun[1]} chosen
      </span>
      <span className="bulk-sep" aria-hidden="true" />
      <div className="bulk-actions">{children}</div>
      <button type="button" className="bulk-clear" onClick={onClear} aria-label="Clear the choice" title="Clear">
        <X size={16} />
      </button>
    </section>
  );
}

/** Rows that keep the table's shape while it loads. */
export function SkeletonRows({ rows = 8, cols }: { rows?: number; cols: number }) {
  const widths = [72, 54, 88, 40, 64, 48];
  return (
    <>
      {Array.from({ length: rows }, (_, r) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: placeholder rows never reorder
        <tr key={r} className="skeleton-row">
          {Array.from({ length: cols }, (_, c) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: placeholder cells never reorder
            <td key={c}>
              <span className="ui-skeleton" style={{ width: `${widths[(r + c) % widths.length]}%` }} />
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}

/** Pages of a list: which rows are shown, of how many, and the way to the others. */
export function Pager({
  offset,
  size,
  total,
  onChange,
}: {
  offset: number;
  size: number;
  total: number;
  onChange: (o: number) => void;
}) {
  if (total <= size) return null;
  const page = Math.floor(offset / size) + 1;
  const pages = Math.ceil(total / size);
  const first = Math.min(total, offset + 1);
  const last = Math.min(total, offset + size);
  return (
    <nav className="pager" aria-label="Pages">
      <span className="pager-range">
        <b>
          {first.toLocaleString()}–{last.toLocaleString()}
        </b>{" "}
        of {total.toLocaleString()}
      </span>
      <span className="pager-steps">
        <Button
          size="sm"
          variant="secondary"
          icon={ChevronLeft}
          disabled={page <= 1}
          onClick={() => onChange(Math.max(0, offset - size))}
          aria-label="Previous page"
        />
        <span className="pager-page">
          Page {page} of {pages}
        </span>
        <Button
          size="sm"
          variant="secondary"
          icon={ChevronRight}
          disabled={page >= pages}
          onClick={() => onChange(offset + size)}
          aria-label="Next page"
        />
      </span>
    </nav>
  );
}
