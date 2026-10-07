/** Help center (directions of use, FAQ, shortcuts), support tickets, legal pages, not found. */
import {
  ArrowLeft,
  BookOpen,
  CircleQuestionMark,
  Compass,
  Hand,
  Keyboard,
  LifeBuoy,
  Map as MapIcon,
  MessageSquare,
  Send,
  Shield,
  Sparkles,
  User as UserIcon,
  Wrench,
} from "lucide-react";
import { type FormEvent, useEffect, useMemo, useState } from "react";
import { v1 } from "../api";
import { useAuth } from "../auth";
import { ago, date } from "../format";
import { useResource, useTitle } from "../hooks";
import { Link, navigate, useLocation } from "../nav";
import {
  Alert,
  Badge,
  Button,
  Card,
  EmptyState,
  SearchBox,
  SelectField,
  Spinner,
  TextArea,
  TextField,
} from "../ui/core";
import { useToast } from "../ui/overlay";

interface Article {
  q: string;
  a: string;
}
interface Topic {
  id: string;
  title: string;
  icon: typeof BookOpen;
  intro: string;
  articles: Article[];
}

const TOPICS: Topic[] = [
  {
    id: "start",
    title: "Getting started",
    icon: Sparkles,
    intro: "Your first lesson in three steps.",
    articles: [
      {
        q: "How do I create my first lesson?",
        a: "Open Create lesson (or press the red Create button at the top). Type what you want to understand, choose who it is for, the length and the narration language, then press Create lesson. The page opens straight away and the first scene plays as soon as it is ready, usually in a minute or two.",
      },
      {
        q: "What makes a good topic?",
        a: "One clear question works best: “Why does ice float?”, “How does binary search work?”. Add an example you like in the style notes (“use a cricket ball”). Pick the level of the person who will watch.",
      },
      {
        q: "What do the levels mean?",
        a: "The level sets the words, the pace and how much is assumed: Primary school (ages 6–10), Middle school, High school, University, or Curious adult (no background needed).",
      },
      {
        q: "How many lessons can I create?",
        a: "Learner accounts can create a few lessons a day and educators more; your page shows how many you have left. The limit resets over a rolling 24 hours.",
      },
    ],
  },
  {
    id: "watching",
    title: "Watching and learning",
    icon: BookOpen,
    intro: "The player, progress, notes and checks.",
    articles: [
      {
        q: "Can I pause and look closer?",
        a: "Yes. Pause anywhere and hover any part of the picture: its name, what it is and its values appear. Use the scene list on the right to jump between scenes, or the transcript to jump to a sentence.",
      },
      {
        q: "Does it remember where I stopped?",
        a: "Yes. Your place is saved as you watch. When you come back, the lesson offers to continue from there. Finished lessons count towards your progress and streak.",
      },
      {
        q: "How do notes work?",
        a: "Press N (or open My notes under the player) to write a note pinned to the current moment. Click a note's time to jump back to that moment. All your notes are in Library → Notes.",
      },
      {
        q: "What are the quick checks?",
        a: "Some lessons pause for a short question. Pick an answer to see whether it is right and why, then continue.",
      },
      {
        q: "Can I change the speed, captions or voice?",
        a: "Use the controls under the picture, or set your defaults in Settings → Playback.",
      },
    ],
  },
  {
    id: "doubts",
    title: "Asking doubts",
    icon: Hand,
    intro: "Raise your hand mid-lesson.",
    articles: [
      {
        q: "How do I ask a doubt?",
        a: "Press the hand button on the player (or D). The lesson pauses and the tutor greets you. Type your question or tap the microphone and say it. The answer is spoken, captioned and drawn on the lesson's own picture or on a board.",
      },
      {
        q: "What if I still do not get it?",
        a: "Press “Still not clear” or “Give an example”: each follow-up is simpler and takes a new angle. “Got it, continue” closes the doubt and the lesson carries on from where you paused.",
      },
      {
        q: "Is there a limit?",
        a: "To keep the service fair, each account has a daily number of doubt answers. It is generous for normal learning.",
      },
    ],
  },
  {
    id: "sharing",
    title: "Sharing and Explore",
    icon: Compass,
    intro: "Lessons for your class and the community.",
    articles: [
      {
        q: "How do I share a lesson?",
        a: "Open the lesson and press Share. Choose “Anyone with the link” and copy the link. People can watch it without an account; they sign in to ask doubts or take notes.",
      },
      {
        q: "How do lessons get into Explore?",
        a: "Educators can publish a finished lesson to Explore (Share → Everyone). Our team features the best ones.",
      },
      {
        q: "Can I download a lesson?",
        a: "Owners can download a finished lesson as an MP4 video from the lesson's ⋯ menu.",
      },
    ],
  },
  {
    id: "account",
    title: "Account and security",
    icon: Shield,
    intro: "Passwords, devices and your data.",
    articles: [
      {
        q: "I forgot my password.",
        a: "On the sign-in page choose “Forgot password?”. We email a link that works for one hour. For your safety, resetting signs you out everywhere.",
      },
      {
        q: "How do I see where I am signed in?",
        a: "Settings → Security lists your devices. Sign out of any of them, or of all others at once.",
      },
      {
        q: "How do I download or delete my data?",
        a: "Settings → Privacy & data. “Download my data” gives you everything we keep about you. “Delete my account” removes it for good.",
      },
    ],
  },
  {
    id: "troubleshooting",
    title: "Troubleshooting",
    icon: Wrench,
    intro: "When something does not look right.",
    articles: [
      {
        q: "My lesson stopped before finishing.",
        a: "Open it and press Resume. If it stops again, use ⋯ → Report a problem: the ticket includes the lesson, so we can look at exactly what happened.",
      },
      {
        q: "I cannot hear the voice.",
        a: "Check the speaker button under the picture (M toggles it) and your device volume. Some lessons have captions only.",
      },
      {
        q: "The microphone does not work for doubts.",
        a: "Allow microphone access for this site in your browser. Chrome, Edge and Safari recognise speech directly; you can always type instead.",
      },
    ],
  },
];

