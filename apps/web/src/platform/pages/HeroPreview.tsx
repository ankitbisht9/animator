/** The landing page's live preview: a sample lesson drawn by the real engine, looping quietly (paused
 * when off screen; one still frame when the visitor prefers reduced motion). Loaded lazily. */
import { SceneView } from "@animator/components";
import { evaluate } from "@animator/engine";
import { useEffect, useMemo, useRef, useState } from "react";
import { fixtureLesson } from "../../lesson/fixtures";
import { buildLesson, locate } from "../../lesson/lesson";

export default function HeroPreview({ slug = "archimedes", seconds = 36 }: { slug?: string; seconds?: number }) {
  const lesson = useMemo(() => {
    const f = fixtureLesson(slug);
    return f ? buildLesson(f.meta.title, f.scenes) : null;
  }, [slug]);
  const [t, setT] = useState(4);
  const box = useRef<HTMLDivElement>(null);
  const loop = Math.min(seconds, lesson?.duration ?? seconds);

  useEffect(() => {
    if (!lesson) return;
    const still = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (still) return;
    let raf = 0;
    let visible = true;
    let last = performance.now();
    const obs = new IntersectionObserver(([e]) => {
      visible = !!e?.isIntersecting;
    });
    if (box.current) obs.observe(box.current);
    const tick = (now: number) => {
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      if (visible && document.visibilityState === "visible") setT((x) => (x + dt >= loop ? 0.5 : x + dt));
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
      obs.disconnect();
    };
  }, [lesson, loop]);

  const loc = lesson ? locate(lesson, t) : null;
  const compiled = loc?.scene.compiled;
  const frame = useMemo(() => (compiled && loc ? evaluate(compiled, loc.local) : null), [compiled, loc?.local, loc]);
  return (
    <div className="hero-preview-stage" ref={box}>
      {compiled && frame ? (
        <SceneView
          compiled={compiled}
          frame={frame}
          captions
          narration={loc?.scene.narration ?? null}
          width="100%"
          height="100%"
        />
      ) : (
        <div className="hero-preview-empty" />
      )}
      <div className="hero-preview-progress" style={{ width: `${(t / loop) * 100}%` }} />
    </div>
  );
}
