/** Sign in, sign up, forgot and reset password, email confirmation. */
import { ArrowRight, CircleCheck, CircleX, Lock, Mail, User as UserIcon } from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";
import { v1 } from "../api";
import { useAuth } from "../auth";
import { useResource, useTitle } from "../hooks";
import { HttpError } from "../http";
import { AuthLayout } from "../layout/AuthLayout";
import { Link, navigate, Redirect, useLocation } from "../nav";
import { Alert, Button, Checkbox, PasswordField, Spinner, TextField } from "../ui/core";

function message(e: unknown): string {
  return e instanceof HttpError || e instanceof Error ? e.message : String(e);
}

/** Only local paths are followed after sign-in (no open redirect). */
function safeNext(next: string | null): string | null {
  return next && /^\/(?!\/)[^\s]*$/.test(next) && !next.startsWith("/login") ? next : null;
}

export function LoginPage() {
  useTitle("Sign in");
  const { login, status, user } = useAuth();
  const { search } = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const next = safeNext(search.get("next"));
  if (status === "signed-in" && user) return <Redirect to={next ?? (user.onboarded ? "/dashboard" : "/onboarding")} />;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const u = await login(email.trim(), password, remember);
      navigate(next ?? (u.onboarded ? "/dashboard" : "/onboarding"), { replace: true });
    } catch (err) {
      setError(message(err));
      setBusy(false);
    }
  };

  return (
    <AuthLayout
      title="Welcome back"
      sub="Sign in to continue learning."
      footer={
        <>
          New here?{" "}
          <Link href={`/signup${next ? `?next=${encodeURIComponent(next)}` : ""}`}>Create a free account</Link>
        </>
      }
    >
      {search.get("ended") && !error && <Alert tone="warning">Your session ended. Sign in again to carry on.</Alert>}
      {search.get("reset") && !error && (
        <Alert tone="success">Your password was changed. Sign in with the new one.</Alert>
      )}
      {error && <Alert tone="danger">{error}</Alert>}
      <form className="auth-form" onSubmit={submit} noValidate>
        <TextField
          label="Email"
          type="email"
          icon={Mail}
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
          autoFocus
        />
        <PasswordField
          label="Password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
        <div className="auth-row">
          <Checkbox checked={remember} onChange={setRemember}>
            Keep me signed in
          </Checkbox>
          <Link href="/forgot-password" className="auth-link">
            Forgot password?
          </Link>
        </div>
        <Button
          type="submit"
          variant="primary"
          size="lg"
          block
          loading={busy}
          iconRight={ArrowRight}
          disabled={!email || !password}
        >
          Sign in
        </Button>
      </form>
    </AuthLayout>
  );
}

export function SignupPage() {
  useTitle("Create your account");
  const { register, status, user } = useAuth();
  const { search } = useLocation();
  const info = useResource("public-info", v1.info, 300_000);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [agree, setAgree] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const next = safeNext(search.get("next"));
  if (status === "signed-in" && user) return <Redirect to={next ?? "/dashboard"} />;

  const min = info.data?.password_min_length ?? 8;
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (password.length < min) {
      setError(`Use at least ${min} characters for your password.`);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await register(name.trim(), email.trim(), password);
      navigate(`/onboarding${next ? `?next=${encodeURIComponent(next)}` : ""}`, { replace: true });
    } catch (err) {
      setError(message(err));
      setBusy(false);
    }
  };

  if (info.data && !info.data.signup) {
    return (
      <AuthLayout
        title="Sign-ups are closed"
        sub="This platform is invite-only right now."
        footer={<Link href="/login">Sign in instead</Link>}
      >
        <Alert tone="info">Ask an administrator to create an account for you.</Alert>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      title="Create your account"
      sub="Free to start. Your first animated lesson is minutes away."
      footer={
        <>
          Already have an account?{" "}
          <Link href={`/login${next ? `?next=${encodeURIComponent(next)}` : ""}`}>Sign in</Link>
        </>
      }
    >
      {error && <Alert tone="danger">{error}</Alert>}
      <form className="auth-form" onSubmit={submit} noValidate>
        <TextField
          label="Your name"
          icon={UserIcon}
          autoComplete="name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
          autoFocus
          maxLength={80}
        />
        <TextField
          label="Email"
          type="email"
          icon={Mail}
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        />
        <PasswordField
          label="Password"
          autoComplete="new-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          meter
          hint={`At least ${min} characters, mixing letters with numbers or symbols.`}
          required
        />
        <Checkbox checked={agree} onChange={setAgree}>
          I agree to the{" "}
          <Link href="/terms" target="_blank">
            Terms
          </Link>{" "}
          and{" "}
          <Link href="/privacy" target="_blank">
            Privacy policy
          </Link>
          .
        </Checkbox>
        <Button
          type="submit"
          variant="primary"
          size="lg"
          block
          loading={busy}
          iconRight={ArrowRight}
          disabled={!name || !email || !password || !agree}
        >
          Create account
        </Button>
      </form>
    </AuthLayout>
  );
}

