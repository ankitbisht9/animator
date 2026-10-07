/** The public front page: what the platform does, shown with a live lesson, and how to start. */
import {
  ArrowRight,
  BookOpen,
  CircleCheck,
  GraduationCap,
  Hand,
  Languages,
  Lightbulb,
  PenLine,
  Play,
  School,
  Share2,
  Sparkles,
  Users,
} from "lucide-react";
import { type FormEvent, lazy, Suspense, useState } from "react";
import { useAuth } from "../auth";
import { useTitle } from "../hooks";
import { Link, navigate } from "../nav";
import { SUBJECTS } from "../ui/brand";
import { Button } from "../ui/core";

const HeroPreview = lazy(() => import("./HeroPreview"));

const FEATURES = [
  {
    icon: Sparkles,
    title: "Animated, not slides",
    text: "Every lesson is drawn by a real animation engine: diagrams that move, forces you can see, equations that build step by step.",
  },
  {
    icon: Hand,
    title: "Raise your hand",
    text: "Stuck mid-lesson? Pause and ask, by voice or text. The tutor answers on the same picture, and simpler if you ask again.",
  },
  {
    icon: Languages,
    title: "Your language",
    text: "Narrated in English or Hinglish, with word-by-word captions, at the speed you like.",
  },
  {
    icon: CircleCheck,
    title: "Checks for understanding",
    text: "Quick questions appear at the right moments, with an explanation whichever answer you pick.",
  },
  {
    icon: PenLine,
    title: "Notes and progress",
    text: "Take notes pinned to the exact moment, pick up where you left off, keep a learning streak.",
  },
  {
    icon: Share2,
    title: "Share with your class",
    text: "Send a lesson with a link, or publish it to Explore as an educator for every learner to find.",
  },
];

const STEPS = [
  {
    n: 1,
    title: "Ask for a topic",
    text: "“Explain Archimedes' principle.” Choose the level, the length and the language.",
  },
  {
    n: 2,
    title: "Watch it come alive",
    text: "The first scene plays within a minute or two, while the rest is still being made.",
  },
  {
    n: 3,
    title: "Ask, note, revisit",
    text: "Raise your hand for doubts, pin notes to moments, and come back to it any time.",
  },
];

const FAQ = [
  {
    q: "How long does a lesson take to make?",
    a: "The first scene is usually ready in one to two minutes and starts playing right away; the rest arrives while you watch.",
  },
  {
    q: "Which subjects work best?",
    a: "Anything that can be pictured: physics, chemistry, biology, mathematics, computer science, earth science, economics and more.",
  },
  {
    q: "Can I use it with my students?",
    a: "Yes. Share any lesson with a link. Educator accounts can also publish lessons to Explore for everyone.",
  },
  {
    q: "Is my data private?",
    a: "Your lessons are private unless you share them. Your notes and doubts are only ever yours. You can download or delete your data at any time.",
  },
];

