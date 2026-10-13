/** Settings: account, learning, playback, appearance, notifications, security, privacy and data. */
import {
  Bell,
  CirclePlay,
  Coins,
  Download,
  GraduationCap,
  KeyRound,
  Laptop,
  Monitor,
  Moon,
  Palette,
  Shield,
  Smartphone,
  Sun,
  Trash,
  User as UserIcon,
} from "lucide-react";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { type Accent, type Preferences, type ThemePref, v1 } from "../api";
import { useAuth, useUser } from "../auth";
import { ago, credits, date, device } from "../format";
import { useResource, useTitle } from "../hooks";
import { Link, navigate } from "../nav";
import { ACCENT_NAMES, ACCENTS, applyAccent, applyTheme, isAccent } from "../theme";
import { AUDIENCES, SUBJECTS } from "../ui/brand";
import {
  Alert,
  Avatar,
  Badge,
  Button,
  Card,
  PasswordField,
  ProgressBar,
  Segmented,
  Spinner,
  Switch,
  TextArea,
  TextField,
} from "../ui/core";
import { Modal, useToast } from "../ui/overlay";

const TABS = [
  { key: "account", label: "Account", icon: UserIcon },
  { key: "learning", label: "Learning", icon: GraduationCap },
  { key: "playback", label: "Playback", icon: CirclePlay },
  { key: "appearance", label: "Appearance", icon: Palette },
  { key: "notifications", label: "Notifications", icon: Bell },
  { key: "credits", label: "Credits", icon: Coins },
  { key: "security", label: "Security", icon: Shield },
  { key: "privacy", label: "Privacy & data", icon: Download },
] as const;
type TabKey = (typeof TABS)[number]["key"];

const COLORS = ["crimson", "ruby", "amber", "teal", "indigo", "slate", "plum", "forest"];

export function SettingsPage({ tab }: { tab?: string }) {
  useTitle("Settings");
  const current: TabKey = (TABS.find((t) => t.key === tab)?.key ?? "account") as TabKey;
  return (
    <div className="page settings">
      <header className="page-head">
        <div>
          <h1>Settings</h1>
          <p className="page-sub">Your account, how lessons are made and played, and your privacy.</p>
        </div>
      </header>
      <div className="settings-layout">
        <nav className="settings-nav" aria-label="Settings sections">
          {TABS.map((t) => (
            <Link
              key={t.key}
              href={`/settings/${t.key}`}
              className={`settings-link ${current === t.key ? "active" : ""}`}
              aria-current={current === t.key ? "page" : undefined}
            >
              <t.icon size={17} /> {t.label}
            </Link>
          ))}
        </nav>
        <div className="settings-body" key={current}>
          {current === "account" && <AccountTab />}
          {current === "learning" && <LearningTab />}
          {current === "playback" && <PlaybackTab />}
          {current === "appearance" && <AppearanceTab />}
          {current === "notifications" && <NotificationsTab />}
          {current === "credits" && <CreditsTab />}
          {current === "security" && <SecurityTab />}
          {current === "privacy" && <PrivacyTab />}
        </div>
      </div>
    </div>
  );
}

/** Save a preference at once (switches, choices). */
function usePref() {
  const { setUser } = useAuth();
  const toast = useToast();
  return async (p: Partial<Preferences>, quiet = false) => {
    try {
      setUser((await v1.updateMe({ preferences: p })).user);
      if (!quiet) toast.success("Saved.");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    }
  };
}

