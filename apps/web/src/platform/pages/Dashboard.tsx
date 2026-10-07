/** The learner's home: where they left off, how the week is going, what to do next. */
import {
  ArrowRight,
  BookOpen,
  Check,
  Clock,
  Compass,
  Flame,
  Hand,
  Lightbulb,
  PenLine,
  Play,
  Sparkles,
  Target,
  Trophy,
} from "lucide-react";
import { type FormEvent, useMemo, useState } from "react";
import { type Lesson, v1 } from "../api";
import { useUser } from "../auth";
import { clock, greeting, today } from "../format";
import { useInterval, useResource, useTitle } from "../hooks";
import { useSaveToggle } from "../lesson/actions";
import { LessonCard, LessonCardSkeleton } from "../lesson/LessonCard";
import { Link, navigate } from "../nav";
import { LessonCover, subject } from "../ui/brand";
import { BarChart } from "../ui/charts";
import { Button, Card, ProgressBar, ProgressRing, SectionTitle } from "../ui/core";

const IDEAS = [
  "How do vaccines train the immune system?",
  "Why is the sky blue?",
  "How does binary search work?",
  "What is compound interest?",
];

export function DashboardPage() {
  useTitle("Dashboard");
  const user = useUser();
  const day = today();
  const stats = useResource(`stats:${day}`, () => v1.stats(day));
  const learning = useResource("learning", v1.learning, 15_000);
  const interests = user.preferences.interests;
  const recommended = useResource(`explore:rec:${interests[0] ?? "all"}`, () =>
    v1.explore({ subject: interests[0], limit: 4 }).then((p) => (p.items.length ? p.items : v1.featured())),
  );
  const generating = learning.data?.created.some((l) => ["queued", "planning", "generating"].includes(l.status));
  useInterval(() => void learning.reload(), generating ? 5000 : null);
  const toggleSave = useSaveToggle();
  const s = stats.data;
  const goal = s ? Math.min(1, s.minutes_week / Math.max(1, s.weekly_goal_min)) : 0;
  const last7 = s?.activity.slice(-7) ?? [];

  return (
    <div className="page dashboard">
      <header className="page-head">
        <div>
          <p className="page-kicker">
            {new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" })}
          </p>
          <h1>
            {greeting()}, {user.name.split(" ")[0]}
          </h1>
        </div>
        <div className="page-head-actions">
          {s && s.streak > 0 && (
            <span className="streak-pill" title={`Best streak: ${s.best_streak} days`}>
              <Flame size={16} /> {s.streak}-day streak
            </span>
          )}
          {user.permissions.includes("lesson:create") && (
            <Button variant="primary" icon={Sparkles} onClick={() => navigate("/create")}>
              Create lesson
            </Button>
          )}
        </div>
      </header>

      {!user.onboarded && (
        <Card className="nudge">
          <div>
            <b>Tell us what you like to learn</b>
            <p>Two quick questions, and lessons and suggestions will fit you better.</p>
          </div>
          <Button variant="primary" iconRight={ArrowRight} onClick={() => navigate("/onboarding")}>
            Personalise
          </Button>
        </Card>
      )}

      <div className="dash-grid">
        <Card className="dash-goal">
          <div className="dash-card-title">
            <Target size={18} /> Weekly goal
          </div>
          <div className="dash-goal-body">
            <ProgressRing value={goal} size={108} stroke={10} label={`Weekly goal ${Math.round(goal * 100)}%`}>
              <b>{Math.round(s?.minutes_week ?? 0)}</b>
              <small>of {s?.weekly_goal_min ?? user.preferences.weekly_goal_min} min</small>
            </ProgressRing>
            <div className="dash-goal-text">
              {goal >= 1 ? (
                <p>
                  <b>Goal reached!</b> Wonderful consistency this week.
                </p>
              ) : (
                <p>
                  <b>{Math.max(0, Math.ceil((s?.weekly_goal_min ?? 60) - (s?.minutes_week ?? 0)))} min</b> to go this
                  week.
                </p>
              )}
              <Link href="/settings/learning" className="muted-link">
                Change goal
              </Link>
            </div>
          </div>
        </Card>
        <Card className="dash-streak">
          <div className="dash-card-title">
            <Flame size={18} /> Streak
          </div>
          <div className="dash-streak-num">
            <b>{s?.streak ?? 0}</b>
            <span>{s?.streak === 1 ? "day" : "days"}</span>
          </div>
          <ul className="week-dots" aria-label="This week">
            {last7.map((d) => (
              <li key={d.day} className={d.minutes >= 1 ? "on" : ""} title={`${d.day}: ${d.minutes} min`}>
                {new Date(`${d.day}T00:00:00`).toLocaleDateString(undefined, { weekday: "narrow" })}
              </li>
            ))}
          </ul>
          <small className="muted">Best: {s?.best_streak ?? 0} days · learn a minute a day to keep it</small>
        </Card>
        <Card className="dash-numbers">
          <div className="dash-card-title">
            <Trophy size={18} /> Your learning
          </div>
          <dl>
            <div>
              <dt>Minutes learned</dt>
              <dd>{s?.minutes_total ?? 0}</dd>
            </div>
            <div>
              <dt>Lessons finished</dt>
              <dd>{s?.lessons_completed ?? 0}</dd>
            </div>
            <div>
              <dt>Doubts asked</dt>
              <dd>{s?.doubts_asked ?? 0}</dd>
            </div>
            <div>
              <dt>Notes</dt>
              <dd>{s?.notes ?? 0}</dd>
            </div>
          </dl>
          {s && (
            <BarChart
              label="Minutes per day, last 14 days"
              height={56}
              unit=" min"
              data={s.activity.slice(-14).map((d) => ({ label: d.day.slice(8), value: d.minutes, hint: d.day }))}
            />
          )}
        </Card>
      </div>

      <section className="dash-section">
        <SectionTitle
          title="Continue learning"
          action={
            <Link href="/progress" className="muted-link">
              See progress
            </Link>
          }
        />
        {learning.loading && !learning.data ? (
          <div className="continue-row">
            <LessonCardSkeleton />
            <LessonCardSkeleton />
          </div>
        ) : learning.data?.continue.length ? (
          <div className="continue-row">
            {learning.data.continue.map((l) => (
              <ContinueCard key={l.id} lesson={l} />
            ))}
          </div>
        ) : (
          <QuickStart />
        )}
      </section>

      {!!learning.data?.continue.length && <QuickStart compact />}

      <Checklist />

      <section className="dash-section">
        <SectionTitle
          title="Your recent lessons"
          action={
            <Link href="/lessons" className="muted-link">
              All lessons <ArrowRight size={14} />
            </Link>
          }
        />
        {learning.data && learning.data.created.length === 0 ? (
          <p className="muted">Lessons you create appear here.</p>
        ) : (
          <div className="lesson-grid">
            {(learning.data?.created ?? []).slice(0, 4).map((l) => (
              <LessonCard key={l.id} lesson={l} onToggleSave={toggleSave} />
            ))}
            {!learning.data && [0, 1, 2].map((i) => <LessonCardSkeleton key={i} />)}
          </div>
        )}
      </section>

      {!!recommended.data?.length && (
        <section className="dash-section">
          <SectionTitle
            title={interests.length ? "Recommended for you" : "Popular on Animator"}
            sub={
              interests.length
                ? `Because you like ${interests
                    .map((k) => subject(k).label)
                    .slice(0, 2)
                    .join(" and ")}`
                : undefined
            }
            action={
              <Link href="/explore" className="muted-link">
                Explore <Compass size={14} />
              </Link>
            }
          />
          <div className="lesson-grid">
            {recommended.data.slice(0, 4).map((l) => (
              <LessonCard key={l.id} lesson={l} showOwner onToggleSave={toggleSave} />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

function ContinueCard({ lesson }: { lesson: Lesson }) {
  const p = lesson.progress;
  return (
    <Link href={`/lessons/${lesson.id}`} className="continue-card">
      <div className="continue-cover">
        <LessonCover lesson={lesson} compact />
        <span className="continue-play">
          <Play size={18} />
        </span>
      </div>
      <div className="continue-body">
        <span className="continue-subject" style={{ color: subject(lesson.subject).ink }}>
          {subject(lesson.subject).label}
        </span>
        <b>{lesson.title}</b>
        <ProgressBar value={p?.percent ?? 0} label="Watched" />
        <small>
          <Clock size={12} /> Resume at {clock(p?.position_s ?? 0)} · {Math.round((p?.percent ?? 0) * 100)}%
        </small>
      </div>
    </Link>
  );
}

function QuickStart({ compact = false }: { compact?: boolean }) {
  const [topic, setTopic] = useState("");
  const go = (e?: FormEvent, t?: string) => {
    e?.preventDefault();
    const value = (t ?? topic).trim();
    navigate(value ? `/create?topic=${encodeURIComponent(value)}` : "/create");
  };
  return (
    <Card className={`quickstart ${compact ? "compact" : ""}`}>
      <div className="quickstart-icon">
        <Lightbulb size={22} />
      </div>
      <div className="quickstart-body">
        <b>{compact ? "Curious about something new?" : "Start with a question"}</b>
        <form onSubmit={go} className="quickstart-form">
          <input
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            placeholder="What do you want to understand today?"
            aria-label="Topic"
          />
          <Button type="submit" variant="primary" iconRight={ArrowRight}>
            Create
          </Button>
        </form>
        <div className="chips">
          {IDEAS.map((i) => (
            <button key={i} type="button" className="chip" onClick={() => go(undefined, i)}>
              {i}
            </button>
          ))}
        </div>
      </div>
    </Card>
  );
}

function Checklist() {
  const user = useUser();
  const day = today();
  const stats = useResource(`stats:${day}`, () => v1.stats(day));
  const items = useMemo(() => {
    const s = stats.data;
    if (!s) return [];
    return [
      { done: user.verified, label: "Confirm your email", icon: Check, href: "/settings/account" },
      { done: s.lessons_created > 0, label: "Create your first lesson", icon: Sparkles, href: "/create" },
      { done: s.lessons_completed > 0, label: "Finish a lesson", icon: BookOpen, href: "/lessons" },
      { done: s.doubts_asked > 0, label: "Raise your hand with a doubt", icon: Hand, href: "/help#doubts" },
      { done: s.notes > 0, label: "Pin a note to a moment", icon: PenLine, href: "/help#watching" },
    ];
  }, [stats.data, user.verified]);
  const done = items.filter((i) => i.done).length;
  if (!items.length || done === items.length) return null;
  return (
    <Card className="checklist">
      <div className="checklist-head">
        <div>
          <b>Getting started</b>
          <small>
            {done} of {items.length} done
          </small>
        </div>
        <ProgressBar value={done / items.length} label="Getting started" />
      </div>
      <ul>
        {items.map((i) => (
          <li key={i.label} className={i.done ? "done" : ""}>
            <span className="checklist-mark">{i.done ? <Check size={14} /> : <i.icon size={14} />}</span>
            {i.done ? (
              <span>{i.label}</span>
            ) : (
              <Link href={i.href}>
                {i.label} <ArrowRight size={13} />
              </Link>
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}
