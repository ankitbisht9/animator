/**
 * The web app's routes. `/render` (the headless export target) stays exactly as it was, outside the
 * platform. Everything else runs inside the account, toast and error layers: public pages (landing,
 * Explore, help, shared lessons) for everyone, the app shell for signed-in people, the admin pages for
 * roles that have them. Heavy pages (the engine, the editor, admin) load on demand.
 */
import { Component, lazy, type ReactNode, Suspense } from "react";
import { AuthProvider, useAuth } from "./platform/auth";
import { AppShell } from "./platform/layout/AppShell";
import { PublicLayout } from "./platform/layout/PublicLayout";
import { match, Redirect, useLocation } from "./platform/nav";
import { ForgotPage, LoginPage, ResetPage, SignupPage, VerifyPage } from "./platform/pages/AuthPages";
import { DashboardPage } from "./platform/pages/Dashboard";
import { ExplorePage } from "./platform/pages/Explore";
import { HelpPage, LegalPage, NotFoundPage, SupportPage, TicketThread } from "./platform/pages/Help";
import { LandingPage } from "./platform/pages/Landing";
import { LibraryPage, NotificationsPage, ProfilePage, ProgressPage } from "./platform/pages/Learning";
import { MyLessonsPage } from "./platform/pages/MyLessons";
import { OnboardingPage } from "./platform/pages/Onboarding";
import { SettingsPage } from "./platform/pages/Settings";
import { Logo } from "./platform/ui/brand";
import { Button, Spinner } from "./platform/ui/core";
import { ToastProvider } from "./platform/ui/overlay";
import { usePath } from "./router";

// The headless export target: the exporter waits for its window.__ready, so it may load lazily.
const RenderPage = lazy(async () => ({ default: (await import("./pages/RenderPage")).RenderPage }));
const LessonView = lazy(async () => ({ default: (await import("./platform/pages/LessonView")).LessonView }));
const SampleView = lazy(async () => ({ default: (await import("./platform/pages/SampleView")).SampleView }));
const CreatePage = lazy(async () => ({ default: (await import("./platform/pages/Create")).CreatePage }));
const AdminOverview = lazy(async () => ({ default: (await import("./platform/pages/Admin")).AdminOverview }));
const AdminUsers = lazy(async () => ({ default: (await import("./platform/pages/Admin")).AdminUsers }));
const AdminLessons = lazy(async () => ({ default: (await import("./platform/pages/Admin")).AdminLessons }));
const AdminSupport = lazy(async () => ({ default: (await import("./platform/pages/Admin")).AdminSupport }));
const AdminAudit = lazy(async () => ({ default: (await import("./platform/pages/Admin")).AdminAudit }));
const AdminSystem = lazy(async () => ({ default: (await import("./platform/pages/Admin")).AdminSystem }));
const AdminCredits = lazy(async () => ({ default: (await import("./platform/pages/AdminCredits")).AdminCredits }));
const AdminRatings = lazy(async () => ({ default: (await import("./platform/pages/AdminRatings")).AdminRatings }));
const Playground = lazy(async () => ({ default: (await import("./pages/Playground")).Playground }));
const DebugPage = lazy(async () => ({ default: (await import("./pages/DebugPage")).DebugPage }));
const GenerationPage = lazy(async () => ({ default: (await import("./pages/GenerationPage")).GenerationPage }));
const ClassicHome = lazy(async () => ({ default: (await import("./pages/Home")).Home }));

const GEN = /^g_[0-9a-f]{12}$/;

export function App() {
  const path = usePath();
  if (path === "/render")
    return (
      <Suspense fallback={null}>
        <RenderPage />
      </Suspense>
    );
  return (
    <AuthProvider>
      <ToastProvider>
        <ErrorBoundary>
          <Routes />
        </ErrorBoundary>
      </ToastProvider>
    </AuthProvider>
  );
}

function Loading() {
  return (
    <div className="page-loading" aria-busy="true">
      <Spinner size={28} />
    </div>
  );
}

function Boot() {
  return (
    <div className="boot">
      <Logo size={44} withName={false} />
      <Spinner size={22} />
    </div>
  );
}