function AccountTab() {
  const user = useUser();
  const { setUser } = useAuth();
  const toast = useToast();
  const [name, setName] = useState(user.name);
  const [bio, setBio] = useState(user.bio);
  const [avatar, setAvatar] = useState(user.avatar);
  const [busy, setBusy] = useState(false);
  const dirty = name !== user.name || bio !== user.bio || avatar !== user.avatar;
  // The app's color follows the avatar's (ADR-057): a new avatar color shows across the app at once and
  // is kept when the profile is saved; leaving without saving puts the saved color back.
  const savedAccent = user.preferences.accent;
  const saved = useRef(savedAccent);
  saved.current = savedAccent;
  const newColor = avatar !== user.avatar && isAccent(avatar);
  useEffect(() => {
    applyAccent(newColor ? (avatar as Accent) : savedAccent);
  }, [newColor, avatar, savedAccent]);
  useEffect(() => () => applyAccent(saved.current), []);
  const matchAvatarColor = async () => {
    if (!isAccent(user.avatar)) return;
    try {
      setUser((await v1.updateMe({ preferences: { accent: user.avatar } })).user);
      toast.success(`The app is ${ACCENT_NAMES[user.avatar]} now.`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  };
  const save = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      const preferences = newColor ? { accent: avatar as Accent } : undefined;
      setUser((await v1.updateMe({ name: name.trim(), bio: bio.trim(), avatar, preferences })).user);
      toast.success(newColor ? "Profile updated: the app takes your new color too." : "Profile updated.");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card>
      <h2 className="card-h">Profile</h2>
      <form className="settings-form" onSubmit={save}>
        <div className="avatar-pick">
          <Avatar name={name || user.name} color={avatar} size={64} />
          <div>
            <span className="ui-label">Avatar color</span>
            <fieldset className="swatches">
              <legend className="sr-only">Avatar color</legend>
              {COLORS.map((c) => (
                <button
                  key={c}
                  type="button"
                  aria-pressed={avatar === c}
                  aria-label={c}
                  title={isAccent(c) ? ACCENT_NAMES[c] : c}
                  className={`swatch av-${c} ${avatar === c ? "on" : ""}`}
                  onClick={() => setAvatar(c)}
                />
              ))}
            </fieldset>
            <p className="field-note swatch-note">
              {newColor ? (
                "The whole app shows this color now; save to keep it."
              ) : savedAccent === avatar || !isAccent(avatar) ? (
                "The app takes your avatar's color: buttons, links and highlights."
              ) : (
                <>
                  The app is {ACCENT_NAMES[savedAccent]}.{" "}
                  <button type="button" className="link-btn" onClick={() => void matchAvatarColor()}>
                    Use {ACCENT_NAMES[avatar as Accent]} for the app too
                  </button>
                </>
              )}
            </p>
          </div>
        </div>
        <TextField label="Name" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} required />
        <TextField
          label="Email"
          value={user.email}
          readOnly
          hint={
            user.verified ? (
              <Badge tone="success">Confirmed</Badge>
            ) : (
              <span>
                Not confirmed yet. <ResendLink />
              </span>
            )
          }
        />
        <TextArea
          label="Bio"
          value={bio}
          onChange={(e) => setBio(e.target.value)}
          maxLength={300}
          rows={3}
          hint={`${bio.length}/300`}
        />
        <div className="form-actions">
          <Button type="submit" variant="primary" loading={busy} disabled={!dirty || !name.trim()}>
            Save changes
          </Button>
        </div>
      </form>
    </Card>
  );
}

function ResendLink() {
  const toast = useToast();
  return (
    <button
      type="button"
      className="link-btn"
      onClick={() =>
        v1
          .resendVerification()
          .then(() => toast.success("Confirmation link sent."))
          .catch((e: unknown) => toast.error(e instanceof Error ? e.message : String(e)))
      }
    >
      Send the link again
    </button>
  );
}

function LearningTab() {
  const user = useUser();
  const p = user.preferences;
  const save = usePref();
  const interests = p.interests ?? [];
  return (
    <>
      <Card>
        <h2 className="card-h">Defaults for new lessons</h2>
        <div className="settings-stack">
          <div>
            <span className="ui-label">Level</span>
            <div className="choice-grid">
              {AUDIENCES.map((a) => (
                <button
                  key={a.value}
                  type="button"
                  aria-pressed={p.audience === a.value}
                  className={`choice-card ${p.audience === a.value ? "on" : ""}`}
                  onClick={() => void save({ audience: a.value })}
                >
                  <b>{a.label}</b>
                  <small>{a.hint}</small>
                </button>
              ))}
            </div>
          </div>
          <div className="create-two">
            <div>
              <span className="ui-label">Narration</span>
              <Segmented
                label="Narration language"
                value={p.language}
                onChange={(v) => void save({ language: v })}
                options={[
                  { value: "English" as const, label: "English" },
                  { value: "Hinglish" as const, label: "Hinglish" },
                ]}
              />
            </div>
            <div>
              <span className="ui-label">Length</span>
              <Segmented
                label="Default length"
                value={p.duration_min}
                onChange={(v) => void save({ duration_min: v })}
                options={[1, 2, 3, 5, 8, 10]
                  .filter((m) => m <= user.limits.max_minutes)
                  .map((m) => ({ value: m, label: `${m}m` }))}
              />
            </div>
          </div>
        </div>
      </Card>
      <Card>
        <h2 className="card-h">Weekly goal</h2>
        <Segmented
          label="Weekly goal"
          value={p.weekly_goal_min}
          onChange={(v) => void save({ weekly_goal_min: v })}
          options={[30, 60, 120, 240].map((m) => ({ value: m, label: m < 60 ? `${m} min` : `${m / 60} h` }))}
        />
      </Card>
      <Card>
        <h2 className="card-h">Interests</h2>
        <p className="muted">We recommend lessons from these subjects.</p>
        <div className="chips">
          {Object.entries(SUBJECTS)
            .filter(([k]) => k !== "general")
            .map(([k, s]) => {
              const on = interests.includes(k);
              return (
                <button
                  key={k}
                  type="button"
                  aria-pressed={on}
                  className={`chip ${on ? "on" : ""}`}
                  onClick={() =>
                    void save({ interests: on ? interests.filter((x) => x !== k) : [...interests, k] }, true)
                  }
                >
                  <s.icon size={14} /> {s.label}
                </button>
              );
            })}
        </div>
      </Card>
    </>
  );
}

