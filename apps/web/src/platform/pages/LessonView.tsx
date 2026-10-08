/**
 * The heart of the platform: one lesson. The pipeline's own stream and player (unchanged logic),
 * dressed with the platform: what the lesson is, sharing, saving, progress that is remembered,
 * notes pinned to moments, doubts (the raised hand), and a finish worth celebrating.
 */
import {
  ArrowLeft,
  Bookmark,
  BookmarkCheck,
  Bug,
  Clock,
  Download,
  Ellipsis,
  Eye,
  Flag,
  Hand,
  Keyboard,
  Languages,
  Layers,
  ListChecks,
  LoaderCircle,
  MessageSquare,
  NotebookPen,
  Share2,
  Star,
  Trash,
  Users,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, type ExportJob } from "../../api";
import { DoubtFeature } from "../../doubts/DoubtFeature";
import { explain, progressLine, useLessonStream } from "../../lesson/useLessonStream";
import { Player, type PlayerControl } from "../../player/Player";
import { v1 } from "../api";
import { useAuth } from "../auth";
import { ago, clock, minutes } from "../format";
import { invalidate, useResource, useTitle } from "../hooks";
import { HttpError } from "../http";
import { refreshLessonLists } from "../lesson/actions";
import {
  CompleteCard,
  GenerationProgress,
  ProgressTracker,
  ResumePrompt,
  StageExtras,
  StarRating,
} from "../lesson/Extras";
import { type NotesHandle, NotesPanel } from "../lesson/NotesPanel";
import { ShareDialog } from "../lesson/ShareDialog";
import { Link, navigate } from "../nav";
import { audienceLabel, StatusBadge, subject } from "../ui/brand";
import { Alert, Avatar, Badge, Button, EmptyState, Spinner, Tabs } from "../ui/core";
import { Confirm, Menu, useToast } from "../ui/overlay";

type Tab = "overview" | "notes" | "doubts" | "shortcuts";

export function LessonView({ id }: { id: string }) {
  const { status } = useAuth();
  const signedIn = status === "signed-in";
  const meta = useResource(`lesson:${id}`, () => v1.lesson(id), 10_000);
  const allowed = meta.data !== undefined;
  // The stream opens only for a lesson this person may watch (the platform said so).
  return allowed ? (
    <LessonBody id={id} />
  ) : meta.error ? (
    <NotAvailable signedIn={signedIn} error={meta.error} />
  ) : (
    <div className="page lesson-loading" aria-busy="true">
      <Spinner size={30} />
    </div>
  );
}

function NotAvailable({ signedIn, error }: { signedIn: boolean; error: Error }) {
  const here = window.location.pathname;
  return (
    <div className="page narrow">
      <EmptyState
        icon={Layers}
        title={signedIn ? "This lesson is not available" : "Sign in to watch this lesson"}
        text={
          signedIn
            ? error instanceof HttpError && error.status === 404
              ? "It may be private, or it was deleted. Ask its owner to share it with a link."
              : error.message
            : "It may be private to its owner. Sign in to see the lessons you have access to."
        }
        action={
          signedIn ? (
            <Button variant="primary" onClick={() => navigate("/dashboard")}>
              Back to dashboard
            </Button>
          ) : (
            <Button variant="primary" onClick={() => navigate(`/login?next=${encodeURIComponent(here)}`)}>
              Sign in
            </Button>
          )
        }
      />
    </div>
  );
}