const SHORTCUTS: [string, string][] = [
  ["Space", "Play or pause"],
  ["← / →", "Back or forward 5 seconds"],
  ["D", "Raise your hand (ask a doubt)"],
  ["N", "Write a note at this moment"],
  ["C", "Captions on or off"],
  ["M", "Voice on or off"],
  ["F", "Full screen"],
  ["Ctrl K or /", "Search lessons"],
  ["Esc", "Close a dialog or the doubt screen"],
];

export function HelpPage() {
  useTitle("Help center");
  const { hash } = useLocation();
  const [q, setQ] = useState("");
  const term = q.trim().toLowerCase();
  const results = useMemo(
    () =>
      term.length < 2
        ? []
        : TOPICS.flatMap((t) =>
            t.articles.filter((a) => `${a.q} ${a.a}`.toLowerCase().includes(term)).map((a) => ({ topic: t, ...a })),
          ),
    [term],
  );
  useEffect(() => {
    if (hash) document.getElementById(hash.slice(1))?.scrollIntoView({ behavior: "smooth" });
  }, [hash]);
  return (
    <div className="page help">
      <section className="help-hero">
        <CircleQuestionMark size={28} />
        <h1>How can we help?</h1>
        <SearchBox value={q} onChange={setQ} placeholder="Search the help center" />
      </section>

      {term.length >= 2 ? (
        <section className="help-results">
          {results.length === 0 ? (
            <EmptyState
              icon={CircleQuestionMark}
              title="No answers found"
              text="Try other words, or ask our team."
              action={
                <Button variant="primary" icon={LifeBuoy} onClick={() => navigate("/support")}>
                  Contact support
                </Button>
              }
            />
          ) : (
            results.map((r) => (
              <details key={r.q} open>
                <summary>
                  <Badge tone="neutral">{r.topic.title}</Badge> {r.q}
                </summary>
                <p>{r.a}</p>
              </details>
            ))
          )}
        </section>
      ) : (
        <>
          <div className="help-topics">
            {TOPICS.map((t) => (
              <a key={t.id} href={`#${t.id}`} className="help-topic">
                <t.icon size={22} />
                <b>{t.title}</b>
                <small>{t.intro}</small>
              </a>
            ))}
            <a href="#shortcuts" className="help-topic">
              <Keyboard size={22} />
              <b>Keyboard shortcuts</b>
              <small>Learn faster with the keyboard.</small>
            </a>
          </div>
          <Card className="help-guide">
            <MapIcon size={22} />
            <div>
              <h2>A quick tour</h2>
              <ol>
                <li>
                  <b>Dashboard</b>: continue where you left off, your weekly goal and streak.
                </li>
                <li>
                  <b>Create lesson</b>: describe a topic, choose level, length and language.
                </li>
                <li>
                  <b>My lessons</b>: everything you made; share or delete from here.
                </li>
                <li>
                  <b>Explore</b>: lessons educators published for everyone.
                </li>
                <li>
                  <b>Library</b>: saved lessons, your notes and your doubts.
                </li>
                <li>
                  <b>Progress</b>: minutes, streaks, achievements.
                </li>
              </ol>
            </div>
          </Card>
          {TOPICS.map((t) => (
            <section key={t.id} id={t.id} className="help-section">
              <h2>
                <t.icon size={20} /> {t.title}
              </h2>
              {t.articles.map((a) => (
                <details key={a.q}>
                  <summary>{a.q}</summary>
                  <p>{a.a}</p>
                </details>
              ))}
            </section>
          ))}
          <section id="shortcuts" className="help-section">
            <h2>
              <Keyboard size={20} /> Keyboard shortcuts
            </h2>
            <dl className="shortcuts">
              {SHORTCUTS.map(([k, v]) => (
                <div key={k}>
                  <dt>
                    <kbd>{k}</kbd>
                  </dt>
                  <dd>{v}</dd>
                </div>
              ))}
            </dl>
          </section>
        </>
      )}
      <Card className="help-contact">
        <LifeBuoy size={26} />
        <div>
          <h2>Still need help?</h2>
          <p>Our team answers every ticket, usually within a day.</p>
        </div>
        <Button variant="primary" icon={MessageSquare} onClick={() => navigate("/support")}>
          Contact support
        </Button>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------- support

const CATEGORIES = [
  { value: "technical", label: "Something is not working" },
  { value: "lesson", label: "A lesson's content" },
  { value: "account", label: "My account" },
  { value: "billing", label: "Plans and limits" },
  { value: "feedback", label: "Feedback or an idea" },
  { value: "other", label: "Something else" },
];

const TICKET_TONE: Record<string, "info" | "warning" | "success" | "neutral"> = {
  open: "info",
  pending: "warning",
  resolved: "success",
  closed: "neutral",
};
const TICKET_LABEL: Record<string, string> = {
  open: "Open",
  pending: "Awaiting you",
  resolved: "Resolved",
  closed: "Closed",
};

export function SupportPage() {
  useTitle("Support");
  const { status } = useAuth();
  const { search } = useLocation();
  const toast = useToast();
  const tickets = useResource(status === "signed-in" ? "tickets" : null, v1.tickets);
  const [category, setCategory] = useState(search.get("lesson") ? "lesson" : "technical");
  const [subject, setSubject] = useState(search.get("subject") ?? "");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lessonId = search.get("lesson");

  if (status !== "signed-in") {
    return (
      <div className="page narrow">
        <EmptyState
          icon={LifeBuoy}
          title="Sign in to contact support"
          text="So we can see your lessons and answer you. Many answers are already in the help center."
          action={
            <div className="row-gap">
              <Button variant="primary" onClick={() => navigate("/login?next=/support")}>
                Sign in
              </Button>
              <Button variant="secondary" onClick={() => navigate("/help")}>
                Help center
              </Button>
            </div>
          }
        />
      </div>
    );
  }

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const t = await v1.openTicket({
        subject: subject.trim(),
        category,
        message: message.trim(),
        lesson_id: lessonId,
      });
      toast.success(`Ticket ${t.ref} sent. We will reply here and notify you.`);
      setSubject("");
      setMessage("");
      void tickets.reload();
      navigate(`/support/${t.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page support">
      <header className="page-head">
        <div>
          <h1>Support</h1>
          <p className="page-sub">
            Tell us what happened. Quick answers are often in the <Link href="/help">help center</Link>.
          </p>
        </div>
      </header>
      <div className="support-grid">
        <Card>
          <h2 className="card-h">New request</h2>
          <form className="settings-form" onSubmit={submit}>
            {error && <Alert tone="danger">{error}</Alert>}
            {lessonId && <Alert tone="info">About lesson {lessonId}: we will look at it directly.</Alert>}
            <SelectField
              label="What is it about?"
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              options={CATEGORIES}
            />
            <TextField
              label="Subject"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              maxLength={200}
              placeholder="A short summary"
              required
            />
            <TextArea
              label="Details"
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              rows={6}
              maxLength={5000}
              placeholder="What did you do, what did you expect, what happened instead?"
              hint={`${message.length}/5000 · at least 10 characters`}
              required
            />
            <div className="form-actions">
              <Button
                type="submit"
                variant="primary"
                icon={Send}
                loading={busy}
                disabled={subject.trim().length < 3 || message.trim().length < 10}
              >
                Send to support
              </Button>
            </div>
          </form>
        </Card>
        <Card>
          <h2 className="card-h">Your requests</h2>
          {tickets.loading && !tickets.data ? (
            <Spinner />
          ) : !tickets.data?.length ? (
            <p className="muted">No requests yet.</p>
          ) : (
            <ul className="ticket-list">
              {tickets.data.map((t) => (
                <li key={t.id}>
                  <Link href={`/support/${t.id}`}>
                    <span>
                      <b>{t.subject}</b>
                      <small>
                        {t.ref} · updated {ago(t.updated_at)}
                      </small>
                    </span>
                    <Badge tone={TICKET_TONE[t.status] ?? "neutral"}>{TICKET_LABEL[t.status] ?? t.status}</Badge>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}

export function TicketThread({ id, staff = false }: { id: string; staff?: boolean }) {
  useTitle("Support request");
  const toast = useToast();
  const res = useResource(`ticket:${id}`, () => v1.ticket(id), 5000);
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const t = res.data;
  const send = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      const updated = await v1.reply(id, body.trim());
      res.mutate(() => updated);
      setBody("");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };
  const triage = async (patch: { status?: string; priority?: string }) => {
    try {
      await v1.triage(id, patch);
      void res.reload();
      toast.success("Updated.");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    }
  };
  if (!t)
    return (
      <div className="page narrow">{res.error ? <Alert tone="danger">{res.error.message}</Alert> : <Spinner />}</div>
    );
  return (
    <div className="page narrow ticket">
      <Link href={staff ? "/admin/support" : "/support"} className="crumbs-back">
        <ArrowLeft size={16} /> {staff ? "Support queue" : "Support"}
      </Link>
      <header className="page-head">
        <div>
          <p className="page-kicker">
            {t.ref} · {CATEGORIES.find((c) => c.value === t.category)?.label ?? t.category}
          </p>
          <h1>{t.subject}</h1>
          <div className="row-gap">
            <Badge tone={TICKET_TONE[t.status] ?? "neutral"}>{TICKET_LABEL[t.status] ?? t.status}</Badge>
            {staff && (
              <Badge tone={t.priority === "urgent" || t.priority === "high" ? "danger" : "neutral"}>
                {t.priority} priority
              </Badge>
            )}
            <span className="muted">Opened {date(t.created_at, true)}</span>
            {t.lesson_id && <Link href={`/lessons/${t.lesson_id}`}>Open the lesson</Link>}
          </div>
        </div>
        {staff && (
          <div className="row-gap">
            <SelectField
              aria-label="Status"
              value={t.status}
              onChange={(e) => void triage({ status: e.target.value })}
              options={["open", "pending", "resolved", "closed"].map((v) => ({
                value: v,
                label: TICKET_LABEL[v] ?? v,
              }))}
            />
            <SelectField
              aria-label="Priority"
              value={t.priority}
              onChange={(e) => void triage({ priority: e.target.value })}
              options={["low", "normal", "high", "urgent"].map((v) => ({
                value: v,
                label: `${v[0]?.toUpperCase()}${v.slice(1)}`,
              }))}
            />
          </div>
        )}
      </header>
      {staff && (
        <p className="muted">
          From <b>{t.name ?? "a learner"}</b> ({t.email})
        </p>
      )}
      <ol className="thread">
        {(t.messages ?? []).map((m) => (
          <li key={m.id} className={m.staff ? "staff" : "learner"}>
            <div className="thread-avatar">{m.staff ? <LifeBuoy size={16} /> : <UserIcon size={16} />}</div>
            <div className="thread-bubble">
              <div className="thread-meta">
                <b>{m.staff ? (staff ? m.author : "Support team") : staff ? (t.name ?? "Learner") : "You"}</b>
                <span>{ago(m.created_at)}</span>
              </div>
              <p>{m.body}</p>
            </div>
          </li>
        ))}
      </ol>
      {t.status !== "closed" || !staff ? (
        <Card>
          <form onSubmit={send} className="thread-reply">
            <TextArea
              label={staff ? "Reply to the learner" : "Add a reply"}
              value={body}
              onChange={(e) => setBody(e.target.value)}
              rows={3}
              maxLength={5000}
            />
            <div className="form-actions">
              {!staff && t.status !== "closed" && (
                <Button
                  variant="ghost"
                  onClick={async () => {
                    await v1.closeTicket(id);
                    void res.reload();
                  }}
                >
                  Close request
                </Button>
              )}
              <Button type="submit" variant="primary" icon={Send} loading={busy} disabled={!body.trim()}>
                Send
              </Button>
            </div>
          </form>
        </Card>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------- legal and not found

export function LegalPage({ kind }: { kind: "terms" | "privacy" }) {
  useTitle(kind === "terms" ? "Terms of use" : "Privacy policy");
  return (
    <div className="page narrow legal">
      <h1>{kind === "terms" ? "Terms of use" : "Privacy policy"}</h1>
      <Alert tone="warning">This is a starting template. Have it reviewed by a legal professional before launch.</Alert>
      {kind === "terms" ? (
        <>
          <h2>Using the service</h2>
          <p>
            You may use the platform to create, watch and share educational lessons. Do not use it to create unlawful,
            harmful or misleading content, or to attempt to access other people's accounts or data.
          </p>
          <h2>Your content</h2>
          <p>
            Lessons you create are yours to share. By publishing a lesson to Explore you allow other learners to watch
            it on the platform. We may remove content that breaks these terms.
          </p>
          <h2>AI-generated material</h2>
          <p>Lessons and answers are produced with the help of AI and may contain mistakes. Check important facts.</p>
          <h2>Accounts</h2>
          <p>Keep your password safe. We may suspend accounts that misuse the service.</p>
        </>
      ) : (
        <>
          <h2>What we collect</h2>
          <p>
            Your name, email and a securely hashed password; the lessons you create; your learning progress, notes,
            ratings and doubts; support messages; and security records (sign-ins, devices) to protect your account.
          </p>
          <h2>How we use it</h2>
          <p>
            To run the service, personalise suggestions, keep accounts secure and answer support requests. We do not
            sell your data.
          </p>
          <h2>Your choices</h2>
          <p>
            Download a copy of your data or delete your account at any time in Settings → Privacy & data. Private
            lessons, notes and doubts are visible only to you (and to administrators when needed to provide support).
          </p>
          <h2>Cookies</h2>
          <p>We use only the cookies needed to keep you signed in and protect your session.</p>
        </>
      )}
    </div>
  );
}

export function NotFoundPage() {
  useTitle("Page not found");
  const { status } = useAuth();
  return (
    <div className="page narrow not-found">
      <div className="not-found-code">404</div>
      <h1>This page wandered off</h1>
      <p className="muted">The link may be old, or the page moved.</p>
      <div className="row-gap center">
        <Button variant="primary" onClick={() => navigate(status === "signed-in" ? "/dashboard" : "/")}>
          {status === "signed-in" ? "Go to dashboard" : "Go home"}
        </Button>
        <Button variant="secondary" onClick={() => navigate("/help")}>
          Help center
        </Button>
      </div>
    </div>
  );
}
