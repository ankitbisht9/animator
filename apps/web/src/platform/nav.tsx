/** Navigation on top of the tiny router (../router): the location with its query, route patterns
 * with parameters, links that know when they are active, and redirects. No dependency. */
import { type AnchorHTMLAttributes, useEffect, useState } from "react";

export interface Location {
  path: string;
  search: URLSearchParams;
  hash: string;
}

function current(): Location {
  return {
    path: window.location.pathname,
    search: new URLSearchParams(window.location.search),
    hash: window.location.hash,
  };
}

export function navigate(to: string, opts: { replace?: boolean; keepScroll?: boolean } = {}): void {
  const here = window.location.pathname + window.location.search;
  if (to === here && !opts.replace) return;
  if (opts.replace) window.history.replaceState({}, "", to);
  else window.history.pushState({}, "", to);
  window.dispatchEvent(new PopStateEvent("popstate"));
  if (!opts.keepScroll) window.scrollTo({ top: 0 });
}

export function useLocation(): Location {
  const [loc, setLoc] = useState(current);
  useEffect(() => {
    const on = () => setLoc(current());
    window.addEventListener("popstate", on);
    return () => window.removeEventListener("popstate", on);
  }, []);
  return loc;
}

/** "/lessons/:id" against "/lessons/g_123": { id: "g_123" }; null when it does not match. */
export function match(pattern: string, path: string): Record<string, string> | null {
  const p = pattern.split("/").filter(Boolean);
  const a = path.replace(/\/+$/, "").split("/").filter(Boolean);
  const rest = p.at(-1) === "*";
  if (rest ? a.length < p.length - 1 : a.length !== p.length) return null;
  const out: Record<string, string> = {};
  for (let i = 0; i < p.length; i++) {
    const seg = p[i] ?? "";
    if (seg === "*") break;
    const val = a[i];
    if (val === undefined) return null;
    if (seg.startsWith(":")) out[seg.slice(1)] = decodeURIComponent(val);
    else if (seg !== val) return null;
  }
  return out;
}

export function Redirect({ to }: { to: string }) {
  useEffect(() => navigate(to, { replace: true }), [to]);
  return null;
}

type LinkProps = AnchorHTMLAttributes<HTMLAnchorElement> & {
  href: string;
  /** Active when the path starts with href (or equals it, with exact). */
  exact?: boolean;
  activeClassName?: string;
};

export function Link({ href, exact, activeClassName, className, onClick, children, ...rest }: LinkProps) {
  const { path } = useLocation();
  const target = href.split("?")[0] ?? href;
  const active = activeClassName && (exact ? path === target : path === target || path.startsWith(`${target}/`));
  return (
    <a
      href={href}
      className={[className, active ? activeClassName : ""].filter(Boolean).join(" ") || undefined}
      aria-current={active ? "page" : undefined}
      {...rest}
      onClick={(e) => {
        onClick?.(e);
        if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
        if (rest.target === "_blank" || /^https?:|^mailto:/.test(href)) return;
        e.preventDefault();
        navigate(href);
      }}
    >
      {children}
    </a>
  );
}
