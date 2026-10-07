/** A hand-authored sample lesson (the engine's reference lessons), in the platform's lesson look. */
import { ArrowLeft, Clock, Sparkles } from "lucide-react";
import { useMemo } from "react";
import { fixtureLesson } from "../../lesson/fixtures";
import { buildLesson } from "../../lesson/lesson";
import { Player } from "../../player/Player";
import { useAuth } from "../auth";
import { minutes } from "../format";
import { useTitle } from "../hooks";
import { Link, navigate } from "../nav";
import { Badge, Button, EmptyState } from "../ui/core";

export function SampleView({ slug }: { slug: string }) {
  const fixture = fixtureLesson(slug);
  const lesson = useMemo(() => (fixture ? buildLesson(fixture.meta.title, fixture.scenes) : null), [fixture]);
  const { status, can, user } = useAuth();
  useTitle(fixture?.meta.title ?? "Sample lesson");
  if (!fixture || !lesson) {
    return (
      <div className="page narrow">
        <EmptyState icon={Sparkles} title="Sample not found" action={<Link href="/explore">Back to Explore</Link>} />
      </div>
    );
  }
  const prefs = user?.preferences;
  return (
    <div className="lesson-page">
      <nav className="crumbs" aria-label="Breadcrumb">
        <Link href="/explore" className="crumbs-back">
          <ArrowLeft size={16} /> Explore
        </Link>
        <span className="crumbs-sep">/</span>
        <span className="crumbs-here">{fixture.meta.title}</span>
      </nav>
      <header className="lesson-hero">
        <div className="lesson-hero-text">
          <div className="lesson-tags">
            <Badge tone="brand">Sample lesson</Badge>
          </div>
          <h1>{fixture.meta.title}</h1>
          {fixture.meta.description && <p className="lesson-sub">{fixture.meta.description}</p>}
          <div className="lesson-meta">
            <span>
              <Clock size={15} /> {minutes(lesson.duration)}
            </span>
            {fixture.meta.audience && <span>{fixture.meta.audience}</span>}
          </div>
        </div>
        <div className="lesson-actions">
          {status === "signed-in" && can("lesson:create") ? (
            <Button variant="primary" icon={Sparkles} onClick={() => navigate("/create")}>
              Make one like this
            </Button>
          ) : status !== "signed-in" ? (
            <Button variant="primary" icon={Sparkles} onClick={() => navigate("/signup")}>
              Make your own, free
            </Button>
          ) : null}
        </div>
      </header>
      <div className="lesson-player">
        <Player
          lesson={lesson}
          initial={prefs ? { rate: prefs.playback_rate, captions: prefs.captions, voice: prefs.voice } : undefined}
        />
      </div>
    </div>
  );
}
