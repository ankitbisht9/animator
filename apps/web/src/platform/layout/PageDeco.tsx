/**
 * The band behind each page's heading (ADR-057), in the app's color: a soft glow and a fine grid that
 * fades out towards the heading, drawn brighter around the pointer as it moves over the band. Only CSS
 * paints it; the pointer only moves two variables, at most once a frame, and only over the band. Static
 * on touch screens and with reduced motion. AppShell leaves it out of lesson pages (they stay light).
 */
import { useEffect, useRef } from "react";

function still(): boolean {
  return (
    document.documentElement.dataset.motion === "reduced" ||
    (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false)
  );
}

export function PageDeco() {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const band = ref.current;
    const area = band?.parentElement;
    if (!band || !area || !(window.matchMedia?.("(pointer: fine)").matches ?? false)) return;
    let frame = 0;
    let x = 0;
    let y = 0;
    const paint = () => {
      frame = 0;
      const r = band.getBoundingClientRect();
      if (still() || y < r.top || y > r.bottom || x < r.left || x > r.right) {
        band.removeAttribute("data-spot");
        return;
      }
      band.style.setProperty("--spot-x", `${Math.round(x - r.left)}px`);
      band.style.setProperty("--spot-y", `${Math.round(y - r.top)}px`);
      band.setAttribute("data-spot", "");
    };
    const move = (e: PointerEvent) => {
      x = e.clientX;
      y = e.clientY;
      if (!frame) frame = requestAnimationFrame(paint);
    };
    const leave = () => {
      if (frame) cancelAnimationFrame(frame);
      frame = 0;
      band.removeAttribute("data-spot");
    };
    area.addEventListener("pointermove", move, { passive: true });
    area.addEventListener("pointerleave", leave);
    return () => {
      area.removeEventListener("pointermove", move);
      area.removeEventListener("pointerleave", leave);
      if (frame) cancelAnimationFrame(frame);
    };
  }, []);
  return <div className="page-deco" ref={ref} aria-hidden="true" />;
}
