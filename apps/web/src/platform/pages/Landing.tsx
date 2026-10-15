/**
 * The public front page (ADR-062): what the platform does, shown with a live lesson, and how to start.
 * Parts rise into view as they are reached, the figures count up, the live lesson tilts flat as it
 * comes up, and a slow network of points drifts behind the opening; all of it still for visitors who
 * prefer reduced motion (landing/motion.tsx).
 */
import {
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  Check,
  CircleCheck,
  Clock3,
  Flame,
  GraduationCap,
  Hand,
  Languages,
  Lightbulb,
  Link2,
  type LucideIcon,
  MessageCircleQuestion,
  PenLine,
  Play,
  Plus,
  School,
  Share2,
  Sparkles,
  Telescope,
  WandSparkles,
} from "lucide-react";
import { type CSSProperties, type FormEvent, lazy, Suspense, useRef, useState } from "react";
import { useAuth } from "../auth";
import { useTitle } from "../hooks";
import { Constellation, CountUp, prefersStill, useReveal, useScrollTilt, useSpotlight } from "../landing/motion";
import { Link, navigate } from "../nav";
import { SUBJECTS } from "../ui/brand";
import { Button } from "../ui/core";

const HeroPreview = lazy(() => import("./HeroPreview"));

/** Who is learning: each brings its own example topics (the field's placeholder and three ideas). */
const LEARNERS: readonly { key: string; label: string; icon: LucideIcon; placeholder: string; ideas: string[] }[] = [
  {
    key: "student",
    label: "Student",
    icon: GraduationCap,
    placeholder: "Explain photosynthesis for my exam",
    ideas: ["How does a transistor work?", "Newton's third law, simply", "What is a derivative?"],
  },
  {
    key: "teacher",
    label: "Teacher",
    icon: School,
    placeholder: "A 5-minute lesson on the water cycle for class 7",
    ideas: ["Ohm's law with a worked example", "Mitosis, step by step", "Why the French Revolution began"],
  },
  {
    key: "curious",
    label: "Curious",
    icon: Telescope,
    placeholder: "Why is the sky blue?",
    ideas: ["How do vaccines train the body?", "Why do huge ships float?", "How does compound interest grow?"],
  },
];

const STEPS = [
  {
    icon: MessageCircleQuestion,
    title: "Ask for a topic",
    text: "“Explain Archimedes' principle.” Choose the level, the length and the language.",
  },
  {
    icon: WandSparkles,
    title: "Watch it come alive",
    text: "The first scene plays within a minute or two, while the rest is still being made.",
  },
  {
    icon: PenLine,
    title: "Ask, note, revisit",
    text: "Raise your hand for doubts, pin notes to moments, and come back to it any time.",
  },
];