function PlaybackTab() {
  const p = useUser().preferences;
  const save = usePref();
  return (
    <Card>
      <h2 className="card-h">Playback</h2>
      <div className="settings-stack">
        <div>
          <span className="ui-label">Default speed</span>
          <Segmented
            label="Default speed"
            value={p.playback_rate}
            onChange={(v) => void save({ playback_rate: v })}
            options={[0.75, 1, 1.25, 1.5, 2].map((r) => ({ value: r, label: `${r}×` }))}
          />
        </div>
        <Switch
          label="Captions"
          description="Word-by-word captions under the picture."
          checked={p.captions}
          onChange={(v) => void save({ captions: v })}
        />
        <Switch
          label="Narration voice"
          description="Hear the lesson read aloud (when the lesson has a voice)."
          checked={p.voice}
          onChange={(v) => void save({ voice: v })}
        />
      </div>
    </Card>
  );
}

function AppearanceTab() {
  const p = useUser().preferences;
  const save = usePref();
  const choose = (theme: ThemePref) => {
    applyTheme(theme, p.reduced_motion);
    void save({ theme }, true);
  };
  const chooseAccent = (accent: Accent) => {
    applyAccent(accent);
    void save({ accent }, true);
  };
  return (
    <Card>
      <h2 className="card-h">Appearance</h2>
      <fieldset className="theme-pick">
        <legend className="sr-only">Theme</legend>
        {(
          [
            { v: "light", label: "Light", icon: Sun },
            { v: "dark", label: "Dark", icon: Moon },
            { v: "system", label: "Same as my device", icon: Monitor },
          ] as const
        ).map((t) => (
          <button
            key={t.v}
            type="button"
            aria-pressed={p.theme === t.v}
            className={`theme-card ${t.v} ${p.theme === t.v ? "on" : ""}`}
            onClick={() => choose(t.v)}
          >
            <span className="theme-preview" aria-hidden="true">
              <span />
              <span />
              <span />
            </span>
            <span className="theme-label">
              <t.icon size={16} /> {t.label}
            </span>
          </button>
        ))}
      </fieldset>
      <div className="accent-section">
        <h3 className="settings-sub">App color</h3>
        <p className="muted small">
          Buttons, links and highlights across the app (lessons keep their own colors). Changing your avatar color
          changes it too.
        </p>
        <fieldset className="swatches accent-pick">
          <legend className="sr-only">App color</legend>
          {ACCENTS.map((a) => (
            <button
              key={a}
              type="button"
              aria-pressed={p.accent === a}
              aria-label={a === "crimson" ? `${ACCENT_NAMES[a]} (default)` : ACCENT_NAMES[a]}
              title={a === "crimson" ? `${ACCENT_NAMES[a]} (default)` : ACCENT_NAMES[a]}
              className={`swatch av-${a} ${p.accent === a ? "on" : ""}`}
              onClick={() => chooseAccent(a)}
            />
          ))}
        </fieldset>
        <p className="field-note">
          {ACCENT_NAMES[p.accent] ?? ACCENT_NAMES.crimson}
          {p.accent === "crimson" ? " (default)" : ""}
          {p.accent !== "crimson" && (
            <>
              {" · "}
              <button type="button" className="link-btn" onClick={() => chooseAccent("crimson")}>
                Back to the default red
              </button>
            </>
          )}
        </p>
      </div>
      <Switch
        label="Reduce motion"
        description="Fewer animations in the interface (lessons still play)."
        checked={p.reduced_motion}
        onChange={(v) => {
          applyTheme(p.theme, v);
          void save({ reduced_motion: v });
        }}
      />
    </Card>
  );
}