export function LandingPage() {
  useTitle("");
  const { status } = useAuth();
  const [topic, setTopic] = useState("");
  const start = (e?: FormEvent) => {
    e?.preventDefault();
    const target = topic.trim() ? `/create?topic=${encodeURIComponent(topic.trim())}` : "/create";
    navigate(status === "signed-in" ? target : `/signup?next=${encodeURIComponent(target)}`);
  };
  return (
    <div className="landing">
      <section className="hero">
        <div className="hero-inner">
          <div className="hero-copy">
            <div className="kicker-pill">
              <Sparkles size={14} /> AI-directed animated lessons
            </div>
            <h1>
              Understand anything, <em>one moving picture</em> at a time.
            </h1>
            <p className="hero-lead">
              Type a topic and watch a clear, animated lesson made for your level, with a friendly voice, quick checks
              and a tutor you can ask mid-lesson.
            </p>
            <form className="hero-ask" onSubmit={start}>
              <Lightbulb size={20} aria-hidden="true" />
              <input
                value={topic}
                onChange={(e) => setTopic(e.target.value)}
                placeholder="What do you want to understand?"
                aria-label="Topic"
                maxLength={300}
              />
              <Button type="submit" variant="primary" iconRight={ArrowRight}>
                Create lesson
              </Button>
            </form>
            <div className="hero-secondary">
              <Link href="/samples/archimedes" className="hero-sample">
                <span className="hero-sample-play">
                  <Play size={14} />
                </span>
                Watch a sample lesson
              </Link>
              <span className="hero-note">Free to start · No credit card</span>
            </div>
          </div>
          <div className="hero-visual">
            <div className="hero-frame">
              <div className="hero-frame-bar" aria-hidden="true">
                <span />
                <span />
                <span />
                <b>Archimedes' principle · sample lesson</b>
              </div>
              <Suspense fallback={<div className="hero-preview-stage loading" />}>
                <HeroPreview />
              </Suspense>
            </div>
            <div className="hero-float hero-float-a" aria-hidden="true">
              <Hand size={16} /> “Why does the boat float?”
            </div>
            <div className="hero-float hero-float-b" aria-hidden="true">
              <CircleCheck size={16} /> Quick check: 3 of 3
            </div>
          </div>
        </div>
        <ul className="subject-strip" aria-label="Subjects">
          {Object.entries(SUBJECTS)
            .filter(([k]) => k !== "general")
            .map(([k, s]) => (
              <li key={k} className="subject-chip" style={{ color: s.ink, background: s.tint }}>
                <s.icon size={15} />
                {s.label}
              </li>
            ))}
        </ul>
      </section>

      <section className="landing-section" id="features">
        <div className="section-head">
          <span className="eyebrow">Why learners love it</span>
          <h2>A teacher's explanation, a textbook's precision, a cartoon's charm.</h2>
        </div>
        <div className="feature-grid">
          {FEATURES.map((f, i) => (
            <article key={f.title} className="feature reveal" style={{ animationDelay: `${i * 60}ms` }}>
              <div className="feature-icon">
                <f.icon size={22} />
              </div>
              <h3>{f.title}</h3>
              <p>{f.text}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="landing-section how" id="how">
        <div className="section-head">
          <span className="eyebrow">How it works</span>
          <h2>From a question to a lesson in three steps</h2>
        </div>
        <ol className="steps">
          {STEPS.map((s) => (
            <li key={s.n} className="step">
              <span className="step-n">{s.n}</span>
              <h3>{s.title}</h3>
              <p>{s.text}</p>
            </li>
          ))}
        </ol>
      </section>

      <section className="landing-section">
        <div className="section-head">
          <span className="eyebrow">Made for</span>
          <h2>Everyone who learns, and everyone who teaches</h2>
        </div>
        <div className="audience-grid">
          <article className="audience">
            <GraduationCap size={26} />
            <h3>Students</h3>
            <p>Revise a chapter the night before, or finally get the idea that never clicked in class.</p>
          </article>
          <article className="audience">
            <School size={26} />
            <h3>Teachers</h3>
            <p>Make a visual explanation for tomorrow's class in minutes, share it with a link, publish your best.</p>
          </article>
          <article className="audience">
            <Users size={26} />
            <h3>Curious minds</h3>
            <p>Ask about anything you wonder about, at your level, in your language.</p>
          </article>
        </div>
      </section>

      <section className="facts">
        <div>
          <b>1–20</b>
          <span>minutes per lesson, your choice</span>
        </div>
        <div>
          <b>2</b>
          <span>narration languages</span>
        </div>
        <div>
          <b>10</b>
          <span>subject areas</span>
        </div>
        <div>
          <b>1 tap</b>
          <span>to raise your hand</span>
        </div>
      </section>

      <section className="landing-section faq">
        <div className="section-head">
          <span className="eyebrow">Questions</span>
          <h2>Good to know</h2>
        </div>
        <div className="faq-list">
          {FAQ.map((f) => (
            <details key={f.q}>
              <summary>{f.q}</summary>
              <p>{f.a}</p>
            </details>
          ))}
        </div>
        <p className="faq-more">
          More in the <Link href="/help">help center</Link>.
        </p>
      </section>

      <section className="cta-band">
        <div>
          <h2>Your first lesson is one sentence away.</h2>
          <p>Create an account and watch your topic come to life.</p>
        </div>
        <div className="cta-actions">
          <Button variant="secondary" size="lg" icon={BookOpen} onClick={() => navigate("/explore")}>
            Browse lessons
          </Button>
          <Button variant="primary" size="lg" iconRight={ArrowRight} onClick={() => start()} className="on-red">
            Get started free
          </Button>
        </div>
      </section>
    </div>
  );
}
