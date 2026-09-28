import { DebugPage } from "./pages/DebugPage";
import { GenerationPage } from "./pages/GenerationPage";
import { Home } from "./pages/Home";
import { LessonPage } from "./pages/LessonPage";
import { Playground } from "./pages/Playground";
import { RenderPage } from "./pages/RenderPage";
import { Link, usePath } from "./router";

export function App() {
  const path = usePath();
  if (path === "/render") return <RenderPage />;
  const lesson = /^\/lesson\/([a-z0-9_-]+)\/?$/.exec(path);
  const generated = /^\/g\/(g_[0-9a-f]{12})\/?$/.exec(path);
  const debug = /^\/debug\/(g_[0-9a-f]{12})\/?$/.exec(path);
  return (
    <div className="app">
      <header className="topbar">
        <Link href="/" className="brand">
          <svg viewBox="0 0 32 32" width="28" height="28" aria-hidden="true">
            <rect x="2" y="2" width="28" height="28" rx="9" fill="var(--accent)" />
            <circle cx="12" cy="18" r="5" fill="#ffc43d" />
            <path d="M17 12l7 4-7 4z" fill="#fff" />
          </svg>
          Animator
        </Link>
        <nav>
          <Link href="/" className={path === "/" ? "active" : ""}>
            Lessons
          </Link>
          <Link href="/playground" className={path === "/playground" ? "active" : ""}>
            DSL Playground
          </Link>
        </nav>
      </header>
      <main>
        {lesson?.[1] ? (
          <LessonPage slug={lesson[1]} />
        ) : generated?.[1] ? (
          <GenerationPage key={generated[1]} id={generated[1]} />
        ) : debug?.[1] ? (
          <DebugPage id={debug[1]} />
        ) : path === "/playground" ? (
          <Playground />
        ) : (
          <Home />
        )}
      </main>
    </div>
  );
}
