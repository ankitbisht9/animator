/**
 * Administration → All lessons (ADR-057): every lesson, managed from one list. Share a lesson (private,
 * link only, public), feature public lessons on Explore's Featured shelf, remove and restore, one at a
 * time from its row or many at once from the bar over the chosen rows; find lessons by words (title,
 * topic, owner or id), status and subject, in the order wanted. The rules are the server's: a lesson
 * is published once it is ready, and only public lessons are featured.
 */
import {
  ArrowUpRight,
  ChevronDown,
  Clapperboard,
  FileCode2,
  Globe,
  Layers,
  Link2,
  Lock,
  type LucideIcon,
  RotateCcw,
  Star,
  StarOff,
  Trash2,
} from "lucide-react";
import { useEffect, useState } from "react";
import { type AdminLessonCounts, type Lesson, type LessonBulkAction, type Visibility, v1 } from "../api";
import { useAuth } from "../auth";
import { date, money, plural } from "../format";
import { useDebounced, useResource, useTitle } from "../hooks";
import { TrialBadge } from "../lesson/TrialBadge";
import { Link, navigate } from "../nav";
import { StatusBadge, SUBJECTS, subject } from "../ui/brand";
import { Avatar, Button, Card, EmptyState, SearchBox, SelectField, Tabs } from "../ui/core";
import { Confirm, useToast } from "../ui/overlay";
import { AllCheck, BulkBar, Pager, RowCheck, SkeletonRows, useSelection } from "../ui/table";
import { Forbidden } from "./Admin";

const PAGE = 25;

type TabKey = "all" | "public" | "featured" | "unlisted" | "private" | "deleted";
const TABS: { value: TabKey; label: string; icon: LucideIcon; count: keyof AdminLessonCounts }[] = [
  { value: "all", label: "All", icon: Layers, count: "all" },
  { value: "public", label: "Public", icon: Globe, count: "public" },
  { value: "featured", label: "Featured", icon: Star, count: "featured" },
  { value: "unlisted", label: "Link only", icon: Link2, count: "unlisted" },
  { value: "private", label: "Private", icon: Lock, count: "private" },
  { value: "deleted", label: "Removed", icon: Trash2, count: "deleted" },
];

/** What each tab asks the server for. */
function tabQuery(tab: TabKey): { visibility?: Visibility; featured?: boolean; deleted?: boolean } {
  if (tab === "deleted") return { deleted: true };
  if (tab === "featured") return { visibility: "public", featured: true };
  if (tab === "all") return {};
  return { visibility: tab };
}

const STATUSES = [
  { value: "", label: "Any status" },
  { value: "complete", label: "Ready" },
  { value: "queued,planning,generating", label: "Being made" },
  { value: "paused,failed,cancelled", label: "Stopped" },
];

const SORTS = [
  { value: "new", label: "Newest first" },
  { value: "old", label: "Oldest first" },
  { value: "views", label: "Most viewed" },
  { value: "cost", label: "Highest AI cost" },
  { value: "title", label: "Title A–Z" },
];

export const SHARING: Record<Visibility, { label: string; icon: LucideIcon; hint: string }> = {
  private: { label: "Private", icon: Lock, hint: "Only its owner and admins" },
  unlisted: { label: "Link only", icon: Link2, hint: "Anyone with the link" },
  public: { label: "Public", icon: Globe, hint: "Listed in Explore" },
};

interface BulkDef {
  action: LessonBulkAction;
  label: string;
  icon: LucideIcon;
  /** Whether the action would change this lesson (the button counts these). */
  applies: (l: Lesson) => boolean;
  /** Why none of the chosen lessons can take it. */
  none: string;
  done: (n: number) => string;
  danger?: boolean;
  /** A question before it runs (things leave Explore, or are removed), or null to run at once. */
  ask?: (ls: Lesson[]) => { title: string; text: string; label: string } | null;
}

const leaving = (ls: Lesson[]): string => {
  const listed = ls.filter((l) => l.visibility === "public").length;
  const featured = ls.filter((l) => l.featured).length;
  if (!listed) return "";
  return ` ${plural(listed, "lesson")} leave${listed === 1 ? "s" : ""} Explore${
    featured ? ` (${featured} stop${featured === 1 ? "s" : ""} being featured)` : ""
  }.`;
};