const AUDIENCES = [
  {
    icon: GraduationCap,
    title: "Students",
    text: "Revise a chapter the night before, or finally get the idea that never clicked in class.",
    idea: "Explain Newton's laws",
  },
  {
    icon: School,
    title: "Teachers",
    text: "Make a visual explanation for tomorrow's class in minutes, share it with a link, publish your best.",
    idea: "The water cycle for class 7",
  },
  {
    icon: Telescope,
    title: "Curious minds",
    text: "Ask about anything you wonder about, at your level, in your language.",
    idea: "Why is the sky blue?",
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

/** A delay for a part that rises into view (staggered within its group). */
const delay = (i: number, step = 90): CSSProperties => ({ "--d": `${i * step}ms` }) as CSSProperties;

export function LandingPage() {
  useTitle("");
  const { status } = useAuth();
  const [topic, setTopic] = useState("");
  const [who, setWho] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const show = useRef<HTMLDivElement>(null);
  const bento = useRef<HTMLDivElement>(null);
  useReveal(root);
  useScrollTilt(show);
  useSpotlight(bento);
  const learner = LEARNERS[who] ?? (LEARNERS[0] as (typeof LEARNERS)[number]);
  const create = (t: string) => {
    const target = t.trim() ? `/create?topic=${encodeURIComponent(t.trim())}` : "/create";
    navigate(status === "signed-in" ? target : `/signup?next=${encodeURIComponent(target)}`);
  };
  const start = (e?: FormEvent) => {
    e?.preventDefault();
    create(topic);
  };
  const subjects = Object.entries(SUBJECTS).filter(([k]) => k !== "general");
  const moving = !prefersStill();

  return (
    <div className="lp" ref={root}>
      {/* ---------------- the opening ---------------- */}
      <section className="lp-hero">
        <div className="lp-hero-bg" aria-hidden="true">
          <span className="lp-glow lp-glow-a" />
          <span className="lp-glow lp-glow-b" />
          <span className="lp-glow lp-glow-c" />
          <span className="lp-grid" />
          <Constellation className="lp-net" />
        </div>
        <div className="lp-hero-inner">
          <p className="lp-kicker">
            <span className="lp-kicker-dot" aria-hidden="true" />
            <span className="lp-kicker-text">
              AI-directed animated lessons
              <span className="lp-kicker-more">
                {" "}
                <i aria-hidden="true">·</i> English &amp; Hinglish <i aria-hidden="true">·</i> 1 to 20 minutes
              </span>
            </span>
          </p>
          <h1 className="lp-title">
            <span className="lp-line" style={{ "--i": 0 } as CSSProperties}>
              Understand anything,
            </span>
            <span className="lp-line" style={{ "--i": 1 } as CSSProperties}>
              <em className="lp-accent">
                one moving picture
                <svg className="lp-underline" viewBox="0 0 300 18" preserveAspectRatio="none" aria-hidden="true">
                  <path d="M3 13 C 60 4, 140 3, 297 9" pathLength={1} />
                </svg>
              </em>
            </span>
            <span className="lp-line" style={{ "--i": 2 } as CSSProperties}>
              at a time.
            </span>
          </h1>
          <p className="lp-lead">
            Type a topic and watch a clear, animated lesson made for your level, with a friendly voice, quick checks and
            a tutor you can ask mid-lesson.
          </p>

          <div className="lp-who">
            <span className="lp-who-label" aria-hidden="true">
              I'm a
            </span>
            <fieldset className="lp-switch" style={{ "--n": who } as CSSProperties}>
              <legend className="sr-only">Who is learning? Example topics follow your choice.</legend>
              {LEARNERS.map((l, i) => (
                <button
                  key={l.key}
                  type="button"
                  aria-pressed={who === i}
                  className={who === i ? "on" : ""}
                  onClick={() => setWho(i)}
                >
                  <l.icon size={15} aria-hidden="true" />
                  {l.label}
                </button>
              ))}
              <span className="lp-switch-thumb" aria-hidden="true" />
            </fieldset>
          </div>

          <form className="lp-ask" onSubmit={start}>
            <Lightbulb size={20} aria-hidden="true" />
            <input
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder={learner.placeholder}
              aria-label="Topic"
              maxLength={300}
            />
            <Button type="submit" variant="primary" size="lg" iconRight={ArrowRight} className="lp-pill">
              Create lesson
            </Button>
          </form>
          <ul className="lp-ideas" aria-label="Ideas to try">
            {learner.ideas.map((idea) => (
              <li key={idea}>
                <button type="button" onClick={() => setTopic(idea)}>
                  {idea}
                </button>
              </li>
            ))}
          </ul>
          <div className="lp-secondary">
            <Link href="/samples/archimedes" className="lp-sample">
              <span className="lp-sample-play">
                <Play size={14} />
              </span>
              Watch a sample lesson
            </Link>
            <span className="lp-note">
              <Check size={15} aria-hidden="true" /> Free to start
              <Check size={15} aria-hidden="true" /> No credit card
            </span>
          </div>
        </div>

        <div className="lp-show" ref={show}>
          <div className="lp-show-frame">
            <div className="lp-show-bar" aria-hidden="true">
              <span />
              <span />
              <span />
              <b>Archimedes' principle · sample lesson</b>
              <em className="lp-live">
                <i className="live-dot" /> Live preview
              </em>
            </div>
            <Suspense fallback={<div className="hero-preview-stage loading" />}>
              <HeroPreview />
            </Suspense>
          </div>
          <div className="lp-chip lp-chip-a" aria-hidden="true">
            <span className="lp-chip-icon">
              <Clock3 size={18} />
            </span>
            <span>
              <b>1 to 20 minutes</b>
              <small>a lesson as long as you like</small>
            </span>
          </div>
          <div className="lp-chip lp-chip-b" aria-hidden="true">
            <span className="lp-chip-icon">
              <Hand size={18} />
            </span>
            <span>
              <b>“Why does the boat float?”</b>
              <small>ask mid-lesson, by voice or text</small>
            </span>
          </div>
          <div className="lp-chip lp-chip-c" aria-hidden="true">
            <span className="lp-chip-icon">
              <CircleCheck size={18} />
            </span>
            <span>
              <b>Quick check: 3 of 3</b>
              <small>at the right moments</small>
            </span>
          </div>
          <div className="lp-chip lp-chip-d" aria-hidden="true">
            <span className="lp-chip-icon">
              <Languages size={18} />
            </span>
            <span>
              <b>English · Hinglish</b>
              <small>word-by-word captions</small>
            </span>
          </div>
        </div>
      </section>

      {/* ---------------- subjects ---------------- */}
      <section className="lp-subjects" aria-label="Subjects">
        <p className="lp-subjects-label">Lessons in every subject that can be pictured</p>
        <div className="lp-marquee">
          <ul className="lp-marquee-track">
            {[0, 1].flatMap((copy) =>
              subjects.map(([k, s]) => (
                <li
                  key={`${k}-${copy}`}
                  className="lp-subject"
                  style={{ color: s.ink, background: s.tint }}
                  aria-hidden={copy === 1 || undefined}
                >
                  <s.icon size={16} />
                  {s.label}
                </li>
              )),
            )}
          </ul>
        </div>
      </section>

      {/* ---------------- what it does ---------------- */}
      <section className="lp-section" id="features">
        <header className="lp-head lp-reveal">
          <span className="lp-eyebrow">Why learners love it</span>
          <h2 className="lp-h2">
            A teacher's explanation, a textbook's precision, <em>a cartoon's charm.</em>
          </h2>
        </header>
        <div className="lp-bento" ref={bento}>
          <div className="lp-cell wide lp-reveal" style={delay(0)}>
            <article className="lp-card lp-spot">
              <span className="lp-card-icon">
                <Sparkles size={22} />
              </span>
              <h3>Animated, not slides</h3>
              <p>
                Every lesson is drawn by a real animation engine: diagrams that move, forces you can see, equations that
                build step by step.
              </p>
              <ProjectileViz moving={moving} />
            </article>
          </div>
          <div className="lp-cell lp-reveal" style={delay(1)}>
            <article className="lp-card lp-spot">
              <span className="lp-card-icon">
                <Hand size={22} />
              </span>
              <h3>Raise your hand</h3>
              <p>
                Stuck mid-lesson? Pause and ask. The tutor answers on the same picture, and simpler if you ask again.
              </p>
              <div className="lp-viz lp-viz-chat" aria-hidden="true">
                <span className="lp-bubble q">
                  <Hand size={14} /> Why does the boat float?
                </span>
                <span className="lp-bubble a">It pushes aside water that weighs as much as it does.</span>
              </div>
            </article>
          </div>
          <div className="lp-cell lp-reveal" style={delay(0)}>
            <article className="lp-card lp-spot">
              <span className="lp-card-icon">
                <Languages size={22} />
              </span>
              <h3>Your language</h3>
              <p>Narrated in English or Hinglish, with word-by-word captions, at the speed you like.</p>
              <div className="lp-viz lp-viz-lang" aria-hidden="true">
                <span className="lp-lang on">English</span>
                <span className="lp-lang">Hinglish</span>
                <p className="lp-caption">
                  <span>Every</span> <span>word</span> <span>lights</span> <span>up</span> <span>as</span>{" "}
                  <span>it</span> <span>is</span> <span>spoken.</span>
                </p>
              </div>
            </article>
          </div>
          <div className="lp-cell lp-reveal" style={delay(1)}>
            <article className="lp-card lp-spot">
              <span className="lp-card-icon">
                <CircleCheck size={22} />
              </span>
              <h3>Checks for understanding</h3>
              <p>Quick questions appear at the right moments, with an explanation whichever answer you pick.</p>
              <ul className="lp-viz lp-viz-quiz" aria-hidden="true">
                <li>It is lighter than water</li>
                <li className="right">
                  It moves enough water aside <Check size={14} />
                </li>
                <li>The air holds it up</li>
              </ul>
            </article>
          </div>
          <div className="lp-cell lp-reveal" style={delay(2)}>
            <article className="lp-card lp-spot">
              <span className="lp-card-icon">
                <PenLine size={22} />
              </span>
              <h3>Notes and progress</h3>
              <p>Take notes pinned to the exact moment, pick up where you left off, keep a learning streak.</p>
              <div className="lp-viz lp-viz-notes" aria-hidden="true">
                <span className="lp-note-chip">
                  <b>02:14</b> buoyant force = weight of water moved
                </span>
                <span className="lp-streak">
                  <Flame size={15} /> 5-day streak
                </span>
                <span className="lp-meter">
                  <span />
                </span>
              </div>
            </article>
          </div>
          <div className="lp-cell full lp-reveal" style={delay(0)}>
            <article className="lp-card lp-card-row lp-spot">
              <div className="lp-card-copy">
                <span className="lp-card-icon">
                  <Share2 size={22} />
                </span>
                <h3>Share with your class</h3>
                <p>Send a lesson with a link, or publish it to Explore as an educator for every learner to find.</p>
              </div>
              <div className="lp-viz lp-viz-share" aria-hidden="true">
                <span className="lp-link">
                  <Link2 size={15} /> animator.app/l/archimedes <b>Copy</b>
                </span>
                <span className="lp-faces">
                  <i className="av-crimson">AK</i>
                  <i className="av-teal">RS</i>
                  <i className="av-indigo">MJ</i>
                  <i className="av-amber">PT</i>
                  <em>+28 in class 9B</em>
                </span>
              </div>
            </article>
          </div>
        </div>
      </section>

      {/* ---------------- how it works ---------------- */}
      <section className="lp-section" id="how">
        <header className="lp-head lp-reveal">
          <span className="lp-eyebrow">How it works</span>
          <h2 className="lp-h2">
            From a question to a lesson in <em>three steps.</em>
          </h2>
        </header>
        <ol className="lp-steps lp-reveal">
          {STEPS.map((s, i) => (
            <li key={s.title} className="lp-step">
              <span className="lp-step-n" aria-hidden="true">
                {String(i + 1).padStart(2, "0")}
              </span>
              <span className="lp-step-icon">
                <s.icon size={24} />
              </span>
              <h3>
                <span className="sr-only">Step {i + 1}: </span>
                {s.title}
              </h3>
              <p>{s.text}</p>
            </li>
          ))}
        </ol>
      </section>

      {/* ---------------- who it is for, in figures ---------------- */}
      <section className="lp-night">
        <div className="lp-night-inner">
          <span className="lp-stars" aria-hidden="true" />
          <header className="lp-head lp-reveal">
            <span className="lp-eyebrow">Made for</span>
            <h2 className="lp-h2">
              Everyone who learns, <em>and everyone who teaches.</em>
            </h2>
          </header>
          <div className="lp-aud">
            {AUDIENCES.map((a, i) => (
              <article key={a.title} className="lp-aud-card lp-reveal" style={delay(i)}>
                <span className="lp-card-icon">
                  <a.icon size={22} />
                </span>
                <h3>{a.title}</h3>
                <p>{a.text}</p>
                <button type="button" className="lp-try" onClick={() => create(a.idea)}>
                  Try “{a.idea}” <ArrowUpRight size={15} />
                </button>
              </article>
            ))}
          </div>
          <dl className="lp-stats lp-reveal">
            <div>
              <dt>minutes a lesson, your choice</dt>
              <dd>
                1–
                <CountUp to={20} />
              </dd>
            </div>
            <div>
              <dt>narration languages</dt>
              <dd>
                <CountUp to={2} duration={900} />
              </dd>
            </div>
            <div>
              <dt>subject areas</dt>
              <dd>
                <CountUp to={10} />
              </dd>
            </div>
            <div>
              <dt>to raise your hand</dt>
              <dd>
                <CountUp to={1} duration={700} /> tap
              </dd>
            </div>
          </dl>
        </div>
      </section>

      {/* ---------------- questions ---------------- */}
      <section className="lp-section lp-faq" id="faq">
        <header className="lp-faq-side lp-reveal">
          <span className="lp-eyebrow">Questions</span>
          <h2 className="lp-h2">
            Good to <em>know.</em>
          </h2>
          <p className="lp-sub">
            More answers in the <Link href="/help">help center</Link>, or <Link href="/support">write to us</Link>.
          </p>
        </header>
        <div className="lp-faq-list lp-reveal" style={delay(1)}>
          {FAQ.map((f) => (
            <details key={f.q}>
              <summary>
                {f.q}
                <span className="lp-faq-plus" aria-hidden="true">
                  <Plus size={16} />
                </span>
              </summary>
              <p>{f.a}</p>
            </details>
          ))}
        </div>
      </section>

      {/* ---------------- the last word ---------------- */}
      <section className="lp-cta">
        <div className="lp-cta-card lp-reveal">
          <h2>
            Your first lesson is <em>one sentence away.</em>
          </h2>
          <p>Create an account and watch your topic come to life.</p>
          <div className="lp-cta-actions">
            <Button
              variant="ghost"
              size="lg"
              icon={BookOpen}
              onClick={() => navigate("/explore")}
              className="lp-pill lp-ghost"
            >
              Browse lessons
            </Button>
            <Button
              variant="primary"
              size="lg"
              iconRight={ArrowRight}
              onClick={() => start()}
              className="lp-pill on-red"
            >
              Get started free
            </Button>
          </div>
        </div>
      </section>
    </div>
  );
}

/** A ball thrown in an arc, its path drawn as it flies: "diagrams that move" (still with reduced motion). */
function ProjectileViz({ moving }: { moving: boolean }) {
  const path = "M24 132 Q 210 -36 396 132";
  return (
    <svg className="lp-viz lp-viz-arc" viewBox="0 0 420 150" aria-hidden="true">
      <line x1="12" y1="133" x2="408" y2="133" className="lp-arc-ground" />
      <path d={path} className="lp-arc-path" pathLength={1} />
      <g className="lp-arc-labels">
        <text x="198" y="26">
          v = 12 m/s
        </text>
        <text x="300" y="120">
          g ↓
        </text>
      </g>
      <g className="lp-arc-ball">
        <circle r="9" cx={moving ? 0 : 210} cy={moving ? 0 : 48} />
        {moving && <animateMotion dur="3.6s" repeatCount="indefinite" path={path} />}
      </g>
    </svg>
  );
}
