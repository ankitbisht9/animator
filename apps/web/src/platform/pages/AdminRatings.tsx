/** Ratings and Explore (ADR-053): how lessons are rated (well and poorly), which ones Explore lists and
 * why, and the hand curators have over it: add any lesson, keep any out, or leave it to the rule. Lessons
 * that need work can be flagged for the team. Each action needs its own permission (the API checks). */
import {
  Compass,
  EyeOff,
  Flag,
  MessageSquareText,
  Pin,
  RotateCcw,
  Star,
  ThumbsDown,
  ThumbsUp,
  TriangleAlert,
  Wrench,
} from "lucide-react";
import { useEffect, useState } from "react";
import {
  type ExploreRule,
  type RatedLesson,
  type RatingsSummary,
  type RatingsView,
  type ReviewStatus,
  v1,
} from "../api";
import { useAuth } from "../auth";
import { ago, date, plural } from "../format";
import { invalidate, useDebounced, useResource, useTitle } from "../hooks";
import { Link } from "../nav";
import { SUBJECTS, subject } from "../ui/brand";
import {
  Alert,
  Badge,
  Button,
  Card,
  Checkbox,
  EmptyState,
  ProgressBar,
  SearchBox,
  SectionTitle,
  Segmented,
  SelectField,
  Spinner,
  Stat,
  Tabs,
  TextArea,
} from "../ui/core";
import { Confirm, Modal, useToast } from "../ui/overlay";
import { SkeletonRows } from "../ui/table";
import { Forbidden, Pager } from "./Admin";

const VIEWS: { value: RatingsView; label: string; count?: (s: RatingsSummary) => number }[] = [
  { value: "rated", label: "All rated", count: (s) => s.rated_lessons },
  { value: "top", label: "Top rated" },
  { value: "poor", label: "Poorly rated", count: (s) => s.poor },
  { value: "rule", label: "Meet the rule", count: (s) => s.meets_rule },
  { value: "near", label: "Almost there", count: (s) => s.near_rule },
  { value: "listed", label: "In Explore", count: (s) => s.in_explore.total },
  { value: "excluded", label: "Kept out", count: (s) => s.excluded },
  { value: "review", label: "Under review", count: (s) => s.review.flagged + s.review.working },
  { value: "all", label: "All lessons" },
];

const SORTS = [
  { value: "", label: "Suggested order" },
  { value: "score", label: "Best weighted score" },
  { value: "worst", label: "Worst weighted score" },
  { value: "average", label: "Highest average" },
  { value: "count", label: "Most ratings" },
  { value: "recent", label: "Most recent" },
  { value: "new", label: "Newest lessons" },
];

const REVIEW: Record<Exclude<ReviewStatus, "">, { label: string; tone: "warning" | "info" | "success" }> = {
  flagged: { label: "Needs work", tone: "warning" },
  working: { label: "Being improved", tone: "info" },
  resolved: { label: "Improved", tone: "success" },
};

function ruleText(rule: ExploreRule): string {
  return `an average of ${rule.min_average.toFixed(1)} or more from at least ${plural(rule.min_raters, "person", "people")}`;
}