export function ForgotPage() {
  useTitle("Reset your password");
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setSent((await v1.forgot(email.trim())).message);
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <AuthLayout
      title="Forgot your password?"
      sub="Enter your email and we will send you a link to choose a new one."
      footer={<Link href="/login">Back to sign in</Link>}
    >
      {sent ? (
        <div className="auth-done">
          <CircleCheck size={40} />
          <p>{sent}</p>
          <p className="muted">The link works for one hour. Check your spam folder if it does not arrive.</p>
        </div>
      ) : (
        <form className="auth-form" onSubmit={submit} noValidate>
          {error && <Alert tone="danger">{error}</Alert>}
          <TextField
            label="Email"
            type="email"
            icon={Mail}
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            autoFocus
          />
          <Button type="submit" variant="primary" size="lg" block loading={busy} disabled={!email}>
            Send reset link
          </Button>
        </form>
      )}
    </AuthLayout>
  );
}

export function ResetPage() {
  useTitle("Choose a new password");
  const { search } = useLocation();
  const token = search.get("token") ?? "";
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (password !== again) {
      setError("The two passwords are different.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await v1.reset(token, password);
      navigate("/login?reset=1", { replace: true });
    } catch (err) {
      setError(message(err));
      setBusy(false);
    }
  };
  if (!token)
    return (
      <AuthLayout title="This link is incomplete" footer={<Link href="/forgot-password">Ask for a new link</Link>}>
        <Alert tone="danger">Open the link from the email exactly as it was sent.</Alert>
      </AuthLayout>
    );
  return (
    <AuthLayout title="Choose a new password" sub="You will be signed out everywhere else, for safety.">
      <form className="auth-form" onSubmit={submit} noValidate>
        {error && <Alert tone="danger">{error}</Alert>}
        <PasswordField
          label="New password"
          autoComplete="new-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          meter
          required
          autoFocus
        />
        <PasswordField
          label="Repeat it"
          autoComplete="new-password"
          value={again}
          onChange={(e) => setAgain(e.target.value)}
          required
        />
        <Button
          type="submit"
          variant="primary"
          size="lg"
          block
          loading={busy}
          icon={Lock}
          disabled={!password || !again}
        >
          Save new password
        </Button>
      </form>
    </AuthLayout>
  );
}

export function VerifyPage() {
  useTitle("Confirm your email");
  const { search } = useLocation();
  const { reload, status } = useAuth();
  const token = search.get("token") ?? "";
  const [state, setState] = useState<"working" | "done" | "failed">("working");
  const [error, setError] = useState("");
  useEffect(() => {
    if (!token) {
      setState("failed");
      setError("The link is incomplete.");
      return;
    }
    v1.verify(token)
      .then(() => {
        setState("done");
        void reload();
      })
      .catch((e: unknown) => {
        setState("failed");
        setError(message(e));
      });
  }, [token, reload]);
  return (
    <AuthLayout
      title={state === "done" ? "Email confirmed" : state === "failed" ? "We could not confirm it" : "Confirming…"}
    >
      <div className="auth-done">
        {state === "working" && <Spinner size={36} />}
        {state === "done" && <CircleCheck size={44} />}
        {state === "failed" && <CircleX size={44} className="bad" />}
        <p>{state === "done" ? "Thanks! Your account is fully set up." : state === "failed" ? error : "One moment."}</p>
        {state !== "working" && (
          <Button variant="primary" onClick={() => navigate(status === "signed-in" ? "/dashboard" : "/login")}>
            {status === "signed-in" ? "Go to dashboard" : "Sign in"}
          </Button>
        )}
      </div>
    </AuthLayout>
  );
}