function NotificationsTab() {
  const p = useUser().preferences;
  const save = usePref();
  return (
    <Card>
      <h2 className="card-h">Notifications</h2>
      <p className="muted">In-app notifications are always on. Choose what also reaches your inbox.</p>
      <div className="settings-stack">
        <Switch
          label="Lesson ready"
          description="When a lesson you created is ready to watch."
          checked={p.notify_lesson_ready}
          onChange={(v) => void save({ notify_lesson_ready: v })}
        />
        <Switch
          label="Support replies"
          description="When our team answers your ticket."
          checked={p.notify_support}
          onChange={(v) => void save({ notify_support: v })}
        />
        <Switch
          label="Weekly summary"
          description="Your minutes, streak and suggestions, every Monday."
          checked={p.notify_weekly_summary}
          onChange={(v) => void save({ notify_weekly_summary: v })}
        />
      </div>
    </Card>
  );
}

function SecurityTab() {
  const toast = useToast();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sessions = useResource("sessions", v1.sessions, 5000);
  const change = async (e: FormEvent) => {
    e.preventDefault();
    if (next !== again) {
      setError("The new passwords are different.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const r = await v1.changePassword(current, next);
      toast.success(
        r.signed_out ? `Password changed. ${r.signed_out} other device(s) signed out.` : "Password changed.",
      );
      setCurrent("");
      setNext("");
      setAgain("");
      void sessions.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };
  const end = async (id: string) => {
    try {
      await v1.endSession(id);
      toast.success("Signed out on that device.");
      void sessions.reload();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    }
  };
  const others = (sessions.data ?? []).filter((s) => !s.current).length;
  return (
    <>
      <Card>
        <h2 className="card-h">
          <KeyRound size={18} /> Change password
        </h2>
        <form className="settings-form" onSubmit={change}>
          {error && <Alert tone="danger">{error}</Alert>}
          <PasswordField
            label="Current password"
            autoComplete="current-password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
          />
          <PasswordField
            label="New password"
            autoComplete="new-password"
            value={next}
            onChange={(e) => setNext(e.target.value)}
            meter
          />
          <PasswordField
            label="Repeat new password"
            autoComplete="new-password"
            value={again}
            onChange={(e) => setAgain(e.target.value)}
          />
          <div className="form-actions">
            <Button type="submit" variant="primary" loading={busy} disabled={!current || !next || !again}>
              Update password
            </Button>
          </div>
        </form>
      </Card>
      <Card>
        <div className="card-h-row">
          <h2 className="card-h">Where you are signed in</h2>
          {others > 0 && (
            <Button
              variant="secondary"
              size="sm"
              onClick={async () => {
                const r = await v1.endOtherSessions();
                toast.success(`Signed out of ${r.signed_out} other device(s).`);
                void sessions.reload();
              }}
            >
              Sign out everywhere else
            </Button>
          )}
        </div>
        <ul className="device-list">
          {(sessions.data ?? []).map((s) => {
            const mobile = /Android|iPhone|iPad/.test(s.user_agent);
            return (
              <li key={s.id}>
                {mobile ? <Smartphone size={20} /> : <Laptop size={20} />}
                <div>
                  <b>
                    {device(s.user_agent)} {s.current && <Badge tone="success">This device</Badge>}
                  </b>
                  <small>
                    {s.ip || "unknown network"} · active {ago(s.last_used_at)} · signed in {ago(s.created_at)}
                  </small>
                </div>
                {!s.current && (
                  <Button variant="ghost" size="sm" onClick={() => void end(s.id)}>
                    Sign out
                  </Button>
                )}
              </li>
            );
          })}
        </ul>
      </Card>
    </>
  );
}

function PrivacyTab() {
  const user = useUser();
  const { logout } = useAuth();
  const toast = useToast();
  const [confirm, setConfirm] = useState(false);
  const [password, setPassword] = useState("");
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!confirm) {
      setPassword("");
      setTyped("");
      setError(null);
    }
  }, [confirm]);
  const download = async () => {
    try {
      const r = await fetch("/api/v1/me/export");
      if (!r.ok) throw new Error((await r.json().catch(() => ({ detail: "Export failed" }))).detail);
      const blob = await r.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "my-animator-data.json";
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    }
  };
  const remove = async () => {
    setBusy(true);
    setError(null);
    try {
      await v1.deleteAccount(password);
      toast.success("Your account was deleted.");
      await logout().catch(() => undefined);
      navigate("/", { replace: true });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };
  return (
    <>
      <Card>
        <h2 className="card-h">Your data</h2>
        <p className="muted">
          Download a copy of everything we keep about you: profile, lessons, progress, notes, doubts and support
          messages.
        </p>
        <Button variant="secondary" icon={Download} onClick={() => void download()}>
          Download my data
        </Button>
      </Card>
      <Card className="danger-zone">
        <h2 className="card-h">Delete account</h2>
        <p className="muted">
          This removes your account, progress, notes and saved lessons for good. Lessons you created are removed from
          the platform. This cannot be undone.
        </p>
        <Button variant="danger" icon={Trash} onClick={() => setConfirm(true)}>
          Delete my account
        </Button>
      </Card>
      <Modal
        open={confirm}
        onClose={() => setConfirm(false)}
        title="Delete your account?"
        description={`Signed in as ${user.email}`}
        size="sm"
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirm(false)}>
              Keep my account
            </Button>
            <Button
              variant="danger"
              loading={busy}
              disabled={!password || typed !== "DELETE"}
              onClick={() => void remove()}
            >
              Delete forever
            </Button>
          </>
        }
      >
        <div className="settings-form">
          {error && <Alert tone="danger">{error}</Alert>}
          <PasswordField
            label="Your password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            data-autofocus
          />
          <TextField label='Type "DELETE" to confirm' value={typed} onChange={(e) => setTyped(e.target.value)} />
        </div>
      </Modal>
    </>
  );
}