const BULK: BulkDef[] = [
  {
    action: "publish",
    label: "Publish",
    icon: Globe,
    applies: (l) => !l.deleted && l.visibility !== "public" && l.status === "complete",
    none: "None of the chosen lessons can be published: they are public already, or not ready yet.",
    done: (n) => `Published ${plural(n, "lesson")}: listed in Explore.`,
  },
  {
    action: "feature",
    label: "Feature",
    icon: Star,
    applies: (l) => !l.deleted && l.visibility === "public" && !l.featured,
    none: "Only public lessons can be featured: publish them first.",
    done: (n) => `Featured ${plural(n, "lesson")} on Explore.`,
  },
  {
    action: "unfeature",
    label: "Unfeature",
    icon: StarOff,
    applies: (l) => l.featured,
    none: "None of the chosen lessons is featured.",
    done: (n) => `${plural(n, "lesson")} no longer featured.`,
  },
  {
    action: "unlist",
    label: "Link only",
    icon: Link2,
    applies: (l) => !l.deleted && l.visibility !== "unlisted",
    none: "The chosen lessons are shared with a link already.",
    done: (n) => `${plural(n, "lesson")} now shared with a link only.`,
    ask: (ls) => {
      const away = leaving(ls);
      return away
        ? { title: "Share with a link only?", text: `Anyone with the link can still watch.${away}`, label: "Link only" }
        : null;
    },
  },
  {
    action: "private",
    label: "Make private",
    icon: Lock,
    applies: (l) => !l.deleted && l.visibility !== "private",
    none: "The chosen lessons are private already.",
    done: (n) => `${plural(n, "lesson")} made private.`,
    ask: (ls) => ({
      title: `Make ${plural(ls.length, "lesson")} private?`,
      text: `Only their owners (and admins) can watch them; links stop working.${leaving(ls)}`,
      label: "Make private",
    }),
  },
  {
    action: "delete",
    label: "Remove",
    icon: Trash2,
    danger: true,
    applies: (l) => !l.deleted,
    none: "The chosen lessons are removed already.",
    done: (n) => `Removed ${plural(n, "lesson")}. Restore them from Removed.`,
    ask: (ls) => ({
      title: `Remove ${plural(ls.length, "lesson")}?`,
      text: `They are hidden from their owners and from Explore.${leaving(ls)} You can restore them from the Removed tab.`,
      label: "Remove",
    }),
  },
  {
    action: "restore",
    label: "Restore",
    icon: RotateCcw,
    applies: (l) => l.deleted,
    none: "None of the chosen lessons is removed.",
    done: (n) => `Restored ${plural(n, "lesson")}: private to their owners again.`,
  },
];

type RowChange = { visibility?: Visibility; featured?: boolean; deleted?: boolean };

/** The row as the change would leave it (shown at once, put right by the server's answer). */
function changed(l: Lesson, body: RowChange): Lesson {
  const visibility = body.deleted ? "private" : (body.visibility ?? l.visibility);
  const featured = visibility === "public" && !body.deleted ? (body.featured ?? l.featured) : false;
  return { ...l, visibility, featured, deleted: body.deleted ?? l.deleted };
}

