/** Three short steps after sign-up: interests, level and language, a weekly goal. They set the
 * defaults for new lessons and what Explore recommends. Skippable. */
import { ArrowLeft, ArrowRight, Check, Languages, Target } from "lucide-react";
import { useState } from "react";
import { v1 } from "../api";
import { useAuth, useUser } from "../auth";
import { useTitle } from "../hooks";
import { navigate, useLocation } from "../nav";
import { AUDIENCES, Logo, SUBJECTS } from "../ui/brand";
import { Button } from "../ui/core";
import { useToast } from "../ui/overlay";

const GOALS = [
  { value: 30, label: "Casual", hint: "30 min a week" },
  { value: 60, label: "Regular", hint: "1 hour a week" },
  { value: 120, label: "Serious", hint: "2 hours a week" },
  { value: 240, label: "Intense", hint: "4 hours a week" },
];

export function OnboardingPage() {
  useTitle("Welcome");
  const user = useUser();
  const { setUser } = useAuth();
  const toast = useToast();
  const { search } = useLocation();
  const [step, setStep] = useState(0);
  const [interests, setInterests] = useState<string[]>(user.preferences.interests ?? []);
  const [audience, setAudience] = useState(user.preferences.audience);
  const [language, setLanguage] = useState<"English" | "Hinglish">(user.preferences.language);
  const [goal, setGoal] = useState(user.preferences.weekly_goal_min);
  const [busy, setBusy] = useState(false);
  const next = search.get("next");

  const finish = async (skip = false) => {
    setBusy(true);
    try {
      const body = skip
        ? { onboarded: true }
        : { onboarded: true, preferences: { interests, audience, language, weekly_goal_min: goal } };
      setUser((await v1.updateMe(body)).user);
      navigate(next?.startsWith("/") && !next.startsWith("//") ? next : "/dashboard", { replace: true });
      if (!skip) toast.success("All set. Let's learn something!");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  const toggle = (k: string) => setInterests((all) => (all.includes(k) ? all.filter((x) => x !== k) : [...all, k]));
  const steps = ["Interests", "Level", "Goal"];

  return (
    <div className="onboard">
      <header className="onboard-head">
        <Logo size={30} />
        <button type="button" className="link-btn" onClick={() => void finish(true)} disabled={busy}>
          Skip for now
        </button>
      </header>
      <div className="onboard-body page-enter">
        <ol className="onboard-steps" aria-label="Steps">
          {steps.map((s, i) => (
            <li
              key={s}
              className={i < step ? "done" : i === step ? "on" : ""}
              aria-current={i === step ? "step" : undefined}
            >
              <span>{i < step ? <Check size={14} /> : i + 1}</span>
              {s}
            </li>
          ))}
        </ol>

        {step === 0 && (
          <section className="onboard-step" key="s0">
            <h1>Hi {user.name.split(" ")[0]}, what would you like to learn?</h1>
            <p className="lead">Pick a few subjects. We will suggest lessons you will enjoy.</p>
            <div className="onboard-subjects">
              {Object.entries(SUBJECTS)
                .filter(([k]) => k !== "general")
                .map(([k, s]) => (
                  <button
                    key={k}
                    type="button"
                    aria-pressed={interests.includes(k)}
                    className={`onboard-subject ${interests.includes(k) ? "on" : ""}`}
                    style={{ "--tint": s.tint, "--ink": s.ink } as React.CSSProperties}
                    onClick={() => toggle(k)}
                  >
                    <span className="onboard-subject-icon">
                      <s.icon size={22} />
                    </span>
                    {s.label}
                    {interests.includes(k) && <Check size={16} className="onboard-check" />}
                  </button>
                ))}
            </div>
          </section>
        )}

        {step === 1 && (
          <section className="onboard-step" key="s1">
            <h1>Who are the lessons for?</h1>
            <p className="lead">Explanations are pitched to this level. You can change it for any lesson.</p>
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
            <h2 className="onboard-sub">
              <Languages size={18} /> Narration language
            </h2>
            <div className="choice-grid two">
              {(["English", "Hinglish"] as const).map((l) => (
                <button
                  key={l}
                  type="button"
                  aria-pressed={language === l}
                  className={`choice-card ${language === l ? "on" : ""}`}
                  onClick={() => setLanguage(l)}
                >
                  <b>{l}</b>
                  <small>
                    {l === "English" ? "Voice and captions in English" : "Hindi-English mix, English on screen"}
                  </small>
                </button>
              ))}
            </div>
          </section>
        )}

        {step === 2 && (
          <section className="onboard-step" key="s2">
            <h1>Set a weekly goal</h1>
            <p className="lead">Small, steady steps work best. We will track your streak and cheer you on.</p>
            <div className="choice-grid">
              {GOALS.map((g) => (
                <button
                  key={g.value}
                  type="button"
                  aria-pressed={goal === g.value}
                  className={`choice-card ${goal === g.value ? "on" : ""}`}
                  onClick={() => setGoal(g.value)}
                >
                  <Target size={20} />
                  <b>{g.label}</b>
                  <small>{g.hint}</small>
                </button>
              ))}
            </div>
          </section>
        )}

        <div className="onboard-actions">
          {step > 0 ? (
            <Button variant="ghost" icon={ArrowLeft} onClick={() => setStep((s) => s - 1)}>
              Back
            </Button>
          ) : (
            <span />
          )}
          {step < 2 ? (
            <Button variant="primary" iconRight={ArrowRight} onClick={() => setStep((s) => s + 1)}>
              Continue
            </Button>
          ) : (
            <Button variant="primary" iconRight={Check} loading={busy} onClick={() => void finish()}>
              Start learning
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