/** This month's credits (ADR-053): what is left, what they went to, and credits given back. */
function CreditsTab() {
  const res = useResource("me:credits", v1.myCredits, 20_000);
  const c = res.data;
  if (!c) return res.error ? <Alert tone="danger">{res.error.message}</Alert> : <Spinner />;
  const used = Math.max(0, c.used);
  const adjusted = c.by_area.adjustment;
  return (
    <>
      <Card>
        <h2 className="card-h">Credits this month</h2>
        {c.blocked ? (
          <Alert tone="warning" title="Your account cannot use credits at the moment">
            You can still watch lessons. Contact support if this is unexpected.
          </Alert>
        ) : c.unlimited ? (
          <p className="credits-now-note">Your account has no monthly limit. You have used {credits(used)} so far.</p>
        ) : (
          <div className="credits-now">
            <div className="credits-big">
              <b>{credits(Math.max(0, c.remaining ?? 0))}</b>
              <span>of {credits(c.limit)} credits left</span>
            </div>
            <ProgressBar value={c.limit ? used / c.limit : 1} label="Credits used this month" />
            <p className="muted small">
              {credits(used)} used. They renew on {date(c.resets_at)}.
            </p>
          </div>
        )}
        <ul className="kv-list credits-areas">
          <li>
            <span>Making lessons</span>
            <b>{credits(c.by_area.create)}</b>
          </li>
          <li>
            <span>Learning (doubt answers, speech input)</span>
            <b>{credits(c.by_area.learn)}</b>
          </li>
          {adjusted !== 0 && (
            <li>
              <span>{adjusted < 0 ? "Given back to you" : "Charged by an administrator"}</span>
              <b>{credits(Math.abs(adjusted))}</b>
            </li>
          )}
        </ul>
        <p className="muted small">
          Credits stand for what the AI and the voice cost to make your lessons and answer your doubts. Watching lessons
          costs nothing.
        </p>
      </Card>
      <Card>
        <h2 className="card-h">Where they went</h2>
        {c.items.length === 0 && c.adjustments.length === 0 ? (
          <p className="muted">Nothing yet this month.</p>
        ) : (
          <ul className="kv-list credits-items">
            {c.adjustments.map((a) => (
              <li key={`adj-${a.at}`}>
                <span>
                  <b>{a.credits > 0 ? "Given back" : "Charged"}</b>
                  <small>
                    {a.note} · {ago(a.at)}
                  </small>
                </span>
                <b className={a.credits > 0 ? "plus" : ""}>
                  {a.credits > 0 ? "+" : "−"}
                  {credits(Math.abs(a.credits))}
                </b>
              </li>
            ))}
            {c.items.map((i) => (
              <li key={`${i.area}-${i.lesson_id ?? "speech"}`}>
                <span>
                  {i.lesson_id ? (
                    <Link href={`/lessons/${i.lesson_id}`}>{i.lesson_title}</Link>
                  ) : (
                    <b>{i.lesson_title}</b>
                  )}
                  <small>
                    {i.area === "create" ? "Making the lesson" : "Doubts and speech"} · {ago(i.at)}
                  </small>
                </span>
                <b>{credits(i.credits)}</b>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </>
  );
}
