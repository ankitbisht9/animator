/**
 * The front page's motion (ADR-062): parts that rise into view as they are reached, numbers that count
 * up once seen, a showcase that tilts flat as it comes up, cards lit where the pointer is, and a slow
 * network of points behind the hero. Each waits until it is seen, stops off screen, and stays still for
 * visitors who prefer reduced motion (the device's setting or the app's own). Only classes are toggled
 * (never attribute selectors), so the live lesson in the hero pays nothing for any of it.
 */
import { type RefObject, useEffect, useRef, useState } from "react";

export function prefersStill(): boolean {
  return (
    document.documentElement.dataset.motion === "reduced" ||
    (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false)
  );
}

/** Adds `is-in` to each `.lp-reveal` under `root` as it comes into view (once each). */
export function useReveal(root: RefObject<HTMLElement | null>): void {
  useEffect(() => {
    const el = root.current;
    if (!el) return;
    const items = Array.from(el.querySelectorAll<HTMLElement>(".lp-reveal"));
    if (prefersStill() || typeof IntersectionObserver === "undefined") {
      for (const item of items) item.classList.add("is-in");
      return;
    }
    const obs = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (!e.isIntersecting) continue;
          e.target.classList.add("is-in");
          obs.unobserve(e.target);
        }
      },
      { rootMargin: "0px 0px -6% 0px", threshold: 0.12 },
    );
    for (const item of items) obs.observe(item);
    return () => obs.disconnect();
  }, [root]);
}

/** A number that counts up to `to` the first time it is seen (the final value for screen readers). */
export function CountUp({
  to,
  duration = 1400,
  format = (v: number) => String(Math.round(v)),
}: {
  to: number;
  duration?: number;
  format?: (v: number) => string;
}) {
  const ref = useRef<HTMLSpanElement>(null);
  const [value, setValue] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el || prefersStill() || typeof IntersectionObserver === "undefined") {
      setValue(to);
      return;
    }
    let raf = 0;
    const obs = new IntersectionObserver(
      ([e]) => {
        if (!e?.isIntersecting) return;
        obs.disconnect();
        const start = performance.now();
        const tick = (now: number) => {
          const u = Math.min(1, (now - start) / duration);
          setValue(to * (1 - (1 - u) ** 3));
          if (u < 1) raf = requestAnimationFrame(tick);
        };
        raf = requestAnimationFrame(tick);
      },
      { threshold: 0.5 },
    );
    obs.observe(el);
    return () => {
      obs.disconnect();
      cancelAnimationFrame(raf);
    };
  }, [to, duration]);
  return (
    <>
      <span ref={ref} className="lp-count" aria-hidden="true">
        {format(value)}
      </span>
      <span className="sr-only">{format(to)}</span>
    </>
  );
}

/** Writes `--tilt` on `ref`: 1 while it sits low in the window, 0 once it has risen to its upper part. */
export function useScrollTilt(ref: RefObject<HTMLElement | null>): void {
  useEffect(() => {
    const el = ref.current;
    if (!el || prefersStill()) return;
    let frame = 0;
    const paint = () => {
      frame = 0;
      const r = el.getBoundingClientRect();
      const vh = window.innerHeight || 1;
      const u = Math.min(1, Math.max(0, (r.top - vh * 0.2) / (vh * 0.55)));
      el.style.setProperty("--tilt", u.toFixed(3));
    };
    const on = () => {
      if (!frame) frame = requestAnimationFrame(paint);
    };
    paint();
    window.addEventListener("scroll", on, { passive: true });
    window.addEventListener("resize", on);
    return () => {
      window.removeEventListener("scroll", on);
      window.removeEventListener("resize", on);
      if (frame) cancelAnimationFrame(frame);
    };
  }, [ref]);
}

/** Lights each `.lp-spot` card under `root` around the pointer (`--mx`, `--my`; fine pointers only). */
export function useSpotlight(root: RefObject<HTMLElement | null>): void {
  useEffect(() => {
    const el = root.current;
    if (!el || prefersStill() || !(window.matchMedia?.("(pointer: fine)").matches ?? false)) return;
    let frame = 0;
    let target: HTMLElement | null = null;
    let x = 0;
    let y = 0;
    const paint = () => {
      frame = 0;
      if (!target) return;
      const r = target.getBoundingClientRect();
      target.style.setProperty("--mx", `${Math.round(x - r.left)}px`);
      target.style.setProperty("--my", `${Math.round(y - r.top)}px`);
    };
    const move = (e: PointerEvent) => {
      target = (e.target as HTMLElement | null)?.closest<HTMLElement>(".lp-spot") ?? null;
      x = e.clientX;
      y = e.clientY;
      if (target && !frame) frame = requestAnimationFrame(paint);
    };
    el.addEventListener("pointermove", move, { passive: true });
    return () => {
      el.removeEventListener("pointermove", move);
      if (frame) cancelAnimationFrame(frame);
    };
  }, [root]);
}