export function AdminRatings() {
  useTitle("Ratings & Explore");
  const { can } = useAuth();
  const allowed = can("ratings:read");
  const summary = useResource(allowed ? "admin:ratings:summary" : null, v1.ratingsSummary, 30_000);
  const [view, setView] = useState<RatingsView>("rated");
  const [q, setQ] = useState("");
  const [subjectKey, setSubjectKey] = useState("");
  const [sort, setSort] = useState("");
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState<string | null>(null);
  const term = useDebounced(q, 250);
  // biome-ignore lint/correctness/useExhaustiveDependencies: reset when these change
  useEffect(() => setOffset(0), [view, term, subjectKey, sort]);
  const list = useResource(allowed ? `admin:ratings:list:${view}:${term}:${subjectKey}:${sort}:${offset}` : null, () =>
    v1.ratedLessons({ view, q: term, subject: subjectKey, sort: sort || undefined, offset }),
  );
  if (!allowed) return <Forbidden />;
  const s = summary.data;
  const changed = () => {
    void summary.reload();
    invalidate("admin:ratings:");
  };
  const total = list.data?.total ?? 0;
  return (
    <div className="page admin ratings">
      <header className="page-head">
        <div>
          <p className="page-kicker">Administration</p>
          <h1>Ratings &amp; Explore</h1>
          {s && (
            <p className="page-sub">
              {s.rule.auto
                ? `A lesson shared with a link joins Explore by itself once it has ${ruleText(s.rule)} (its owner can opt out). `
                : "Listing lessons by their ratings is turned off (PLATFORM_EXPLORE_AUTO). "}
              Owners' own ratings and suspended accounts' do not count. You can add any lesson, or keep any out.
            </p>
          )}
        </div>
      </header>

      {s ? (
        <>
          <div className="stat-row">
            <Stat icon={Star} label="Rated lessons" value={s.rated_lessons} sub={plural(s.ratings, "rating")} />
            <Stat
              icon={ThumbsUp}
              label="Average rating"
              value={s.average != null ? `${s.average.toFixed(2)} ★` : "–"}
              sub="over every rating that counts"
              tone="warning"
            />
            <Stat
              icon={Compass}
              label="In Explore"
              value={s.in_explore.total}
              sub={`${s.in_explore.owner} published · ${s.in_explore.auto} by ratings · ${s.in_explore.admin} added`}
              tone="success"
            />
            <Stat
              icon={ThumbsDown}
              label="Poorly rated"
              value={s.poor}
              sub="average below 3 stars"
              tone={s.poor ? "warning" : "neutral"}
            />
            <Stat
              icon={Wrench}
              label="Under review"
              value={s.review.flagged + s.review.working}
              sub={`${s.review.resolved} improved`}
              tone="info"
            />
          </div>
          <div className="admin-grid ratings-grid">
            <Card>
              <SectionTitle title="How people rate" sub={plural(s.ratings, "rating")} />
              <StarBars stars={s.stars} />
            </Card>
            <Card className="ratings-wide">
              <SectionTitle title="Latest comments" />
              {s.recent_comments.length === 0 ? (
                <p className="muted small">No comments yet.</p>
              ) : (
                <ul className="comment-list">
                  {s.recent_comments.map((c) => (
                    <li key={`${c.lesson_id}-${c.at}-${c.name}`}>
                      <Stars value={c.stars} />
                      <p>{c.comment}</p>
                      <small>
                        {c.name} on{" "}
                        <button type="button" className="link-button" onClick={() => setOpen(c.lesson_id)}>
                          {c.lesson_title}
                        </button>{" "}
                        · {ago(c.at)}
                      </small>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>
        </>
      ) : summary.error ? (
        <Alert tone="danger">{summary.error.message}</Alert>
      ) : (
        <Spinner />
      )}

      <div className="toolbar ratings-toolbar">
        <Tabs
          label="Lessons"
          value={view}
          onChange={setView}
          tabs={VIEWS.map((v) => ({ value: v.value, label: v.label, count: s && v.count ? v.count(s) : undefined }))}
        />
      </div>
      <div className="toolbar">
        <SearchBox value={q} onChange={setQ} placeholder="Search lessons" />
        <div className="toolbar-right">
          <SelectField
            aria-label="Subject"
            value={subjectKey}
            onChange={(e) => setSubjectKey(e.target.value)}
            options={[
              { value: "", label: "All subjects" },
              ...Object.entries(SUBJECTS).map(([k, v]) => ({ value: k, label: v.label })),
            ]}
          />
          <SelectField aria-label="Order" value={sort} onChange={(e) => setSort(e.target.value)} options={SORTS} />
        </div>
      </div>
      <Card padded={false} className="table-card">
        <div className="table-wrap sticky-head">
          <table className="table ratings-table">
            <thead>
              <tr>
                <th>Lesson</th>
                <th>Rating</th>
                <th>Stars</th>
                <th
                  className="num"
                  title="The average pulled towards the platform's mean until a lesson has about 10 ratings"
                >
                  Score
                </th>
                <th>Explore</th>
                <th>Review</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {list.loading && !list.data ? (
                <SkeletonRows cols={7} rows={6} />
              ) : (
                (list.data?.items ?? []).map((l) => (
                  <tr key={l.id}>
                    <td>
                      <button
                        type="button"
                        className="table-title link-button"
                        onClick={() => setOpen(l.id)}
                        title={l.title}
                      >
                        <b>{l.title}</b>
                        <small>
                          {l.subject_label} · {l.owner?.name ?? "No owner"} · {l.visibility}
                        </small>
                      </button>
                    </td>
                    <td>
                      <RatingCell lesson={l} rule={list.data?.rule} />
                    </td>
                    <td>
                      <Spread stars={l.rating.stars} />
                    </td>
                    <td className="num">
                      {l.rating.count ? l.rating.score.toFixed(2) : <span className="muted">–</span>}
                    </td>
                    <td>
                      <ExploreState lesson={l} />
                    </td>
                    <td>
                      <ReviewBadge status={l.review.status} />
                    </td>
                    <td className="table-actions">
                      <QuickCurate lesson={l} onDone={changed} />
                      <Button size="sm" variant="ghost" icon={MessageSquareText} onClick={() => setOpen(l.id)}>
                        Details
                      </Button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        {list.error && <div className="table-empty">{list.error.message}</div>}
        {list.data && list.data.items.length === 0 && (
          <EmptyState icon={Star} title="Nothing here" text="No lesson matches this view yet." />
        )}
      </Card>
      <Pager offset={offset} size={25} total={total} onChange={setOffset} />
      {open && <LessonRatings id={open} onClose={() => setOpen(null)} onChanged={changed} />}
    </div>
  );
}

// ---------------------------------------------------------------------- small pieces

function Stars({ value }: { value: number }) {
  return (
    <span className="stars-inline" role="img" aria-label={`${value} of 5 stars`}>
      {[1, 2, 3, 4, 5].map((n) => (
        <Star key={n} size={13} className={n <= value ? "on" : ""} />
      ))}
    </span>
  );
}

/** How many gave 1 to 5 stars, as five small bars. */
function Spread({ stars }: { stars: number[] }) {
  const max = Math.max(1, ...stars);
  const total = stars.reduce((a, b) => a + b, 0);
  if (!total) return <span className="muted">–</span>;
  return (
    <span
      className="spread"
      role="img"
      aria-label={stars.map((n, i) => `${i + 1} star${i ? "s" : ""}: ${n}`).join(", ")}
    >
      {stars.map((n, i) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: always five, in order
        <span key={i} className={`s${i + 1}`} style={{ height: `${n ? Math.max(14, (n / max) * 100) : 4}%` }} />
      ))}
    </span>
  );
}

function StarBars({ stars }: { stars: number[] }) {
  const total = stars.reduce((a, b) => a + b, 0) || 1;
  return (
    <ul className="star-bars">
      {[5, 4, 3, 2, 1].map((n) => {
        const count = stars[n - 1] ?? 0;
        return (
          <li key={n}>
            <span>{n} ★</span>
            <span className="star-bars-bar">
              <span className={`s${n}`} style={{ width: `${(count / total) * 100}%` }} />
            </span>
            <b>{count}</b>
          </li>
        );
      })}
    </ul>
  );
}

function RatingCell({ lesson, rule }: { lesson: RatedLesson; rule?: ExploreRule }) {
  const r = lesson.rating;
  if (!r.count) return <span className="muted">Not rated</span>;
  const short = rule && r.count < rule.min_raters ? rule.min_raters - r.count : 0;
  return (
    <span className="num-pair">
      <b>
        {r.average?.toFixed(2)} <Star size={13} className="star-on" />
      </b>
      <small>
        {plural(r.count, "rating")}
        {r.meets_rule
          ? " · meets the rule"
          : short > 0 && (r.average ?? 0) >= (rule?.min_average ?? 5)
            ? ` · ${short} more needed`
            : ""}
      </small>
    </span>
  );
}

function ExploreState({ lesson }: { lesson: RatedLesson }) {
  const e = lesson.explore;
  if (e.listed)
    return (
      <span className="explore-state">
        <Badge tone="success" icon={e.source === "admin" ? Pin : Compass}>
          {e.source === "auto"
            ? "In Explore · ratings"
            : e.source === "admin"
              ? "In Explore · added"
              : "In Explore · published"}
        </Badge>
      </span>
    );
  return (
    <span className="explore-state">
      {e.pin === "exclude" ? (
        <Badge tone="danger" icon={EyeOff}>
          Kept out
        </Badge>
      ) : (
        <Badge tone="neutral">Not listed</Badge>
      )}
      <small className="muted">{e.reason_text}</small>
    </span>
  );
}

function ReviewBadge({ status }: { status: ReviewStatus }) {
  if (!status) return <span className="muted">–</span>;
  const r = REVIEW[status];
  return (
    <Badge tone={r.tone} icon={Flag}>
      {r.label}
    </Badge>
  );
}

// ---------------------------------------------------------------------- curating

type Action = "include" | "exclude" | "automatic";

const DONE: Record<Action, string> = {
  include: "Added to Explore.",
  exclude: "Kept out of Explore.",
  automatic: "Explore follows the rule for it again.",
};

/** One click from the list: add a lesson that is not listed, take out one that is (with a reason). */
function QuickCurate({ lesson, onDone }: { lesson: RatedLesson; onDone: () => void }) {
  const { can } = useAuth();
  const toast = useToast();
  const [action, setAction] = useState<Action | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  if (!can("explore:curate")) return null;
  const listed = lesson.explore.listed;
  const ready = lesson.status === "complete";
  const run = async () => {
    if (!action) return;
    setBusy(true);
    try {
      await v1.curate(lesson.id, {
        action,
        note: note.trim(),
        share: action === "include" && lesson.visibility === "private",
      });
      toast.success(DONE[action]);
      setAction(null);
      setNote("");
      onDone();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      {listed ? (
        <Button size="sm" variant="ghost" icon={EyeOff} onClick={() => setAction("exclude")}>
          Remove
        </Button>
      ) : (
        <Button
          size="sm"
          variant="ghost"
          icon={Pin}
          disabled={!ready}
          title={ready ? undefined : "Once the lesson is ready"}
          onClick={() => setAction("include")}
        >
          Add
        </Button>
      )}
      <Confirm
        open={!!action}
        onClose={() => setAction(null)}
        onConfirm={() => void run()}
        busy={busy}
        danger={action === "exclude"}
        title={action === "exclude" ? "Take it out of Explore?" : "Add it to Explore?"}
        text={
          action === "exclude"
            ? "It stays out whatever its ratings say, until you hand it back to the rule. Its owner is told; its link still works."
            : lesson.visibility === "private"
              ? "This lesson is private: adding it shares it with a link, so anyone can open it from Explore. Its owner is told."
              : "It stays in Explore whatever its ratings say, until you hand it back to the rule."
        }
        confirmLabel={action === "exclude" ? "Take it out" : lesson.visibility === "private" ? "Share and add" : "Add"}
      >
        <TextArea
          label="Why (for the team, optional)"
          rows={2}
          maxLength={300}
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
      </Confirm>
    </>
  );
}

// ---------------------------------------------------------------------- one lesson

function LessonRatings({ id, onClose, onChanged }: { id: string; onClose: () => void; onChanged: () => void }) {
  const [stars, setStars] = useState(0);
  const [offset, setOffset] = useState(0);
  const res = useResource(`admin:ratings:one:${id}:${stars}:${offset}`, () =>
    v1.ratedLesson(id, { stars: stars || undefined, offset }),
  );
  const d = res.data;
  const done = () => {
    void res.reload();
    onChanged();
  };
  return (
    <Modal
      open
      onClose={onClose}
      size="lg"
      title={d ? d.title : "Ratings"}
      description={
        d ? (
          <>
            {subject(d.subject).label} · {d.owner?.name ?? "No owner"} · {d.visibility} ·{" "}
            <Link href={`/lessons/${d.id}`}>Open the lesson</Link>
          </>
        ) : undefined
      }
    >
      {!d ? (
        res.error ? (
          <Alert tone="danger">{res.error.message}</Alert>
        ) : (
          <Spinner />
        )
      ) : (
        <div className="lesson-ratings">
          <section className="lesson-ratings-top">
            <div className="lesson-ratings-score">
              <b>{d.rating.average != null ? d.rating.average.toFixed(2) : "–"}</b>
              <Stars value={Math.round(d.rating.average ?? 0)} />
              <small>
                {plural(d.rating.count, "rating")} that count · score {d.rating.score.toFixed(2)}
              </small>
            </div>
            <StarBars stars={d.rating.stars} />
            <RuleProgress lesson={d} rule={d.rule} />
          </section>

          <ExploreControls lesson={d} onDone={done} />
          <ReviewControls lesson={d} onDone={done} />

          <section>
            <div className="card-h-row">
              <h3>What people said</h3>
              <Segmented
                label="Stars"
                value={stars}
                onChange={(v) => {
                  setStars(v);
                  setOffset(0);
                }}
                options={[{ value: 0, label: "All" }, ...[5, 4, 3, 2, 1].map((n) => ({ value: n, label: `${n} ★` }))]}
              />
            </div>
            {d.ratings.length === 0 ? (
              <p className="muted small">No ratings here.</p>
            ) : (
              <ul className="comment-list">
                {d.ratings.map((r) => (
                  <li key={`${r.name}-${r.at}`} className={r.counts ? "" : "not-counted"}>
                    <Stars value={r.stars} />
                    {r.comment ? <p>{r.comment}</p> : <p className="muted">No comment.</p>}
                    <small>
                      {r.name} · {ago(r.at)}
                      {!r.counts && r.why_not && <> · not counted ({r.why_not})</>}
                    </small>
                  </li>
                ))}
              </ul>
            )}
            <Pager offset={offset} size={50} total={d.ratings_total} onChange={setOffset} />
          </section>

          {d.history.length > 0 && (
            <section>
              <h3>History</h3>
              <ul className="history-list">
                {d.history.map((h) => (
                  <li key={`${h.action}-${h.at}`}>
                    <code>{h.action}</code> by {h.by ?? "someone"} · {date(h.at, true)}
                    {typeof h.detail.note === "string" && h.detail.note && <q>{h.detail.note}</q>}
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>
      )}
    </Modal>
  );
}

function RuleProgress({ lesson, rule }: { lesson: RatedLesson; rule: ExploreRule }) {
  const r = lesson.rating;
  const people = Math.min(1, r.count / Math.max(1, rule.min_raters));
  const high = (r.average ?? 0) >= rule.min_average;
  return (
    <div className="rule-progress">
      <h4>The Explore rule</h4>
      <p className="muted small">{ruleText(rule)}</p>
      <ProgressBar value={people} tone={r.meets_rule ? "success" : "brand"} label="People who rated it" />
      <small>
        {r.count} of {rule.min_raters} people · average {r.average != null ? r.average.toFixed(2) : "–"}{" "}
        {r.count ? (high ? "(high enough)" : `(needs ${rule.min_average.toFixed(1)})`) : ""}
      </small>
      {!rule.auto && <small className="muted">Listing by ratings is turned off.</small>}
    </div>
  );
}

function ExploreControls({ lesson, onDone }: { lesson: RatedLesson; onDone: () => void }) {
  const { can } = useAuth();
  const toast = useToast();
  const [note, setNote] = useState("");
  const [subjectKey, setSubjectKey] = useState(lesson.subject);
  const [share, setShare] = useState(false);
  const [busy, setBusy] = useState<Action | null>(null);
  const e = lesson.explore;
  const curate = can("explore:curate");
  const isPrivate = lesson.visibility === "private";
  const ready = lesson.status === "complete";
  const run = async (action: Action) => {
    setBusy(action);
    try {
      await v1.curate(lesson.id, {
        action,
        note: note.trim(),
        share: action === "include" && isPrivate && share,
        subject: subjectKey !== lesson.subject ? subjectKey : undefined,
      });
      toast.success(DONE[action]);
      setNote("");
      onDone();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };
  return (
    <section className="explore-controls">
      <h3>Explore</h3>
      <div className="explore-controls-state">
        <ExploreState lesson={lesson} />
        {e.listed && <small className="muted">{e.reason_text}</small>}
        {e.pin && (
          <small className="muted">
            {e.pin === "include" ? "Kept in" : "Kept out"} by an admin{e.pinned_at ? ` ${ago(e.pinned_at)}` : ""}
            {e.pin_note ? `: “${e.pin_note}”` : ""}
          </small>
        )}
        {e.opt_out && <small className="muted">Its owner chose not to be listed by ratings.</small>}
      </div>
      {curate && (
        <div className="explore-controls-form">
          <TextArea
            label="Why (for the team, optional)"
            rows={2}
            maxLength={300}
            value={note}
            onChange={(ev) => setNote(ev.target.value)}
          />
          <SelectField
            label="List it under"
            value={subjectKey}
            onChange={(ev) => setSubjectKey(ev.target.value)}
            options={Object.entries(SUBJECTS).map(([k, v]) => ({ value: k, label: v.label }))}
          />
          {isPrivate && e.pin !== "include" && (
            <Alert tone="warning">
              <TriangleAlert size={14} /> This lesson is private. To add it, it must be shared with a link.
              <Checkbox checked={share} onChange={setShare}>
                Share it with a link (its owner is told)
              </Checkbox>
            </Alert>
          )}
          <div className="explore-controls-buttons">
            {e.pin !== "include" && (
              <Button
                variant="primary"
                size="sm"
                icon={Pin}
                loading={busy === "include"}
                disabled={!!busy || !ready || (isPrivate && !share)}
                title={ready ? undefined : "Once the lesson is ready"}
                onClick={() => void run("include")}
              >
                {e.listed ? "Keep it in Explore" : "Add to Explore"}
              </Button>
            )}
            {e.pin !== "exclude" && (
              <Button
                variant="danger"
                size="sm"
                icon={EyeOff}
                loading={busy === "exclude"}
                disabled={!!busy}
                onClick={() => void run("exclude")}
              >
                Keep it out
              </Button>
            )}
            {e.pin && (
              <Button
                variant="secondary"
                size="sm"
                icon={RotateCcw}
                loading={busy === "automatic"}
                disabled={!!busy}
                onClick={() => void run("automatic")}
              >
                Back to the rule
              </Button>
            )}
            {subjectKey !== lesson.subject && (
              // Only the subject changes: the current decision is sent again as it is.
              <Button
                variant="secondary"
                size="sm"
                disabled={!!busy}
                onClick={() =>
                  void run(e.pin === "include" ? "include" : e.pin === "exclude" ? "exclude" : "automatic")
                }
              >
                Move to {SUBJECTS[subjectKey]?.label ?? subjectKey}
              </Button>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

function ReviewControls({ lesson, onDone }: { lesson: RatedLesson; onDone: () => void }) {
  const { can } = useAuth();
  const toast = useToast();
  const [status, setStatus] = useState<ReviewStatus>(lesson.review.status);
  const [note, setNote] = useState(lesson.review.note);
  const [busy, setBusy] = useState(false);
  const manage = can("review:manage");
  const dirty = status !== lesson.review.status || note !== lesson.review.note;
  const save = async () => {
    setBusy(true);
    try {
      await v1.review(lesson.id, status, note.trim());
      toast.success(status ? `Marked: ${REVIEW[status].label.toLowerCase()}.` : "Review cleared.");
      onDone();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  if (!manage)
    return lesson.review.status ? (
      <section>
        <h3>Review</h3>
        <ReviewBadge status={lesson.review.status} />
        {lesson.review.note && <p className="muted small">{lesson.review.note}</p>}
      </section>
    ) : null;
  return (
    <section className="review-controls">
      <h3>Review by the team</h3>
      <Segmented
        label="Review"
        value={status}
        onChange={setStatus}
        options={[
          { value: "" as ReviewStatus, label: "None" },
          { value: "flagged" as ReviewStatus, label: "Needs work" },
          { value: "working" as ReviewStatus, label: "Being improved" },
          { value: "resolved" as ReviewStatus, label: "Improved" },
        ]}
      />
      <TextArea
        label="Notes for the team"
        rows={2}
        maxLength={500}
        value={note}
        placeholder="What to fix: scene 2 says 9.8 m/s, the picture shows 10"
        onChange={(e) => setNote(e.target.value)}
      />
      <Button variant="secondary" size="sm" icon={Flag} disabled={!dirty} loading={busy} onClick={() => void save()}>
        Save review
      </Button>
      {lesson.review.at && <small className="muted">Last changed {ago(lesson.review.at)}</small>}
    </section>
  );
}
