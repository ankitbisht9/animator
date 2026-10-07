/** The learner's own corner: Library (saved, notes, doubts), Progress, Profile, Notifications. */
import {
  Award,
  Bell,
  Bookmark,
  BookOpen,
  CalendarDays,
  Clock,
  Flame,
  Hand,
  Lock,
  MessageSquare,
  NotebookPen,
  Sparkles,
  Target,
  Trophy,
} from "lucide-react";
import { useMemo, useState } from "react";
import { v1 } from "../api";
import { useUser } from "../auth";
import { ago, clock, date, plural, today } from "../format";
import { invalidate, useDebounced, useResource, useTitle } from "../hooks";
import { useSaveToggle } from "../lesson/actions";
import { LessonCard, LessonCardSkeleton } from "../lesson/LessonCard";
import { Link, navigate } from "../nav";
import { BarChart, Heatmap } from "../ui/charts";
import {
  Avatar,
  Badge,
  Button,
  Card,
  EmptyState,
  ProgressRing,
  SearchBox,
  SectionTitle,
  Spinner,
  Stat,
  Tabs,
} from "../ui/core";

// ---------------------------------------------------------------------- library

type LibTab = "saved" | "notes" | "doubts";

export function LibraryPage() {
  useTitle("Library");
  const [tab, setTab] = useState<LibTab>("saved");
  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Library</h1>
          <p className="page-sub">Lessons you saved, the notes you wrote and the doubts you asked, in one place.</p>
        </div>
      </header>
      <Tabs
        label="Library"
        value={tab}
        onChange={setTab}
        tabs={[
          { value: "saved", label: "Saved lessons", icon: Bookmark },
          { value: "notes", label: "Notes", icon: NotebookPen },
          { value: "doubts", label: "Doubts", icon: MessageSquare },
        ]}
      />
      <div className="tab-body">
        {tab === "saved" && <Saved />}
        {tab === "notes" && <AllNotes />}
        {tab === "doubts" && <AllDoubts />}
      </div>
    </div>
  );
}

function Saved() {
  const res = useResource("library", () => v1.library());
  const toggle = useSaveToggle();
  if (res.loading && !res.data)
    return (
      <div className="lesson-grid">
        {[0, 1, 2].map((i) => (
          <LessonCardSkeleton key={i} />
        ))}
      </div>
    );
  if (!res.data?.items.length)
    return (
      <EmptyState
        icon={Bookmark}
        title="Nothing saved yet"
        text="Tap the bookmark on any lesson to keep it here."
        action={
          <Button variant="primary" onClick={() => navigate("/explore")}>
            Explore lessons
          </Button>
        }
      />
    );
  return (
    <div className="lesson-grid">
      {res.data.items.map((l) => (
        <LessonCard key={l.id} lesson={l} onToggleSave={toggle} showOwner={!l.mine} />
      ))}
    </div>
  );
}

function AllNotes() {
  const [q, setQ] = useState("");
  const term = useDebounced(q, 250);
  const res = useResource(`all-notes:${term}`, () => v1.allNotes(term));
  const groups = useMemo(() => {
    const g = new Map<string, { title: string; notes: NonNullable<typeof res.data> }>();
    for (const n of res.data ?? []) {
      const e = g.get(n.lesson_id) ?? { title: n.lesson_title ?? "Lesson", notes: [] };
      e.notes.push(n);
      g.set(n.lesson_id, e);
    }
    return [...g.entries()];
  }, [res.data]);
  return (
    <div className="all-notes">
      <SearchBox value={q} onChange={setQ} placeholder="Search your notes" />
      {res.loading && !res.data ? (
        <Spinner />
      ) : groups.length === 0 ? (
        <EmptyState
          icon={NotebookPen}
          title={term ? "No notes match" : "No notes yet"}
          text="While watching, press N to pin a note to the moment."
        />
      ) : (
        groups.map(([lessonId, g]) => (
          <Card key={lessonId} className="note-group">
            <Link href={`/lessons/${lessonId}`} className="note-group-title">
              <BookOpen size={16} /> {g.title}
            </Link>
            <ul>
              {g.notes
                .slice()
                .sort((a, b) => a.t_s - b.t_s)
                .map((n) => (
                  <li key={n.id}>
                    <span className="notes-time static">{clock(n.t_s)}</span>
                    <p>{n.text}</p>
                  </li>
                ))}
            </ul>
          </Card>
        ))
      )}
    </div>
  );
}

