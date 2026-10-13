/**
 * The signed-in app: a sidebar (sections shown only when the role has them), a top bar with global
 * search, "Create", notifications and the account menu, and the page. On phones the sidebar is a
 * drawer; on desktops it can be collapsed to icons (remembered per browser).
 */
import {
  Bell,
  Bookmark,
  BookOpen,
  ChartColumn,
  ChevronLeft,
  CircleQuestionMark,
  Clapperboard,
  Coins,
  Compass,
  Inbox,
  Layers,
  LayoutDashboard,
  LifeBuoy,
  LogOut,
  type LucideIcon,
  Menu as MenuIcon,
  Moon,
  Plus,
  Server,
  Settings,
  ShieldCheck,
  Sparkles,
  Star,
  Sun,
  TrendingUp,
  User,
  Users,
  Warehouse,
  X,
} from "lucide-react";
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { type Notice, v1 } from "../api";
import { useAuth, useUser } from "../auth";
import { ago } from "../format";
import { invalidate, useDismiss, useInterval, useResource } from "../hooks";
import { Link, navigate, useLocation } from "../nav";
import { applyTheme } from "../theme";
import { Logo } from "../ui/brand";
import { Avatar, Badge, Button } from "../ui/core";
import { useToast } from "../ui/overlay";
import { GlobalSearch } from "./GlobalSearch";
import { PageDeco } from "./PageDeco";

interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  perm?: string;
  exact?: boolean;
}

const LEARN: NavItem[] = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/create", label: "Create lesson", icon: Sparkles, perm: "lesson:create" },
  { href: "/lessons", label: "My lessons", icon: BookOpen },
  { href: "/explore", label: "Explore", icon: Compass },
  { href: "/library", label: "Library", icon: Bookmark },
  { href: "/progress", label: "Progress", icon: TrendingUp },
];
const HELP: NavItem[] = [
  { href: "/help", label: "Help center", icon: CircleQuestionMark },
  { href: "/support", label: "Support", icon: LifeBuoy },
];
const ADMIN: NavItem[] = [
  { href: "/admin", label: "Overview", icon: ChartColumn, perm: "analytics:read", exact: true },
  { href: "/admin/users", label: "People", icon: Users, perm: "user:manage" },
  { href: "/admin/lessons", label: "All lessons", icon: Clapperboard, perm: "lesson:any" },
  { href: "/admin/ratings", label: "Ratings & Explore", icon: Star, perm: "ratings:read" },
  { href: "/admin/credits", label: "Credits & cost", icon: Coins, perm: "usage:read" },
  { href: "/admin/objects", label: "Objects Warehouse", icon: Warehouse, perm: "objects:read" },
  { href: "/admin/support", label: "Support queue", icon: Inbox, perm: "support:manage" },
  { href: "/admin/audit", label: "Audit log", icon: ShieldCheck, perm: "audit:read" },
  { href: "/admin/system", label: "System", icon: Server, perm: "analytics:read" },
  { href: "/admin/playground", label: "DSL playground", icon: Layers, perm: "debug" },
];

const COLLAPSE_KEY = "animator.sidebar.collapsed";

function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(COLLAPSE_KEY) === "1";
  } catch {
    return false;
  }
}

