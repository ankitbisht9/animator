/** The lesson studio: what to explain, for whom, how long, in which language, and who may see it. */
import {
  ArrowRight,
  Clock,
  Globe,
  Info,
  Languages,
  Link2,
  Lock,
  Settings2,
  Sparkles,
  WandSparkles,
} from "lucide-react";
import { type FormEvent, useEffect, useMemo, useState } from "react";
import type { ApiConfig } from "../../api";
import { type Visibility, v1 } from "../api";
import { useAuth, useUser } from "../auth";
import { useResource, useTitle } from "../hooks";
import { HttpError } from "../http";
import { refreshLessonLists } from "../lesson/actions";
import { navigate, useLocation } from "../nav";
import { AUDIENCES, SUBJECTS } from "../ui/brand";
import { Alert, Button, Card, Checkbox, Segmented, TextArea, TextField } from "../ui/core";

const LENGTHS = [1, 2, 3, 5, 8, 10, 15, 20];
const EXAMPLES: Record<string, string[]> = {
  physics: [
    "Explain Archimedes' principle",
    "Why does a pendulum keep a steady rhythm?",
    "What is the difference between speed and velocity?",
  ],
  chemistry: [
    "How do acids and bases neutralise each other?",
    "What happens in electrolysis of water?",
    "Why do atoms form bonds?",
  ],
  biology: [
    "How does photosynthesis work?",
    "How does the heart pump blood?",
    "How do vaccines train the immune system?",
  ],
  mathematics: [
    "Explain the Pythagorean theorem",
    "What does a derivative really mean?",
    "How does compound interest grow?",
  ],
  "computer-science": [
    "How does binary search find a number so fast?",
    "What is recursion?",
    "How does the internet route a message?",
  ],
  "earth-space": ["Why do we have seasons?", "How do volcanoes erupt?", "Why does the Moon have phases?"],
};

async function configOnce(): Promise<ApiConfig> {
  const r = await fetch("/api/config");
  if (!r.ok) throw new Error(String(r.status));
  return (await r.json()) as ApiConfig;
}