function AllDoubts() {
  const res = useResource("my-doubts", v1.myDoubts);
  if (res.loading && !res.data) return <Spinner />;
  if (!res.data?.length)
    return (
      <EmptyState
        icon={Hand}
        title="No doubts yet"
        text="Press the hand on any lesson (or D) to ask the tutor. Your questions show up here."
      />
    );
  return (
    <ul className="doubt-list">
      {res.data.map((d) => (
        <li key={`${d.session_id}-${d.turn}`}>
          <MessageSquare size={16} />
          <div>
            <b>{d.question}</b>
            <small>
              <Link href={`/lessons/${d.lesson_id}`}>{d.lesson_title}</Link> · {ago(d.created_at)}
            </small>
          </div>
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------- progress

export function ProgressPage() {
  useTitle("Progress");
  const day = today();
  const stats = useResource(`stats:${day}`, () => v1.stats(day));
  const learning = useResource("learning", v1.learning);
  const s = stats.data;
  if (!s) return <div className="page">{stats.error ? <p>{stats.error.message}</p> : <Spinner />}</div>;
  const goal = Math.min(1, s.minutes_week / Math.max(1, s.weekly_goal_min));
  const unlocked = s.achievements.filter((a) => a.unlocked).length;
  return (
    <div className="page progress-page">
      <header className="page-head">
        <div>
          <h1>Your progress</h1>
          <p className="page-sub">Small steps every day add up. Here is how far you have come.</p>
        </div>
      </header>
      <div className="stat-row">
        <Stat
          icon={Flame}
          label="Current streak"
          value={`${s.streak} ${s.streak === 1 ? "day" : "days"}`}
          sub={`Best ${s.best_streak}`}
        />
        <Stat
          icon={Clock}
          label="Time learned"
          value={`${s.minutes_total} min`}
          sub={`${s.minutes_today} min today`}
          tone="info"
        />
        <Stat
          icon={Trophy}
          label="Lessons finished"
          value={s.lessons_completed}
          sub={`${s.lessons_started} started`}
          tone="success"
        />
        <Stat
          icon={Hand}
          label="Doubts asked"
          value={s.doubts_asked}
          sub={`${plural(s.notes, "note")} written`}
          tone="warning"
        />
      </div>
      <div className="progress-grid">
        <Card className="progress-goal">
          <SectionTitle title="This week" />
          <div className="progress-goal-body">
            <ProgressRing value={goal} size={140} stroke={12} label={`${Math.round(goal * 100)}% of weekly goal`}>
              <b>{Math.round(s.minutes_week)}</b>
              <small>of {s.weekly_goal_min} min</small>
            </ProgressRing>
            <div>
              <p>
                {goal >= 1
                  ? "You reached your weekly goal. Brilliant!"
                  : `${Math.ceil(s.weekly_goal_min - s.minutes_week)} minutes to reach your goal.`}
              </p>
              <Link href="/settings/learning" className="muted-link">
                <Target size={14} /> Change weekly goal
              </Link>
            </div>
          </div>
        </Card>
        <Card className="progress-chart">
          <SectionTitle title="Last 14 days" sub="Minutes learned per day" />
          <BarChart
            label="Minutes learned per day"
            unit=" min"
            data={s.activity.slice(-14).map((d) => ({
              label: new Date(`${d.day}T00:00:00`).toLocaleDateString(undefined, { day: "numeric" }),
              value: d.minutes,
              hint: d.day,
            }))}
          />
        </Card>
      </div>
      <Card>
        <SectionTitle title="Activity" sub="The last 12 weeks" action={<CalendarDays size={18} className="muted" />} />
        <Heatmap days={s.activity} label="Learning activity, last 12 weeks" />
      </Card>
      <Card>
        <SectionTitle title="Achievements" sub={`${unlocked} of ${s.achievements.length} unlocked`} />
        <div className="achievements">
          {s.achievements.map((a) => (
            <div key={a.key} className={`achievement ${a.unlocked ? "on" : ""}`}>
              <span className="achievement-icon">{a.unlocked ? <Award size={22} /> : <Lock size={18} />}</span>
              <b>{a.title}</b>
              <small>{a.description}</small>
            </div>
          ))}
        </div>
      </Card>
      {!!learning.data?.completed.length && (
        <section className="dash-section">
          <SectionTitle title="Recently finished" />
          <div className="lesson-grid">
            {learning.data.completed.map((l) => (
              <LessonCard key={l.id} lesson={l} showOwner={!l.mine} />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------- profile

export function ProfilePage() {
  const user = useUser();
  useTitle(user.name);
  const day = today();
  const stats = useResource(`stats:${day}`, () => v1.stats(day));
  const learning = useResource("learning", v1.learning);
  const s = stats.data;
  const role = user.role === "admin" ? "Administrator" : user.role === "educator" ? "Educator" : "Learner";
  return (
    <div className="page profile">
      <Card className="profile-head">
        <Avatar name={user.name} color={user.avatar} size={84} />
        <div className="profile-id">
          <h1>{user.name}</h1>
          <div className="profile-tags">
            <Badge tone={user.role === "learner" ? "neutral" : "brand"}>{role}</Badge>
            {user.verified && <Badge tone="success">Email confirmed</Badge>}
            <span className="muted">Member since {date(user.created_at)}</span>
          </div>
          <p className="profile-bio">
            {user.bio || <span className="muted">Add a short bio in settings so others know you.</span>}
          </p>
        </div>
        <Button variant="secondary" onClick={() => navigate("/settings")}>
          Edit profile
        </Button>
      </Card>
      {s && (
        <div className="stat-row">
          <Stat icon={Flame} label="Streak" value={`${s.streak} days`} sub={`Best ${s.best_streak}`} />
          <Stat icon={Clock} label="Minutes learned" value={s.minutes_total} tone="info" />
          <Stat icon={Sparkles} label="Lessons created" value={s.lessons_created} tone="neutral" />
          <Stat icon={Trophy} label="Lessons finished" value={s.lessons_completed} tone="success" />
        </div>
      )}
      {s && (
        <Card>
          <SectionTitle title="Achievements" />
          <div className="achievements compact">
            {s.achievements.map((a) => (
              <div key={a.key} className={`achievement ${a.unlocked ? "on" : ""}`} title={a.description}>
                <span className="achievement-icon">{a.unlocked ? <Award size={20} /> : <Lock size={16} />}</span>
                <b>{a.title}</b>
              </div>
            ))}
          </div>
        </Card>
      )}
      {!!learning.data?.created.length && (
        <section className="dash-section">
          <SectionTitle
            title="Lessons I made"
            action={
              <Link href="/lessons" className="muted-link">
                All
              </Link>
            }
          />
          <div className="lesson-grid">
            {learning.data.created.slice(0, 4).map((l) => (
              <LessonCard key={l.id} lesson={l} />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------- notifications

export function NotificationsPage() {
  useTitle("Notifications");
  const res = useResource("notifications", v1.notifications, 10_000);
  const items = res.data?.items ?? [];
  return (
    <div className="page narrow">
      <header className="page-head">
        <div>
          <h1>Notifications</h1>
        </div>
        {(res.data?.unread ?? 0) > 0 && (
          <Button
            variant="secondary"
            onClick={async () => {
              await v1.readAll().catch(() => undefined);
              invalidate("notifications");
            }}
          >
            Mark all as read
          </Button>
        )}
      </header>
      {res.loading && !res.data ? (
        <Spinner />
      ) : items.length === 0 ? (
        <EmptyState
          icon={Bell}
          title="Nothing here yet"
          text="We let you know when a lesson is ready or support replies."
        />
      ) : (
        <Card padded={false}>
          <ul className="notice-list">
            {items.map((n) => (
              <li key={n.id} className={n.read ? "" : "unread"}>
                <button
                  type="button"
                  onClick={() => {
                    if (!n.read) void v1.readNotice(n.id).then(() => invalidate("notifications"));
                    if (n.link) navigate(n.link);
                  }}
                >
                  <span className="notice-dot" aria-hidden="true" />
                  <span className="notice-text">
                    <b>{n.title}</b>
                    {n.body && <small>{n.body}</small>}
                  </span>
                  <span className="notice-time">{ago(n.created_at)}</span>
                </button>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