function LessonBody({ id }: { id: string }) {
  const { status, user, can } = useAuth();
  const signedIn = status === "signed-in";
  const toast = useToast();
  const meta = useResource(`lesson:${id}`, () => v1.lesson(id), 10_000);
  const info = meta.data;
  const { summary, lesson, pending, running, title, error, setError } = useLessonStream(id);
  useTitle(info?.title ?? title);
  const [tab, setTab] = useState<Tab>("overview");
  const [sharing, setSharing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [exportJob, setExportJob] = useState<ExportJob | null>(null);
  const controlRef = useRef<PlayerControl | null>(null);
  const notesRef = useRef<NotesHandle>(null);
  const playerHost = useRef<HTMLDivElement>(null);
  const owner = !!info?.can_edit;

  // Statuses change while a lesson is made: keep the platform's record fresh too.
  useEffect(() => {
    if (summary?.status) invalidate(`lesson:${id}`);
  }, [summary?.status, id]);

  useEffect(() => {
    if (exportJob?.state !== "running") return;
    const h = window.setInterval(() => {
      api.exportStatus(id).then(setExportJob, () => undefined);
    }, 2000);
    return () => window.clearInterval(h);
  }, [exportJob?.state, id]);

  // N: a note at this moment.
  useEffect(() => {
    if (!signedIn) return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.closest("input, textarea, select, [contenteditable]")) return;
      if (e.key === "n" && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        controlRef.current?.pause();
        setTab("notes");
        notesRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [signedIn]);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await fn();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const toggleSave = async () => {
    if (!info) return;
    try {
      if (info.saved) await v1.unsave(id);
      else await v1.save(id);
      meta.mutate((m) => m && { ...m, saved: !m.saved });
      refreshLessonLists();
      if (!info.saved) toast.success("Saved to your library.");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    }
  };

  const remove = async () => {
    setBusy(true);
    try {
      await v1.deleteLesson(id);
      refreshLessonLists();
      toast.success("Lesson deleted.");
      navigate("/lessons", { replace: true });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  const now = useCallback(() => controlRef.current?.t ?? 0, []);
  const onTime = useCallback((t: number) => void api.playhead(id, t).catch(() => undefined), [id]);
  const seek = useCallback((t: number) => {
    controlRef.current?.seek(t);
    document.querySelector(".player")?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, []);

  const s = subject(info?.subject ?? "general");
  const ready = summary?.status === "complete";
  const hint = summary?.error && can("debug") ? explain(summary.error) : null;
  const prefs = user?.preferences;

  return (
    <div className="lesson-page">
      <nav className="crumbs" aria-label="Breadcrumb">
        <button
          type="button"
          className="crumbs-back"
          onClick={() => (window.history.length > 1 ? window.history.back() : navigate("/lessons"))}
        >
          <ArrowLeft size={16} /> Back
        </button>
        <span className="crumbs-sep">/</span>
        {info?.mine ? <Link href="/lessons">My lessons</Link> : <Link href="/explore">Explore</Link>}
        <span className="crumbs-sep">/</span>
        <span className="crumbs-here">{info?.title ?? title}</span>
      </nav>

      <header className="lesson-hero">
        <div className="lesson-hero-text">
          <div className="lesson-tags">
            <span className="subject-chip" style={{ color: s.ink, background: s.tint }}>
              <s.icon size={14} /> {s.label}
            </span>
            {summary && summary.status !== "complete" && <StatusBadge status={summary.status} />}
            {info?.featured && <Badge tone="brand">Featured</Badge>}
            {info?.mine && info.visibility !== "private" && (
              <Badge tone="info">{info.visibility === "public" ? "Public" : "Shared by link"}</Badge>
            )}
          </div>
          <h1>{info?.title || title}</h1>
          {(summary?.subtitle || info?.subtitle) && <p className="lesson-sub">{summary?.subtitle || info?.subtitle}</p>}
          <div className="lesson-meta">
            <span>
              <Users size={15} /> {audienceLabel(info?.audience ?? summary?.request.audience ?? "")}
            </span>
            <span>
              <Clock size={15} />{" "}
              {lesson.duration > 0
                ? minutes(lesson.duration)
                : `${info?.duration_min ?? summary?.request.duration_min ?? 0} min`}
            </span>
            {lesson.scenes.length > 0 && (
              <span>
                <ListChecks size={15} /> {lesson.scenes.length} scenes
              </span>
            )}
            {(info?.language ?? "English") !== "English" && (
              <span>
                <Languages size={15} /> {info?.language}
              </span>
            )}
            {info && info.views > 0 && (
              <span>
                <Eye size={15} /> {info.views} learner{info.views === 1 ? "" : "s"}
              </span>
            )}
            {info?.rating != null && (
              <span>
                <Star size={15} /> {info.rating.toFixed(1)} ({info.rating_count})
              </span>
            )}
          </div>
          {info?.owner && !info.mine && (
            <div className="lesson-owner">
              <Avatar name={info.owner.name} size={26} /> by <b>{info.owner.name}</b>
            </div>
          )}
        </div>
        {signedIn && info && (
          <div className="lesson-actions">
            <Button
              variant={info.saved ? "soft" : "secondary"}
              icon={info.saved ? BookmarkCheck : Bookmark}
              onClick={() => void toggleSave()}
            >
              {info.saved ? "Saved" : "Save"}
            </Button>
            {owner && (
              <Button variant="secondary" icon={Share2} onClick={() => setSharing(true)}>
                Share
              </Button>
            )}
            <Menu
              label="More actions"
              trigger={(p) => (
                <button
                  type="button"
                  className="ui-btn ui-btn-secondary ui-btn-md icon-only"
                  aria-label="More actions"
                  {...p}
                >
                  <Ellipsis size={18} />
                </button>
              )}
              items={[
                {
                  label: exportJob?.state === "running" ? "Exporting video…" : "Download as video (MP4)",
                  icon: Download,
                  hidden: !owner || !ready,
                  onSelect: () => void act(async () => setExportJob(await api.startExport(id))),
                },
                {
                  label: "Report a problem",
                  icon: Flag,
                  onSelect: () =>
                    navigate(`/support?lesson=${id}&subject=${encodeURIComponent(`Problem with “${info.title}”`)}`),
                },
                {
                  label: "Pipeline view",
                  icon: Layers,
                  hidden: !can("debug"),
                  onSelect: () => navigate(`/admin/raw/${id}`),
                },
                { label: "Debug", icon: Bug, hidden: !can("debug"), onSelect: () => navigate(`/admin/debug/${id}`) },
                {
                  label: "Delete lesson",
                  icon: Trash,
                  danger: true,
                  hidden: !owner,
                  onSelect: () => setDeleting(true),
                },
              ]}
            />
          </div>
        )}
      </header>

      {summary && (!ready || summary.error) && (
        <GenerationProgress
          summary={summary}
          line={progressLine(summary)}
          hint={hint}
          canControl={owner}
          busy={busy}
          onCancel={() => void act(() => api.cancel(id))}
          onResume={() => void act(() => api.resume(id))}
        />
      )}
      {error && !summary && <Alert tone="danger">{error}</Alert>}
      {exportJob && (
        <Alert
          tone={exportJob.state === "failed" ? "danger" : exportJob.state === "done" ? "success" : "info"}
          action={
            exportJob.state === "done" ? (
              <a className="ui-btn ui-btn-primary ui-btn-sm" href={`/api/generations/${id}/export/video`} download>
                <Download size={15} /> Download
              </a>
            ) : exportJob.state === "running" ? (
              <LoaderCircle className="ui-spin" size={18} />
            ) : undefined
          }
        >
          {exportJob.state === "running" && <>Rendering the video… {exportJob.log.at(-1)}</>}
          {exportJob.state === "done" && <>Your video is ready.</>}
          {exportJob.state === "failed" && <>The export failed: {exportJob.error}</>}
        </Alert>
      )}

      {summary ? (
        <div className="lesson-player" ref={playerHost}>
          <Player
            lesson={lesson}
            pending={pending}
            generating={running}
            onTime={running && owner ? onTime : undefined}
            initial={prefs ? { rate: prefs.playback_rate, captions: prefs.captions, voice: prefs.voice } : undefined}
            addon={(control) => {
              controlRef.current = control;
              // As before the platform: only the doubt add-on renders with every frame.
              return signedIn ? (
                <DoubtFeature
                  control={control}
                  lessonId={id}
                  language={summary.request.language || "English"}
                  audience={summary.request.audience}
                  durationMin={summary.request.duration_min}
                />
              ) : null;
            }}
          />
          <StageExtras controlRef={controlRef} host={playerHost}>
            {(control) => (
              <>
                {signedIn && <ProgressTracker control={control} lessonId={id} total={lesson.duration} />}
                {signedIn && info?.progress && !info.progress.completed && (
                  <ResumePrompt control={control} position={info.progress.position_s} />
                )}
                {!running && ready && lesson.scenes.length > 0 && (
                  <CompleteCard
                    control={control}
                    lessonId={id}
                    myStars={info?.my_rating?.stars ?? 0}
                    signedIn={signedIn}
                    canRate={signedIn && !!info?.can_rate}
                    onNotes={() => {
                      setTab("notes");
                      notesRef.current?.focus();
                    }}
                  />
                )}
              </>
            )}
          </StageExtras>
        </div>
      ) : (
        <div className="lesson-player placeholder" aria-busy="true">
          <Spinner size={28} />
          <span>Connecting to the lesson…</span>
        </div>
      )}

      <section className="lesson-below">
        <Tabs
          label="Lesson sections"
          value={tab}
          onChange={setTab}
          tabs={[
            { value: "overview", label: "Overview", icon: ListChecks },
            ...(signedIn
              ? [
                  { value: "notes" as const, label: "My notes", icon: NotebookPen },
                  { value: "doubts" as const, label: "My doubts", icon: MessageSquare },
                ]
              : []),
            { value: "shortcuts", label: "Shortcuts", icon: Keyboard },
          ]}
        />
        <div className="lesson-tab">
          {tab === "overview" && (
            <Overview
              topic={info?.topic ?? summary?.request.topic ?? ""}
              chapters={summary?.chapters ?? []}
              scenes={lesson.scenes.map((sc) => ({ title: sc.title, start: sc.start, duration: sc.duration }))}
              onSeek={seek}
              created={info?.created_at}
              canRate={signedIn && ready && !!info?.can_rate}
              lessonId={id}
              myStars={info?.my_rating?.stars ?? 0}
            />
          )}
          {tab === "notes" && signedIn && <NotesPanel ref={notesRef} lessonId={id} now={now} seek={seek} />}
          {tab === "doubts" && signedIn && <Doubts lessonId={id} />}
          {tab === "shortcuts" && <Shortcuts />}
        </div>
      </section>

      {!signedIn && (
        <div className="signin-nudge">
          <Hand size={18} />
          <span>Sign in to ask the tutor doubts, take notes and keep your progress.</span>
          <Button
            variant="primary"
            size="sm"
            onClick={() => navigate(`/login?next=${encodeURIComponent(`/lessons/${id}`)}`)}
          >
            Sign in
          </Button>
        </div>
      )}

      {sharing && info && (
        <ShareDialog
          lesson={info}
          onClose={() => setSharing(false)}
          onChanged={(l) => {
            meta.mutate(() => l);
            refreshLessonLists();
          }}
        />
      )}
      <Confirm
        open={deleting}
        onClose={() => setDeleting(false)}
        onConfirm={() => void remove()}
        title="Delete this lesson?"
        text="It will disappear from your lessons and from anyone you shared it with."
        confirmLabel="Delete lesson"
        danger
        busy={busy}
      />
    </div>
  );
}

function Overview({
  topic,
  chapters,
  scenes,
  onSeek,
  created,
  canRate,
  lessonId,
  myStars,
}: {
  topic: string;
  chapters: { id: string; title: string }[];
  scenes: { title: string; start: number; duration: number }[];
  onSeek: (t: number) => void;
  created?: number;
  /** Signed in, the lesson ready, and not one's own. */
  canRate: boolean;
  lessonId: string;
  myStars: number;
}) {
  const toast = useToast();
  const [stars, setStars] = useState(myStars);
  useEffect(() => setStars(myStars), [myStars]);
  return (
    <div className="overview">
      <div className="overview-main">
        <h3>What this lesson explains</h3>
        <p className="overview-topic">“{topic}”</p>
        {chapters.length > 0 && (
          <>
            <h3>Chapters</h3>
            <ol className="overview-chapters">
              {chapters.map((c) => (
                <li key={c.id}>{c.title}</li>
              ))}
            </ol>
          </>
        )}
        {scenes.length > 0 && (
          <>
            <h3>Scenes</h3>
            <ol className="overview-scenes">
              {scenes.map((sc, i) => (
                <li key={sc.start}>
                  <button type="button" onClick={() => onSeek(sc.start + 0.001)}>
                    <span className="overview-scene-n">{i + 1}</span>
                    <span className="overview-scene-title">{sc.title}</span>
                    <span className="overview-scene-time">{clock(sc.start)}</span>
                  </button>
                </li>
              ))}
            </ol>
          </>
        )}
      </div>
      <aside className="overview-side">
        {canRate && (
          <div className="overview-rate">
            <h3>Rate this lesson</h3>
            <StarRating
              value={stars}
              onChange={async (n) => {
                setStars(n);
                try {
                  await v1.rate(lessonId, n);
                  invalidate(`lesson:${lessonId}`);
                  toast.success("Thanks for rating!");
                } catch (e) {
                  toast.error(e instanceof Error ? e.message : String(e));
                }
              }}
            />
          </div>
        )}
        <div className="overview-tip">
          <Hand size={18} />
          <div>
            <b>Stuck? Raise your hand.</b>
            <p>
              Press the hand on the player (or D) and ask by voice or text. The tutor answers on the lesson's own
              picture.
            </p>
          </div>
        </div>
        {created && <p className="muted small">Created {ago(created)}</p>}
      </aside>
    </div>
  );
}

function Doubts({ lessonId }: { lessonId: string }) {
  const res = useResource(`doubts:${lessonId}`, () => v1.lessonDoubts(lessonId), 5000);
  if (res.loading && !res.data) return <Spinner />;
  const items = res.data ?? [];
  if (!items.length)
    return (
      <EmptyState
        icon={Hand}
        title="No doubts yet"
        text="Whenever something is unclear, press the hand on the player (or D) and ask. Your questions show up here."
      />
    );
  return (
    <ul className="doubt-list">
      {items.map((d) => (
        <li key={`${d.session_id}-${d.turn}`}>
          <MessageSquare size={16} />
          <div>
            <b>{d.question}</b>
            <small>
              {d.title ? `${d.title} · ` : ""}
              {ago(d.created_at)}
              {d.turn > 1 ? ` · follow-up ${d.turn}` : ""}
            </small>
          </div>
        </li>
      ))}
    </ul>
  );
}

const SHORTCUTS: [string, string][] = [
  ["Space", "Play or pause"],
  ["← / →", "Back or forward 5 seconds"],
  ["D", "Raise your hand (ask a doubt)"],
  ["N", "Write a note at this moment"],
  ["C", "Captions on or off"],
  ["M", "Voice on or off"],
  ["F", "Full screen"],
  ["Ctrl K", "Search lessons"],
];

function Shortcuts() {
  return (
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
  );
}