export function AdminLessons() {
  useTitle("All lessons");
  const { can } = useAuth();
  const toast = useToast();
  const [q, setQ] = useState("");
  const [tab, setTab] = useState<TabKey>("all");
  const [status, setStatus] = useState("");
  const [subjectKey, setSubjectKey] = useState("");
  const [sort, setSort] = useState("new");
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState<ReadonlySet<string>>(() => new Set());
  const [bulkBusy, setBulkBusy] = useState(false);
  const [asking, setAsking] = useState<{
    def: BulkDef;
    lessons: Lesson[];
    text: string;
    title: string;
    label: string;
  } | null>(null);
  const [removing, setRemoving] = useState<Lesson | null>(null);
  const term = useDebounced(q, 250);
  const allowed = can("lesson:any");
  const key = `admin:lessons:${term}:${tab}:${status}:${subjectKey}:${sort}:${offset}`;
  const res = useResource(allowed ? key : null, () =>
    v1.adminLessons({
      q: term,
      ...tabQuery(tab),
      status: status || undefined,
      subject: subjectKey || undefined,
      sort,
      offset,
    }),
  );
  const items = res.data?.items ?? [];
  const selection = useSelection(items.map((l) => l.id));
  const total = res.data?.total ?? 0;
  const counts = res.data?.counts;

  // A page emptied by a change (its last lessons removed or moved to another tab): the one before it.
  useEffect(() => {
    if (res.data && res.data.items.length === 0 && offset > 0) setOffset((o) => Math.max(0, o - PAGE));
  }, [res.data, offset]);

  if (!allowed) return <Forbidden />;

  const reset =
    <T,>(set: (v: T) => void) =>
    (v: T) => {
      set(v);
      setOffset(0);
    };
  const filtered = !!term || !!status || !!subjectKey;
  const clearFilters = () => {
    setQ("");
    setStatus("");
    setSubjectKey("");
    setOffset(0);
  };
  const mark = (id: string, on: boolean) =>
    setBusy((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });

  /** One lesson, from its row: shown changed at once, then as the server has it. */
  const change = async (l: Lesson, body: RowChange, done: string) => {
    mark(l.id, true);
    res.mutate((d) => d && { ...d, items: d.items.map((x) => (x.id === l.id ? changed(x, body) : x)) });
    try {
      const got = await v1.moderate(l.id, body);
      res.mutate((d) => d && { ...d, items: d.items.map((x) => (x.id === l.id ? { ...x, ...got } : x)) });
      toast.success(done);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      mark(l.id, false);
      void res.reload(); // the counts, and whether it still belongs in this tab
    }
  };

  const share = (l: Lesson, v: Visibility) => {
    if (v === l.visibility) return;
    const lost = l.featured && v !== "public" ? " It is no longer featured." : "";
    const done =
      v === "public"
        ? "Published: listed in Explore."
        : v === "unlisted"
          ? `Shared with a link only.${lost}`
          : `Made private.${lost}`;
    void change(l, { visibility: v }, done);
  };

  const runBulk = async (def: BulkDef, lessons: Lesson[]) => {
    setBulkBusy(true);
    try {
      const r = await v1.bulkLessons(
        lessons.map((l) => l.id),
        def.action,
      );
      const reasons = [...new Set(r.skipped.map((x) => x.reason))];
      const text = `${def.done(r.changed.length)}${
        r.skipped.length ? ` ${plural(r.skipped.length, "lesson")} skipped: ${reasons.join(" ")}` : ""
      }`;
      if (r.changed.length && !r.skipped.length) toast.success(text);
      else toast.info(text);
      selection.clear();
      setAsking(null);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBulkBusy(false);
      void res.reload();
    }
  };

  const chosen = items.filter((l) => selection.has(l.id));
  const bulkFor = (def: BulkDef) => {
    const lessons = chosen.filter(def.applies);
    if (!lessons.length) return;
    const q2 = def.ask?.(lessons);
    if (q2) setAsking({ def, lessons, ...q2 });
    else void runBulk(def, lessons);
  };
  const shownBulk = BULK.filter((d) => (tab === "deleted" ? d.action === "restore" : d.action !== "restore"));

  return (
    <div className="page admin admin-lessons">
      <header className="page-head">
        <div>
          <p className="page-kicker">Administration</p>
          <h1>All lessons</h1>
          <p className="page-sub">
            Share, publish, feature and remove lessons from one place. A lesson is published once it is ready; only
            public lessons can be featured on Explore.
          </p>
        </div>
      </header>

      <div className="toolbar">
        <Tabs
          label="Lessons"
          value={tab}
          onChange={reset(setTab)}
          tabs={TABS.map((t) => ({ value: t.value, label: t.label, icon: t.icon, count: counts?.[t.count] }))}
        />
      </div>
      <div className="toolbar">
        <SearchBox
          value={q}
          onChange={reset(setQ)}
          placeholder="Search title, topic, owner or id"
          label="Search all lessons"
        />
        <div className="toolbar-right">
          <SelectField
            aria-label="Status"
            value={status}
            onChange={(e) => reset(setStatus)(e.target.value)}
            options={STATUSES}
          />
          <SelectField
            aria-label="Subject"
            value={subjectKey}
            onChange={(e) => reset(setSubjectKey)(e.target.value)}
            options={[
              { value: "", label: "All subjects" },
              ...Object.entries(SUBJECTS).map(([k, s]) => ({ value: k, label: s.label })),
            ]}
          />
          <SelectField
            aria-label="Order"
            value={sort}
            onChange={(e) => reset(setSort)(e.target.value)}
            options={SORTS}
          />
        </div>
      </div>

      <Card padded={false} className="table-card">
        <div className="table-wrap sticky-head">
          <table className="table fixed admin-lessons-table">
            <colgroup>
              <col className="w-check" />
              <col />
              <col className="w-owner" />
              <col className="w-status" />
              <col className="w-share" />
              <col className="w-star" />
              <col className="w-views" />
              <col className="w-cost" />
              <col className="w-actions" />
            </colgroup>
            <thead>
              <tr>
                <th className="cell-check">
                  <AllCheck selection={selection} label="Choose every lesson on this page" />
                </th>
                <th>Lesson</th>
                <th>Owner</th>
                <th>Status</th>
                <th>Sharing</th>
                <th className="center">Featured</th>
                <th className="num">Views</th>
                <th className="num">AI cost</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {res.loading && !res.data ? (
                <SkeletonRows cols={9} />
              ) : (
                items.map((l) => {
                  const s = subject(l.subject);
                  const rowBusy = busy.has(l.id) || bulkBusy;
                  const canFeature = l.featured || (l.visibility === "public" && !l.deleted);
                  return (
                    <tr
                      key={l.id}
                      className={selection.has(l.id) ? "selected" : undefined}
                      aria-busy={rowBusy || undefined}
                    >
                      <td className="cell-check">
                        <RowCheck selection={selection} id={l.id} label={`Choose ${l.title}`} />
                      </td>
                      <td>
                        <div className="lesson-cell">
                          <span
                            className="lesson-tile"
                            style={{ color: s.ink, background: `color-mix(in srgb, ${s.ink} 13%, transparent)` }}
                            aria-hidden="true"
                          >
                            <s.icon size={17} />
                          </span>
                          <span className="lesson-cell-text">
                            <Link href={`/lessons/${l.id}`} className="lesson-cell-title" title={l.title}>
                              {l.title}
                            </Link>
                            <span className="lesson-cell-meta">
                              <TrialBadge model={l.trial_model} />
                              <span className="truncate" title={`${s.label} · ${date(l.created_at, true)} · ${l.id}`}>
                                {s.label} · {date(l.created_at)} · {l.id}
                              </span>
                            </span>
                          </span>
                        </div>
                      </td>
                      <td>
                        {l.owner ? (
                          <span className="owner-cell" title={l.owner.name}>
                            <Avatar name={l.owner.name} color={l.owner.avatar || undefined} size={26} />
                            <span className="truncate">{l.owner.name}</span>
                          </span>
                        ) : (
                          <span className="muted">Nobody</span>
                        )}
                      </td>
                      <td>
                        <StatusBadge status={l.status} />
                      </td>
                      <td className="cell-share">
                        {l.deleted ? (
                          <span className="share-static">
                            <Trash2 size={14} /> Removed
                          </span>
                        ) : (
                          <SharingPick lesson={l} disabled={rowBusy} onChange={(v) => share(l, v)} />
                        )}
                      </td>
                      <td className="center">
                        <button
                          type="button"
                          className={`star-toggle ${l.featured ? "on" : ""}`}
                          aria-pressed={l.featured}
                          aria-label={l.featured ? `Stop featuring ${l.title}` : `Feature ${l.title}`}
                          title={
                            l.featured
                              ? "Featured on Explore: click to stop featuring"
                              : canFeature
                                ? "Feature on Explore"
                                : l.deleted
                                  ? "Restore it first"
                                  : "Publish it to feature it"
                          }
                          disabled={rowBusy || !canFeature}
                          onClick={() =>
                            void change(
                              l,
                              { featured: !l.featured },
                              l.featured ? "No longer featured." : "Featured on Explore.",
                            )
                          }
                        >
                          <Star size={18} />
                        </button>
                      </td>
                      <td className="num">{l.views.toLocaleString()}</td>
                      <td className="num">{money(l.cost_usd)}</td>
                      <td className="cell-actions">
                        <span className="row-actions">
                          <button
                            type="button"
                            className="ui-icon-btn"
                            aria-label={`Open ${l.title}`}
                            title="Open the lesson"
                            onClick={() => navigate(`/lessons/${l.id}`)}
                          >
                            <ArrowUpRight size={17} />
                          </button>
                          {can("debug") && (
                            <button
                              type="button"
                              className="ui-icon-btn"
                              aria-label={`Pipeline view of ${l.title}`}
                              title="Pipeline view"
                              onClick={() => navigate(`/admin/raw/${l.id}`)}
                            >
                              <FileCode2 size={17} />
                            </button>
                          )}
                          {l.deleted ? (
                            <button
                              type="button"
                              className="ui-icon-btn"
                              aria-label={`Restore ${l.title}`}
                              title="Restore"
                              disabled={rowBusy}
                              onClick={() =>
                                void change(l, { deleted: false }, "Restored: private to its owner again.")
                              }
                            >
                              <RotateCcw size={17} />
                            </button>
                          ) : (
                            <button
                              type="button"
                              className="ui-icon-btn danger"
                              aria-label={`Remove ${l.title}`}
                              title="Remove"
                              disabled={rowBusy}
                              onClick={() => setRemoving(l)}
                            >
                              <Trash2 size={17} />
                            </button>
                          )}
                        </span>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
        {res.error && !res.data && <div className="table-empty">{res.error.message}</div>}
        {res.data && items.length === 0 && (
          <EmptyState
            icon={Clapperboard}
            title={filtered ? "No lesson matches" : tab === "featured" ? "Nothing featured yet" : "No lessons here"}
            text={
              filtered
                ? "Try other words, or clear the filters."
                : tab === "featured"
                  ? "Feature a public lesson (its star) to show it on Explore's Featured shelf."
                  : "Lessons appear here as people make them."
            }
            action={
              filtered ? (
                <Button variant="secondary" onClick={clearFilters}>
                  Clear filters
                </Button>
              ) : undefined
            }
          />
        )}
      </Card>
      <Pager offset={offset} size={PAGE} total={total} onChange={setOffset} />

      <BulkBar count={selection.count} noun={["lesson", "lessons"]} onClear={selection.clear}>
        {shownBulk.map((def) => {
          const n = chosen.filter(def.applies).length;
          return (
            <Button
              key={def.action}
              size="sm"
              variant={def.danger ? "danger" : "secondary"}
              icon={def.icon}
              disabled={n === 0 || bulkBusy}
              title={n === 0 ? def.none : undefined}
              onClick={() => bulkFor(def)}
            >
              {def.label}
              {n > 0 && n !== selection.count ? ` (${n})` : ""}
            </Button>
          );
        })}
      </BulkBar>

      <Confirm
        open={!!asking}
        onClose={() => setAsking(null)}
        onConfirm={() => asking && void runBulk(asking.def, asking.lessons)}
        busy={bulkBusy}
        danger={asking?.def.danger}
        title={asking?.title ?? ""}
        text={asking?.text}
        confirmLabel={asking?.label}
      />
      <Confirm
        open={!!removing}
        onClose={() => setRemoving(null)}
        onConfirm={() => {
          if (removing) void change(removing, { deleted: true }, "Lesson removed. Restore it from Removed.");
          setRemoving(null);
        }}
        danger
        title="Remove this lesson?"
        text={
          removing
            ? `“${removing.title}” is hidden from its owner and from Explore${removing.featured ? ", and no longer featured" : ""}. You can restore it from the Removed tab.`
            : undefined
        }
        confirmLabel="Remove"
      />
    </div>
  );
}

/** A lesson's sharing, changed from its row: private, link only, or public (once it is ready). */
function SharingPick({
  lesson,
  disabled,
  onChange,
}: {
  lesson: Lesson;
  disabled: boolean;
  onChange: (v: Visibility) => void;
}) {
  const v = lesson.visibility;
  const Icon = SHARING[v].icon;
  const ready = lesson.status === "complete";
  return (
    <span className={`share-pick vis-${v}`} title={SHARING[v].hint}>
      <Icon size={14} aria-hidden="true" />
      <select
        aria-label={`Sharing of ${lesson.title}`}
        value={v}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value as Visibility)}
      >
        <option value="private">{SHARING.private.label}</option>
        <option value="unlisted">{SHARING.unlisted.label}</option>
        <option value="public" disabled={!ready && v !== "public"}>
          {ready || v === "public" ? SHARING.public.label : "Public (once ready)"}
        </option>
      </select>
      <ChevronDown size={14} className="share-chevron" aria-hidden="true" />
    </span>
  );
}