type Point = { x: number; y: number; vx: number; vy: number; r: number; lit: boolean };

function rgb(hex: string, fallback: [number, number, number]): [number, number, number] {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m?.[1]) return fallback;
  const n = Number.parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/**
 * A slow network behind the hero: points drift, near ones are joined by faint lines, and the pointer
 * draws lines to the points around it. About one point per 22,000 px² (at most 64), redrawn at most 30
 * times a second, only while on screen and the tab is visible; one still frame for reduced motion.
 */
export function Constellation({ className }: { className?: string }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    const still = prefersStill();
    const fine = window.matchMedia?.("(pointer: fine)").matches ?? false;
    const dpr = Math.min(1.5, window.devicePixelRatio || 1);
    const css = getComputedStyle(document.documentElement);
    const dark = document.documentElement.dataset.theme === "dark";
    const ink = rgb(css.getPropertyValue("--ink"), dark ? [245, 246, 249] : [16, 18, 26]);
    const brand = rgb(css.getPropertyValue("--brand"), [217, 4, 41]);
    const link = 132;
    let w = 0;
    let h = 0;
    let pts: Point[] = [];
    let raf = 0;
    let last = 0;
    let seen = true;
    const pointer = { x: -9999, y: -9999 };

    const layout = () => {
      const r = canvas.getBoundingClientRect();
      w = Math.max(1, r.width);
      h = Math.max(1, r.height);
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const n = Math.round(Math.min(64, Math.max(18, (w * h) / 22000)));
      pts = Array.from({ length: n }, (_, i) => ({
        x: Math.random() * w,
        y: Math.random() * h,
        vx: (Math.random() - 0.5) * 0.22,
        vy: (Math.random() - 0.5) * 0.22,
        r: 1.1 + Math.random() * 1.5,
        lit: i % 7 === 0,
      }));
    };

    const draw = () => {
      ctx.clearRect(0, 0, w, h);
      ctx.lineWidth = 0.8;
      for (let i = 0; i < pts.length; i++) {
        const a = pts[i] as Point;
        for (let j = i + 1; j < pts.length; j++) {
          const b = pts[j] as Point;
          const d = Math.hypot(a.x - b.x, a.y - b.y);
          if (d > link) continue;
          ctx.strokeStyle = `rgba(${ink[0]}, ${ink[1]}, ${ink[2]}, ${(1 - d / link) * (dark ? 0.16 : 0.13)})`;
          ctx.beginPath();
          ctx.moveTo(a.x, a.y);
          ctx.lineTo(b.x, b.y);
          ctx.stroke();
        }
        const dp = Math.hypot(a.x - pointer.x, a.y - pointer.y);
        if (dp < 170) {
          ctx.strokeStyle = `rgba(${brand[0]}, ${brand[1]}, ${brand[2]}, ${(1 - dp / 170) * 0.38})`;
          ctx.beginPath();
          ctx.moveTo(a.x, a.y);
          ctx.lineTo(pointer.x, pointer.y);
          ctx.stroke();
        }
      }
      for (const p of pts) {
        const c = p.lit ? brand : ink;
        ctx.fillStyle = `rgba(${c[0]}, ${c[1]}, ${c[2]}, ${p.lit ? 0.7 : dark ? 0.42 : 0.32})`;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx.fill();
      }
    };

    const step = (now: number) => {
      raf = requestAnimationFrame(step);
      if (!seen || document.hidden || now - last < 33) return;
      last = now;
      for (const p of pts) {
        p.x += p.vx;
        p.y += p.vy;
        if (p.x < -10) p.x = w + 10;
        else if (p.x > w + 10) p.x = -10;
        if (p.y < -10) p.y = h + 10;
        else if (p.y > h + 10) p.y = -10;
      }
      draw();
    };

    layout();
    draw();
    if (still) return;
    const watch =
      typeof IntersectionObserver === "undefined"
        ? null
        : new IntersectionObserver(([e]) => {
            seen = !!e?.isIntersecting;
          });
    watch?.observe(canvas);
    const resize = () => {
      layout();
      draw();
    };
    const host = canvas.parentElement;
    const move = (e: PointerEvent) => {
      const r = canvas.getBoundingClientRect();
      pointer.x = e.clientX - r.left;
      pointer.y = e.clientY - r.top;
    };
    const leave = () => {
      pointer.x = -9999;
      pointer.y = -9999;
    };
    window.addEventListener("resize", resize);
    if (fine && host?.parentElement) {
      host.parentElement.addEventListener("pointermove", move, { passive: true });
      host.parentElement.addEventListener("pointerleave", leave);
    }
    raf = requestAnimationFrame(step);
    return () => {
      cancelAnimationFrame(raf);
      watch?.disconnect();
      window.removeEventListener("resize", resize);
      host?.parentElement?.removeEventListener("pointermove", move);
      host?.parentElement?.removeEventListener("pointerleave", leave);
    };
  }, []);
  // Decoration only: its container is hidden from assistive technology.
  return <canvas ref={ref} className={className} />;
}