export function CreatePage() {
  useTitle("Create a lesson");
  const user = useUser();
  const { can, reload } = useAuth();
  const { search } = useLocation();
  const prefs = user.preferences;
  const config = useResource("api-config", configOnce, 120_000);
  const [topic, setTopic] = useState(search.get("topic") ?? "");
  const [audience, setAudience] = useState(prefs.audience);
  const [minutes, setMinutes] = useState(Math.min(prefs.duration_min || 3, user.limits.max_minutes));
  const [language, setLanguage] = useState<string>(prefs.language);
  const [visibility, setVisibility] = useState<Visibility>("private");
  const [style, setStyle] = useState("");
  const [layout, setLayout] = useState<"classic" | "planned" | null>(prefs.layout);
  const [paced, setPaced] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [subjectKey, setSubjectKey] = useState(prefs.interests[0] ?? "physics");

  useEffect(() => {
    const t = search.get("topic");
    if (t) setTopic(t);
  }, [search]);

  const quota = user.limits.lessons_per_day;
  const used = user.usage.lessons;
  const left = quota > 0 ? Math.max(0, quota - used) : null;
  const blocked = left === 0 || (user.limits.require_verified && !user.verified);
  const lengths = LENGTHS.filter((l) => l <= user.limits.max_minutes);
  const scenes = useMemo(() => Math.max(2, Math.round((minutes * 60) / 22)), [minutes]);
  const notReady = config.data && !config.data.openai_key_configured;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (topic.trim().length < 3) {
      setError("Describe the topic in a few words first.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const lesson = await v1.createLesson({
        topic: topic.trim(),
        audience,
        duration_min: minutes,
        style: style.trim() || null,
        language,
        paced,
        layout: layout ?? config.data?.layout_mode ?? null,
        visibility,
      });
      refreshLessonLists();
      void reload();
      navigate(`/lessons/${lesson.id}`);
    } catch (err) {
      setError(err instanceof HttpError || err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };

  if (!can("lesson:create")) {
    return (
      <div className="page narrow">
        <Alert tone="info" title="Lesson creation is not part of your account">
          Explore lessons others have shared, or ask an administrator.
        </Alert>
      </div>
    );
  }

  return (
    <div className="page create-page">
      <header className="page-head">
        <div>
          <p className="page-kicker">Lesson studio</p>
          <h1>Create a lesson</h1>
          <p className="page-sub">
            Describe what you want to understand. We plan the lesson, draw every scene and narrate it.
          </p>
        </div>
        {left !== null && (
          <div className="quota" title="Lessons you can still create in the next 24 hours">
            <span className="quota-num">{left}</span>
            <span>
              of {quota} lessons
              <br />
              left today
            </span>
          </div>
        )}
      </header>

      {notReady && (
        <Alert tone="warning" title="Lesson creation is not available right now">
          {can("debug")
            ? "OPENAI_API_KEY is not set in .env. Add it and restart the API."
            : "The service is being set up. Please try again later."}
        </Alert>
      )}
      {config.data?.tts?.problem && can("debug") && (
        <Alert tone="warning">Narration voice is off: {config.data.tts.problem}</Alert>
      )}
      {user.limits.require_verified && !user.verified && (
        <Alert tone="info">Confirm your email to create lessons. We sent you a link when you signed up.</Alert>
      )}

      <form className="create-grid" onSubmit={submit}>
        <div className="create-main">
          <Card>
            <TextArea
              label="What should the lesson explain?"
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder="e.g. Explain Archimedes' principle with a boat and a stone"
              maxLength={500}
              rows={3}
              autoFocus
              hint={`${topic.length}/500 · A clear question works best: “Why…”, “How does…”, “Explain…”`}
            />
            <div className="examples-block">
              <div className="examples-subjects" role="tablist" aria-label="Example subjects">
                {Object.keys(EXAMPLES).map((k) => {
                  const s = SUBJECTS[k];
                  if (!s) return null;
                  return (
                    <button
                      key={k}
                      type="button"
                      role="tab"
                      aria-selected={subjectKey === k}
                      className={`subject-tab ${subjectKey === k ? "on" : ""}`}
                      onClick={() => setSubjectKey(k)}
                    >
                      <s.icon size={14} /> {s.label}
                    </button>
                  );
                })}
              </div>
              <div className="chips">
                {(EXAMPLES[subjectKey] ?? []).map((x) => (
                  <button key={x} type="button" className="chip" onClick={() => setTopic(x)}>
                    <WandSparkles size={13} /> {x}
                  </button>
                ))}
              </div>
            </div>
          </Card>

          <Card>
            <h2 className="card-h">Who is it for?</h2>
            <div className="choice-grid">
              {AUDIENCES.map((a) => (
                <button
                  key={a.value}
                  type="button"
                  aria-pressed={audience === a.value}
                  className={`choice-card ${audience === a.value ? "on" : ""}`}
                  onClick={() => setAudience(a.value)}
                >
                  <b>{a.label}</b>
                  <small>{a.hint}</small>
                </button>
              ))}
            </div>
          </Card>

          <Card>
            <div className="create-two">
              <div>
                <h2 className="card-h">
                  <Clock size={17} /> Length
                </h2>
                <Segmented
                  label="Length in minutes"
                  value={minutes}
                  onChange={setMinutes}
                  options={lengths.map((l) => ({ value: l, label: `${l}m` }))}
                />
              </div>
              <div>
                <h2 className="card-h">
                  <Languages size={17} /> Narration
                </h2>
                <Segmented
                  label="Narration language"
                  value={language}
                  onChange={setLanguage}
                  options={[
                    { value: "English", label: "English" },
                    { value: "Hinglish", label: "Hinglish" },
                  ]}
                />
                <p className="field-note">On-screen text is always in English.</p>
              </div>
            </div>
          </Card>

          <Card>
            <h2 className="card-h">Who can watch it?</h2>
            <div className="choice-grid three">
              {(
                [
                  { v: "private", icon: Lock, label: "Only me", hint: "Private to your account" },
                  { v: "unlisted", icon: Link2, label: "Anyone with the link", hint: "Share with a class or a friend" },
                  {
                    v: "public",
                    icon: Globe,
                    label: "Everyone",
                    hint: can("lesson:publish") ? "Listed in Explore" : "Educators only",
                  },
                ] as const
              ).map((o) => (
                <button
                  key={o.v}
                  type="button"
                  aria-pressed={visibility === o.v}
                  disabled={o.v === "public" && !can("lesson:publish")}
                  className={`choice-card ${visibility === o.v ? "on" : ""}`}
                  onClick={() => setVisibility(o.v)}
                >
                  <o.icon size={18} />
                  <b>{o.label}</b>
                  <small>{o.hint}</small>
                </button>
              ))}
            </div>
            <p className="field-note">Public lessons appear in Explore once they are ready.</p>
          </Card>

          <Card>
            <button
              type="button"
              className="advanced-toggle"
              aria-expanded={advanced}
              onClick={() => setAdvanced((a) => !a)}
            >
              <Settings2 size={17} /> Advanced options
            </button>
            {advanced && (
              <div className="advanced">
                <TextField
                  label="Style notes"
                  value={style}
                  onChange={(e) => setStyle(e.target.value)}
                  placeholder="Optional, e.g. use a cricket example, keep it playful"
                  maxLength={300}
                />
                <div>
                  <span className="ui-label">Layout</span>
                  <Segmented
                    label="Layout"
                    value={layout ?? config.data?.layout_mode ?? "classic"}
                    onChange={(v) => setLayout(v)}
                    options={[
                      { value: "classic", label: "Classic" },
                      { value: "planned", label: "Planned (precise)" },
                    ]}
                  />
                </div>
                <Checkbox checked={paced} onChange={setPaced}>
                  Save cost: make scenes just ahead of where I am watching
                </Checkbox>
              </div>
            )}
          </Card>
        </div>

        <aside className="create-side">
          <Card className="summary">
            <h2 className="card-h">
              <Sparkles size={17} /> Your lesson
            </h2>
            <dl>
              <div>
                <dt>Topic</dt>
                <dd>{topic.trim() || <span className="muted">Not chosen yet</span>}</dd>
              </div>
              <div>
                <dt>Level</dt>
                <dd>{AUDIENCES.find((a) => a.value === audience)?.label ?? audience}</dd>
              </div>
              <div>
                <dt>Length</dt>
                <dd>
                  {minutes} min · about {scenes} scenes
                </dd>
              </div>
              <div>
                <dt>Narration</dt>
                <dd>{language}</dd>
              </div>
              <div>
                <dt>Sharing</dt>
                <dd>
                  {visibility === "private"
                    ? "Only you"
                    : visibility === "unlisted"
                      ? "Anyone with the link"
                      : "Everyone (Explore)"}
                </dd>
              </div>
            </dl>
            {error && <Alert tone="danger">{error}</Alert>}
            <Button
              type="submit"
              variant="primary"
              size="lg"
              block
              loading={busy}
              iconRight={ArrowRight}
              disabled={blocked || !!notReady || topic.trim().length < 3}
            >
              {left === 0 ? "Daily limit reached" : "Create lesson"}
            </Button>
            <p className="summary-note">
              <Info size={14} /> The first scene is usually ready in a minute or two and starts playing while the rest
              is made.
            </p>
          </Card>
          <Card className="tips">
            <h3>Tips for a great lesson</h3>
            <ul>
              <li>Ask one clear question rather than a whole chapter.</li>
              <li>Name an example you like: “with a football”, “using a bank account”.</li>
              <li>Pick the level of the person watching, not your own.</li>
            </ul>
          </Card>
        </aside>
      </form>
    </div>
  );
}
