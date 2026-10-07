/** Lessons everyone can watch: by subject, search and sort, with featured lessons and samples. */
import { Compass, Play, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { v1 } from "../api";
import { useAuth } from "../auth";
import { useDebounced, useResource, useTitle } from "../hooks";
import { useSaveToggle } from "../lesson/actions";
import { LessonCard, LessonCardSkeleton } from "../lesson/LessonCard";
import { Link, navigate, useLocation } from "../nav";
import { subject } from "../ui/brand";
import { Button, EmptyState, SearchBox, SelectField } from "../ui/core";

/** The hand-authored reference lesson (its scenes load only when it is opened). */
const SAMPLES = [{ slug: "archimedes", title: "Archimedes' principle", audience: "High school · 5 minutes" }];

export function ExplorePage() {
  useTitle("Explore");
  const { status, can } = useAuth();
  const { search } = useLocation();
  const [q, setQ] = useState(search.get("q") ?? "");
  const [subjectKey, setSubjectKey] = useState(search.get("subject") ?? "");
  const [sort, setSort] = useState("popular");
  const [pages, setPages] = useState(1);
  const term = useDebounced(q, 250);
  // biome-ignore lint/correctness/useExhaustiveDependencies: reset when these change
  useEffect(() => setPages(1), [term, subjectKey, sort]);
  useEffect(() => {
    setQ(search.get("q") ?? "");
  }, [search]);
  const subjects = useResource("explore:subjects", v1.subjects, 120_000);
  const featured = useResource("explore:featured", v1.featured, 120_000);
  const list = useResource(`explore:${term}:${subjectKey}:${sort}:${pages}`, () =>
    v1.explore({ q: term, subject: subjectKey || undefined, sort, limit: 24 * pages }),
  );
  const toggleSave = useSaveToggle();
  const signedIn = status === "signed-in";
  const filtering = !!term || !!subjectKey;

  return (
    <div className="page explore">
      <section className="explore-hero">
        <div>
          <span className="eyebrow">
            <Compass size={14} /> Explore
          </span>
          <h1>Learn from lessons the community made</h1>
          <p>Animated explanations shared by educators. Watch, save, and ask the tutor your doubts.</p>
        </div>
        <SearchBox value={q} onChange={setQ} placeholder="Search topics, e.g. photosynthesis" label="Search Explore" />
      </section>

      <div className="subject-filter" role="toolbar" aria-label="Subjects">
        <button
          type="button"
          className={`subject-pill ${subjectKey === "" ? "on" : ""}`}
          aria-pressed={subjectKey === ""}
          onClick={() => setSubjectKey("")}
        >
          All subjects
        </button>
        {(subjects.data ?? []).map((sb) => {
          const m = subject(sb.key);
          return (
            <button
              key={sb.key}
              type="button"
              aria-pressed={subjectKey === sb.key}
              className={`subject-pill ${subjectKey === sb.key ? "on" : ""}`}
              style={{ "--ink": m.ink, "--tint": m.tint } as React.CSSProperties}
              onClick={() => setSubjectKey(sb.key === subjectKey ? "" : sb.key)}
            >
              <m.icon size={14} /> {m.label}
              {sb.count > 0 && <span className="subject-pill-count">{sb.count}</span>}
            </button>
          );
        })}
      </div>

      {!filtering && (
        <section className="dash-section">
          <div className="ui-section-title">
            <div>
              <h2>Start with a sample</h2>
              <p>A hand-crafted lesson that shows what the engine can do.</p>
            </div>
          </div>
          <div className="sample-row">
            {SAMPLES.map((f) => (
              <Link key={f.slug} href={`/samples/${f.slug}`} className="sample-card">
                <span className="sample-play">
                  <Play size={22} />
                </span>
                <span>
                  <b>{f.title}</b>
                  <small>{f.audience}</small>
                </span>
              </Link>
            ))}
            {signedIn && can("lesson:create") && (
              <button type="button" className="sample-card create" onClick={() => navigate("/create")}>
                <span className="sample-play">
                  <Sparkles size={20} />
                </span>
                <span>
                  <b>Make your own</b>
                  <small>Any topic, your level, in minutes</small>
                </span>
              </button>
            )}
          </div>
        </section>
      )}

      {!filtering && !!featured.data?.length && (
        <section className="dash-section">
          <div className="ui-section-title">
            <div>
              <h2>Featured</h2>
              <p>Lessons our team loves.</p>
            </div>
          </div>
          <div className="lesson-grid">
            {featured.data.slice(0, 4).map((l) => (
              <LessonCard
                key={l.id}
                lesson={l}
                showOwner
                showStatus={false}
                onToggleSave={signedIn ? toggleSave : undefined}
              />
            ))}
          </div>
        </section>
      )}

      <section className="dash-section">
        <div className="ui-section-title">
          <div>
            <h2>{filtering ? "Results" : "All lessons"}</h2>
          </div>
          <SelectField
            aria-label="Sort"
            value={sort}
            onChange={(e) => setSort(e.target.value)}
            options={[
              { value: "popular", label: "Most watched" },
              { value: "top", label: "Highest rated" },
              { value: "new", label: "Newest" },
            ]}
          />
        </div>
        {list.loading && !list.data ? (
          <div className="lesson-grid">
            {[0, 1, 2, 3].map((i) => (
              <LessonCardSkeleton key={i} />
            ))}
          </div>
        ) : list.data?.items.length ? (
          <div className="lesson-grid">
            {list.data.items.map((l) => (
              <LessonCard
                key={l.id}
                lesson={l}
                showOwner
                showStatus={false}
                onToggleSave={signedIn ? toggleSave : undefined}
              />
            ))}
          </div>
        ) : (
          <EmptyState
            icon={Compass}
            title={filtering ? "Nothing matches yet" : "No public lessons yet"}
            text={
              filtering
                ? "Try another word or subject, or create the lesson yourself."
                : "Educators publish lessons here. Meanwhile, watch the sample or make your own."
            }
            action={
              signedIn && can("lesson:create") ? (
                <Button
                  variant="primary"
                  icon={Sparkles}
                  onClick={() => navigate(term ? `/create?topic=${encodeURIComponent(term)}` : "/create")}
                >
                  {term ? `Create “${term}”` : "Create a lesson"}
                </Button>
              ) : !signedIn ? (
                <Button variant="primary" onClick={() => navigate("/signup")}>
                  Create a free account
                </Button>
              ) : undefined
            }
          />
        )}
        {list.data?.has_more && (
          <div className="load-more">
            <Button variant="secondary" loading={list.loading} onClick={() => setPages((p) => p + 1)}>
              Show more
            </Button>
          </div>
        )}
      </section>
    </div>
  );
}