export function AppShell({ children }: { children: ReactNode }) {
  const user = useUser();
  const { can, logout } = useAuth();
  const { path } = useLocation();
  const [drawer, setDrawer] = useState(false);
  const [collapsed, setCollapsed] = useState(readCollapsed);
  // A lesson gets the room: the sidebar folds to icons on lesson pages (one click opens it).
  const lessonPage = /^\/(lessons\/g_|samples\/)/.test(path);
  // The heading band's pattern (ADR-057): not where lessons or scenes play (a lesson, a sample, the
  // pipeline and debug views, the playground). Those pages stay exactly as light as before.
  const plain = lessonPage || /^\/admin\/(playground|raw\/|debug\/)/.test(path);
  const [lessonOpen, setLessonOpen] = useState(false);
  const folded = lessonPage ? !lessonOpen : collapsed;

  // biome-ignore lint/correctness/useExhaustiveDependencies: reset when these change
  useEffect(() => setDrawer(false), [path]);
  useEffect(() => {
    try {
      window.localStorage.setItem(COLLAPSE_KEY, collapsed ? "1" : "0");
    } catch {
      /* private mode */
    }
  }, [collapsed]);

  const section = (title: string, items: NavItem[]) => {
    const shown = items.filter((i) => !i.perm || can(i.perm));
    if (!shown.length) return null;
    return (
      <div className="side-section">
        <div className="side-section-title">{title}</div>
        {shown.map((i) => (
          <Link
            key={i.href}
            href={i.href}
            exact={i.exact}
            className="side-link"
            activeClassName="active"
            title={folded ? i.label : undefined}
          >
            <i.icon size={19} />
            <span>{i.label}</span>
          </Link>
        ))}
      </div>
    );
  };

  return (
    <div className={`shell ${folded ? "collapsed" : ""} ${drawer ? "drawer-open" : ""} ${plain ? "plain" : ""}`}>
      <aside className="sidebar" aria-label="Main">
        <div className="sidebar-head">
          <Link href="/dashboard" className="sidebar-brand" aria-label="Animator home">
            <Logo size={30} withName={!folded} />
          </Link>
          <button
            type="button"
            className="ui-icon-btn sidebar-close"
            onClick={() => setDrawer(false)}
            aria-label="Close menu"
          >
            <X size={18} />
          </button>
        </div>
        <nav className="sidebar-nav">
          {section("Learn", LEARN)}
          {section("Help", HELP)}
          {section("Administration", ADMIN)}
        </nav>
        <div className="sidebar-foot">
          <Link href="/profile" className="sidebar-user" title={folded ? user.name : undefined}>
            <Avatar name={user.name} color={user.avatar} size={34} />
            <span className="sidebar-user-text">
              <b>{user.name}</b>
              <small>
                {user.role === "learner" ? "Learner" : user.role === "educator" ? "Educator" : "Administrator"}
              </small>
            </span>
          </Link>
          <button
            type="button"
            className="ui-icon-btn sidebar-collapse"
            onClick={() => (lessonPage ? setLessonOpen((o) => !o) : setCollapsed((c) => !c))}
            aria-label={folded ? "Expand sidebar" : "Collapse sidebar"}
            title={folded ? "Expand" : "Collapse"}
          >
            <ChevronLeft size={18} />
          </button>
        </div>
      </aside>
      <button
        type="button"
        className="shell-scrim"
        aria-label="Close menu"
        tabIndex={-1}
        onClick={() => setDrawer(false)}
      />
      <div className="shell-main">
        <header className="topbar-app">
          <button
            type="button"
            className="ui-icon-btn topbar-menu"
            onClick={() => setDrawer(true)}
            aria-label="Open menu"
          >
            <MenuIcon size={20} />
          </button>
          <GlobalSearch />
          <div className="topbar-actions">
            {can("lesson:create") && path !== "/create" && (
              <Button
                variant="primary"
                size="sm"
                icon={Plus}
                onClick={() => navigate("/create")}
                className="topbar-create"
              >
                Create
              </Button>
            )}
            <NotificationsBell />
            <AccountMenu onLogout={logout} />
          </div>
        </header>
        {!user.verified && <VerifyBanner />}
        <main className="shell-content" id="main">
          {!plain && <PageDeco />}
          <div className="page-enter" key={path}>
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}

function VerifyBanner() {
  const toast = useToast();
  const [hidden, setHidden] = useState(() => {
    try {
      return window.sessionStorage.getItem("animator.verify.hidden") === "1";
    } catch {
      return false;
    }
  });
  const [busy, setBusy] = useState(false);
  if (hidden) return null;
  return (
    <div className="verify-banner" role="status">
      <span>Confirm your email to keep your account secure. We sent you a link.</span>
      <button
        type="button"
        className="verify-banner-link"
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          try {
            await v1.resendVerification();
            toast.success("A new confirmation link is on its way.");
          } catch (e) {
            toast.error(e instanceof Error ? e.message : String(e));
          } finally {
            setBusy(false);
          }
        }}
      >
        Resend link
      </button>
      <button
        type="button"
        className="ui-icon-btn"
        aria-label="Hide"
        onClick={() => {
          setHidden(true);
          try {
            window.sessionStorage.setItem("animator.verify.hidden", "1");
          } catch {
            /* private mode */
          }
        }}
      >
        <X size={16} />
      </button>
    </div>
  );
}