function Routes() {
  const { path, search } = useLocation();
  const { status, can } = useAuth();
  if (status === "loading") return <Boot />;
  const signedIn = status === "signed-in";
  const here = path + (search.toString() ? `?${search}` : "");
  const lazyPage = (node: ReactNode) => <Suspense fallback={<Loading />}>{node}</Suspense>;
  const either = (node: ReactNode) =>
    signedIn ? <AppShell>{lazyPage(node)}</AppShell> : <PublicLayout>{lazyPage(node)}</PublicLayout>;
  const shell = (node: ReactNode) => <AppShell>{lazyPage(node)}</AppShell>;
  const admin = (perm: string, node: ReactNode) => shell(can(perm) ? node : <NotFoundPage />);
  const param = (pattern: string, key: string): string | null => match(pattern, path)?.[key] ?? null;
  const lessonId = (pattern: string): string | null => {
    const id = param(pattern, "id");
    return id && GEN.test(id) ? id : null;
  };

  // Links from before the platform keep working.
  const oldLesson = param("/g/:id", "id");
  if (oldLesson) return <Redirect to={`/lessons/${oldLesson}`} />;
  const oldDebug = param("/debug/:id", "id");
  if (oldDebug) return <Redirect to={`/admin/debug/${oldDebug}`} />;
  const oldSample = param("/lesson/:slug", "slug");
  if (oldSample) return <Redirect to={`/samples/${oldSample}`} />;
  if (path === "/playground") return <Redirect to="/admin/playground" />;

  // Signing in.
  if (path === "/login") return <LoginPage />;
  if (path === "/signup") return <SignupPage />;
  if (path === "/forgot-password") return <ForgotPage />;
  if (path === "/reset-password") return <ResetPage />;
  if (path === "/verify-email") return <VerifyPage />;

  // For everyone.
  if (path === "/" || path === "")
    return signedIn ? (
      <Redirect to="/dashboard" />
    ) : (
      <PublicLayout>
        <LandingPage />
      </PublicLayout>
    );
  if (path === "/explore") return either(<ExplorePage />);
  if (path === "/help") return either(<HelpPage />);
  if (path === "/terms") return either(<LegalPage kind="terms" />);
  if (path === "/privacy") return either(<LegalPage kind="privacy" />);
  if (path === "/support") return either(<SupportPage />);
  const sample = param("/samples/:slug", "slug");
  if (sample) return either(<SampleView slug={sample} />);
  const lesson = lessonId("/lessons/:id");
  if (lesson) return either(<LessonView key={lesson} id={lesson} />);

  // Signed in only.
  if (!signedIn) return <Redirect to={`/login?next=${encodeURIComponent(here)}`} />;
  if (path === "/onboarding") return <OnboardingPage />;
  if (path === "/dashboard") return shell(<DashboardPage />);
  if (path === "/create") return shell(<CreatePage />);
  if (path === "/lessons") return shell(<MyLessonsPage />);
  if (path === "/library") return shell(<LibraryPage />);
  if (path === "/progress") return shell(<ProgressPage />);
  if (path === "/profile") return shell(<ProfilePage />);
  if (path === "/notifications") return shell(<NotificationsPage />);
  if (path === "/settings") return shell(<SettingsPage />);
  const settingsTab = param("/settings/:tab", "tab");
  if (settingsTab) return shell(<SettingsPage tab={settingsTab} />);
  const ticket = param("/support/:id", "id");
  if (ticket) return shell(<TicketThread key={ticket} id={ticket} />);

  // Administration.
  if (path === "/admin") return admin("analytics:read", <AdminOverview />);
  if (path === "/admin/users") return admin("user:manage", <AdminUsers />);
  if (path === "/admin/lessons") return admin("lesson:any", <AdminLessons />);
  if (path === "/admin/support") return admin("support:manage", <AdminSupport />);
  const staffTicket = param("/admin/support/:id", "id");
  if (staffTicket) return admin("support:manage", <TicketThread key={staffTicket} id={staffTicket} staff />);
  if (path === "/admin/audit") return admin("audit:read", <AdminAudit />);
  if (path === "/admin/system") return admin("analytics:read", <AdminSystem />);
  if (path === "/admin/credits") return admin("usage:read", <AdminCredits />);
  if (path === "/admin/ratings") return admin("ratings:read", <AdminRatings />);
  if (path === "/admin/playground")
    return admin(
      "debug",
      <div className="page wide legacy">
        <Playground />
      </div>,
    );
  if (path === "/admin/classic")
    return admin(
      "debug",
      <div className="page legacy">
        <ClassicHome />
      </div>,
    );
  const debugId = lessonId("/admin/debug/:id");
  if (debugId)
    return admin(
      "debug",
      <div className="legacy">
        <DebugPage id={debugId} />
      </div>,
    );
  const rawId = lessonId("/admin/raw/:id");
  if (rawId)
    return admin(
      "debug",
      <div className="legacy">
        <GenerationPage key={rawId} id={rawId} />
      </div>,
    );

  return shell(<NotFoundPage />);
}

/** A page that fails to draw shows a way out instead of a blank screen. */
class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  override state: { error: Error | null } = { error: null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  override componentDidCatch(error: unknown) {
    console.error("page error", error);
  }
  override render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="crash">
        <Logo size={40} withName={false} />
        <h1>Something went wrong on this page</h1>
        <p>Reload to try again. If it keeps happening, tell us in Support and we will fix it.</p>
        <div className="row-gap center">
          <Button variant="primary" onClick={() => window.location.reload()}>
            Reload
          </Button>
          <Button
            variant="secondary"
            onClick={() => {
              this.setState({ error: null });
              window.location.href = "/dashboard";
            }}
          >
            Go to dashboard
          </Button>
        </div>
      </div>
    );
  }
}
