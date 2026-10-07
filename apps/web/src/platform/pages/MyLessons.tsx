/** The learner's own lessons: filter, search, sort, grid or list, and their actions. */
import { BookOpen, Ellipsis, ExternalLink, LayoutGrid, List, Plus, Share2, Trash } from "lucide-react";
import { useEffect, useState } from "react";
import { type Lesson, v1 } from "../api";
import { useAuth } from "../auth";
import { ago } from "../format";
import { useDebounced, useInterval, useResource, useTitle } from "../hooks";
import { refreshLessonLists, useSaveToggle } from "../lesson/actions";
import { LessonCard, LessonCardSkeleton, lessonLength } from "../lesson/LessonCard";
import { ShareDialog } from "../lesson/ShareDialog";
import { Link, navigate } from "../nav";
import { LessonCover, StatusBadge, subject } from "../ui/brand";
import { Button, EmptyState, IconButton, SearchBox, SelectField, Tabs } from "../ui/core";
import { Confirm, Menu, useToast } from "../ui/overlay";

type Filter = "all" | "ready" | "generating" | "stopped" | "shared";
const VIEW_KEY = "animator.lessons.view";

export function MyLessonsPage() {
  useTitle("My lessons");
  const { can } = useAuth();
  const toast = useToast();
  const [filter, setFilter] = useState<Filter>("all");
  const [q, setQ] = useState("");
  const [sort, setSort] = useState("new");
  const [view, setView] = useState<"grid" | "list">(() => {
    try {
      return window.localStorage.getItem(VIEW_KEY) === "list" ? "list" : "grid";
    } catch {
      return "grid";
    }
  });
  const [pages, setPages] = useState(1);
  const term = useDebounced(q, 250);
  const key = `lessons:${filter}:${term}:${sort}:${pages}`;
  const res = useResource(key, () => v1.myLessons({ status: filter, q: term, sort, limit: 24 * pages }));
  const [sharing, setSharing] = useState<Lesson | null>(null);
  const [deleting, setDeleting] = useState<Lesson | null>(null);
  const [busy, setBusy] = useState(false);
  const toggleSave = useSaveToggle();

  // biome-ignore lint/correctness/useExhaustiveDependencies: reset when these change
  useEffect(() => setPages(1), [filter, term, sort]);
  useEffect(() => {
    try {
      window.localStorage.setItem(VIEW_KEY, view);
    } catch {
      /* private mode */
    }
  }, [view]);
  const live = res.data?.items.some((l) => ["queued", "planning", "generating"].includes(l.status));
  useInterval(() => void res.reload(), live ? 5000 : null);

  const remove = async () => {
    if (!deleting) return;
    setBusy(true);
    try {
      await v1.deleteLesson(deleting.id);
      toast.success("Lesson deleted.");
      refreshLessonLists();
      setDeleting(null);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const items = res.data?.items ?? [];
  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>My lessons</h1>
          <p className="page-sub">Everything you have created, ready to watch, share or revisit.</p>
        </div>
        {can("lesson:create") && (
          <Button variant="primary" icon={Plus} onClick={() => navigate("/create")}>
            New lesson
          </Button>
        )}
      </header>

      <div className="toolbar">
        <Tabs
          label="Filter lessons"
          value={filter}
          onChange={setFilter}
          tabs={[
            { value: "all", label: "All" },
            { value: "ready", label: "Ready" },
            { value: "generating", label: "Being made" },
            { value: "stopped", label: "Stopped" },
            { value: "shared", label: "Shared" },
          ]}
        />
        <div className="toolbar-right">
          <SearchBox value={q} onChange={setQ} placeholder="Search your lessons" />
          <SelectField
            aria-label="Sort"
            value={sort}
            onChange={(e) => setSort(e.target.value)}
            options={[
              { value: "new", label: "Newest first" },
              { value: "recent", label: "Recently watched" },
              { value: "old", label: "Oldest first" },
              { value: "title", label: "Title A–Z" },
            ]}
          />
          <fieldset className="view-toggle">
            <legend className="sr-only">View</legend>
            <IconButton icon={LayoutGrid} label="Grid view" active={view === "grid"} onClick={() => setView("grid")} />
            <IconButton icon={List} label="List view" active={view === "list"} onClick={() => setView("list")} />
          </fieldset>
        </div>
      </div>

      {res.loading && !res.data ? (
        <div className="lesson-grid">
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <LessonCardSkeleton key={i} />
          ))}
        </div>
      ) : items.length === 0 ? (
        <EmptyState
          icon={BookOpen}
          title={term || filter !== "all" ? "No lessons match" : "No lessons yet"}
          text={
            term || filter !== "all"
              ? "Try another search or filter."
              : "Create your first animated lesson in a minute."
          }
          action={
            !term && filter === "all" && can("lesson:create") ? (
              <Button variant="primary" icon={Plus} onClick={() => navigate("/create")}>
                Create a lesson
              </Button>
            ) : undefined
          }
        />
      ) : view === "grid" ? (
        <div className="lesson-grid">
          {items.map((l) => (
            <LessonCard key={l.id} lesson={l} onToggleSave={toggleSave} />
          ))}
        </div>
      ) : (
        <ul className="lesson-rows" aria-label="Your lessons">
          {items.map((l) => (
            <li className="lesson-row" key={l.id}>
              <Link href={`/lessons/${l.id}`} className="lesson-row-main">
                <span className="lesson-row-cover">
                  <LessonCover lesson={l} compact />
                </span>
                <span className="lesson-row-text">
                  <b>{l.title}</b>
                  <small>
                    {subject(l.subject).label} · {lessonLength(l)} · created {ago(l.created_at)}
                  </small>
                </span>
              </Link>
              <span>
                <StatusBadge status={l.status} />
              </span>
              <span className="lesson-row-vis">
                {l.visibility === "private" ? "Private" : l.visibility === "unlisted" ? "Link" : "Public"}
              </span>
              <span className="lesson-row-progress">
                {l.progress?.completed ? "Completed" : l.progress ? `${Math.round(l.progress.percent * 100)}%` : "–"}
              </span>
              <span>
                <Menu
                  label="Lesson actions"
                  trigger={(p) => (
                    <button type="button" className="ui-icon-btn" aria-label="Lesson actions" {...p}>
                      <Ellipsis size={18} />
                    </button>
                  )}
                  items={[
                    { label: "Open", icon: ExternalLink, onSelect: () => navigate(`/lessons/${l.id}`) },
                    { label: "Share", icon: Share2, onSelect: () => setSharing(l) },
                    { label: "Delete", icon: Trash, danger: true, onSelect: () => setDeleting(l) },
                  ]}
                />
              </span>
            </li>
          ))}
        </ul>
      )}

      {res.data?.has_more && (
        <div className="load-more">
          <Button variant="secondary" loading={res.loading} onClick={() => setPages((p) => p + 1)}>
            Show more
          </Button>
        </div>
      )}

      {sharing && (
        <ShareDialog lesson={sharing} onClose={() => setSharing(null)} onChanged={() => refreshLessonLists()} />
      )}
      <Confirm
        open={!!deleting}
        onClose={() => setDeleting(null)}
        onConfirm={() => void remove()}
        title="Delete this lesson?"
        text={`“${deleting?.title ?? ""}” will disappear from your lessons and from anyone you shared it with.`}
        confirmLabel="Delete lesson"
        danger
        busy={busy}
      />
    </div>
  );
}
