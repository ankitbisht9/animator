/** Small hooks: data with a shared cache (stale-while-revalidate), debouncing, titles, intervals. */
import { useCallback, useEffect, useRef, useState } from "react";

interface Entry {
  data: unknown;
  at: number;
}

const cache = new Map<string, Entry>();
const inflight = new Map<string, Promise<unknown>>();
const listeners = new Map<string, Set<() => void>>();

/** Forget cached data (all keys starting with the prefix) and refetch what is on screen. */
export function invalidate(prefix: string): void {
  for (const key of [...cache.keys()]) if (key.startsWith(prefix)) cache.delete(key);
  for (const [key, set] of listeners) if (key.startsWith(prefix)) for (const fn of set) fn();
}

export function clearCache(): void {
  cache.clear();
}

export interface Resource<T> {
  data: T | undefined;
  error: Error | null;
  loading: boolean;
  reload: () => Promise<void>;
  /** Change the cached value locally (optimistic updates). */
  mutate: (fn: (old: T | undefined) => T | undefined) => void;
}

/**
 * Fetch `key` with `load`. Cached results show at once (and are refreshed in the background when
 * older than `maxAge` ms); the same key loading twice shares one request. key null: nothing to load.
 */
export function useResource<T>(key: string | null, load: () => Promise<T>, maxAge = 30_000): Resource<T> {
  const hit = key ? (cache.get(key) as Entry | undefined) : undefined;
  const [data, setData] = useState<T | undefined>(hit?.data as T | undefined);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(!!key && !hit);
  const loadRef = useRef(load);
  loadRef.current = load;
  const keyRef = useRef(key);
  keyRef.current = key;

  const run = useCallback(
    async (k: string, force: boolean) => {
      const entry = cache.get(k);
      if (entry && !force && Date.now() - entry.at < maxAge) {
        setData(entry.data as T);
        setLoading(false);
        return;
      }
      if (!entry) setLoading(true);
      let p = inflight.get(k);
      if (!p || force) {
        p = loadRef.current();
        inflight.set(k, p);
      }
      try {
        const value = await p;
        cache.set(k, { data: value, at: Date.now() });
        if (keyRef.current === k) {
          setData(value as T);
          setError(null);
        }
      } catch (e) {
        if (keyRef.current === k) setError(e instanceof Error ? e : new Error(String(e)));
      } finally {
        if (inflight.get(k) === p) inflight.delete(k);
        if (keyRef.current === k) setLoading(false);
      }
    },
    [maxAge],
  );

  useEffect(() => {
    if (!key) {
      setData(undefined);
      setLoading(false);
      return;
    }
    const entry = cache.get(key);
    setData(entry?.data as T | undefined);
    setError(null);
    void run(key, false);
    const set = listeners.get(key) ?? new Set();
    const refetch = () => void run(key, true);
    set.add(refetch);
    listeners.set(key, set);
    return () => {
      set.delete(refetch);
    };
  }, [key, run]);

  return {
    data,
    error,
    loading,
    reload: useCallback(async () => {
      if (keyRef.current) await run(keyRef.current, true);
    }, [run]),
    mutate: useCallback((fn) => {
      setData((old) => {
        const next = fn(old);
        if (keyRef.current) cache.set(keyRef.current, { data: next, at: Date.now() });
        return next;
      });
    }, []),
  };
}

export function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const h = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(h);
  }, [value, ms]);
  return v;
}

export function useTitle(title: string): void {
  useEffect(() => {
    document.title = title ? `${title} · Animator` : "Animator";
  }, [title]);
}

export function useInterval(fn: () => void, ms: number | null): void {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    if (ms === null) return;
    const h = window.setInterval(() => ref.current(), ms);
    return () => window.clearInterval(h);
  }, [ms]);
}

export function useMedia(query: string): boolean {
  const [on, setOn] = useState(() => window.matchMedia?.(query).matches ?? false);
  useEffect(() => {
    const m = window.matchMedia?.(query);
    if (!m) return;
    const fn = () => setOn(m.matches);
    m.addEventListener("change", fn);
    return () => m.removeEventListener("change", fn);
  }, [query]);
  return on;
}

/** Close on a click outside `ref` or Escape. */
export function useDismiss(ref: React.RefObject<HTMLElement | null>, open: boolean, close: () => void): void {
  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [ref, open, close]);
}