function NotificationsBell() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const close = useCallback(() => setOpen(false), []);
  useDismiss(ref, open, close);
  const res = useResource("notifications", v1.notifications, 20_000);
  useInterval(() => void res.reload(), 60_000);
  useEffect(() => {
    const on = () => void res.reload();
    window.addEventListener("focus", on);
    return () => window.removeEventListener("focus", on);
  }, [res.reload]);
  const unread = res.data?.unread ?? 0;
  const items = res.data?.items.slice(0, 8) ?? [];
  const openNotice = async (n: Notice) => {
    setOpen(false);
    if (!n.read) {
      res.mutate(
        (d) =>
          d && {
            unread: Math.max(0, d.unread - 1),
            items: d.items.map((x) => (x.id === n.id ? { ...x, read: true } : x)),
          },
      );
      void v1.readNotice(n.id).catch(() => undefined);
    }
    if (n.link) navigate(n.link);
  };
  return (
    <div className="ui-menu-wrap" ref={ref}>
      <button
        type="button"
        className="ui-icon-btn bell"
        aria-label={unread ? `Notifications, ${unread} unread` : "Notifications"}
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <Bell size={19} />
        {unread > 0 && <span className="bell-dot">{unread > 9 ? "9+" : unread}</span>}
      </button>
      {open && (
        <div className="ui-menu ui-menu-end notices" role="dialog" aria-label="Notifications">
          <div className="notices-head">
            <b>Notifications</b>
            {unread > 0 && (
              <button
                type="button"
                className="link-btn"
                onClick={async () => {
                  await v1.readAll().catch(() => undefined);
                  invalidate("notifications");
                }}
              >
                Mark all read
              </button>
            )}
          </div>
          {items.length === 0 ? (
            <div className="notices-empty">You are all caught up.</div>
          ) : (
            items.map((n) => (
              <button
                type="button"
                key={n.id}
                className={`notice ${n.read ? "" : "unread"}`}
                onClick={() => void openNotice(n)}
              >
                <span className="notice-title">{n.title}</span>
                {n.body && <span className="notice-body">{n.body}</span>}
                <span className="notice-time">{ago(n.created_at)}</span>
              </button>
            ))
          )}
          <Link href="/notifications" className="notices-all" onClick={() => setOpen(false)}>
            See all
          </Link>
        </div>
      )}
    </div>
  );
}

function AccountMenu({ onLogout }: { onLogout: () => Promise<void> }) {
  const user = useUser();
  const { setUser } = useAuth();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const close = useCallback(() => setOpen(false), []);
  useDismiss(ref, open, close);
  const dark = document.documentElement.dataset.theme === "dark";
  const go = (to: string) => {
    setOpen(false);
    navigate(to);
  };
  return (
    <div className="ui-menu-wrap" ref={ref}>
      <button
        type="button"
        className="account-btn"
        aria-label="Account menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <Avatar name={user.name} color={user.avatar} size={34} />
      </button>
      {open && (
        <div className="ui-menu ui-menu-end account-menu" role="menu" aria-label="Account">
          <div className="account-menu-head">
            <Avatar name={user.name} color={user.avatar} size={40} />
            <div>
              <b>{user.name}</b>
              <small>{user.email}</small>
            </div>
          </div>
          {user.role !== "learner" && (
            <div className="account-menu-role">
              <Badge tone="brand">{user.role === "admin" ? "Administrator" : "Educator"}</Badge>
            </div>
          )}
          <button type="button" role="menuitem" onClick={() => go("/profile")}>
            <User size={16} /> Profile
          </button>
          <button type="button" role="menuitem" onClick={() => go("/settings")}>
            <Settings size={16} /> Settings
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={async () => {
              const theme = dark ? "light" : "dark";
              applyTheme(theme, user.preferences.reduced_motion);
              setOpen(false);
              try {
                setUser((await v1.updateMe({ preferences: { theme } })).user);
              } catch {
                /* the look changed anyway; it is kept next time it saves */
              }
            }}
          >
            {dark ? <Sun size={16} /> : <Moon size={16} />} {dark ? "Light mode" : "Dark mode"}
          </button>
          <button type="button" role="menuitem" onClick={() => go("/help")}>
            <CircleQuestionMark size={16} /> Help center
          </button>
          <div className="ui-menu-sep" />
          <button
            type="button"
            role="menuitem"
            className="danger"
            onClick={() => {
              setOpen(false);
              void onLogout();
            }}
          >
            <LogOut size={16} /> Sign out
          </button>
        </div>
      )}
    </div>
  );
}
