/** Administration: the platform at a glance, people, all lessons, the support queue, the audit log
 * and the system. Each page is shown only to roles with its permission (the API checks again). */
import {
  Ban,
  BookOpen,
  ChevronLeft,
  ChevronRight,
  CircleCheck,
  Clock,
  DollarSign,
  Inbox,
  LogOut,
  Server,
  ShieldCheck,
  TrendingUp,
  UserCheck,
  Users,
} from "lucide-react";
import { Fragment, useState } from "react";
import { type AdminUser, type Role, v1 } from "../api";
import { useAuth } from "../auth";
import { ago, date, money, plural } from "../format";
import { useDebounced, useResource, useTitle } from "../hooks";
import { Link } from "../nav";
import { StatusBadge, subject } from "../ui/brand";
import { BarChart, Donut } from "../ui/charts";
import {
  Alert,
  Avatar,
  Badge,
  Button,
  Card,
  EmptyState,
  SearchBox,
  SectionTitle,
  SelectField,
  Spinner,
  Stat,
  Tabs,
} from "../ui/core";
import { Confirm, useToast } from "../ui/overlay";
import { Pager, SkeletonRows } from "../ui/table";

export function Forbidden() {
  return <Alert tone="danger">You do not have access to this page.</Alert>;
}

// ---------------------------------------------------------------------- overview

export function AdminOverview() {
  useTitle("Admin overview");
  const { can } = useAuth();
  const res = useResource(can("analytics:read") ? "admin:overview" : null, v1.overview, 30_000);
  if (!can("analytics:read")) return <Forbidden />;
  const o = res.data;
  if (!o)
    return <div className="page">{res.error ? <Alert tone="danger">{res.error.message}</Alert> : <Spinner />}</div>;
  const roleColors: Record<string, string> = { learner: "#8d93a8", educator: "#f4a261", admin: "var(--brand-solid)" };
  return (
    <div className="page admin">
      <header className="page-head">
        <div>
          <p className="page-kicker">Administration</p>
          <h1>Overview</h1>
        </div>
      </header>
      <div className="stat-row">
        <Stat
          icon={Users}
          label="People"
          value={o.users}
          sub={`+${o.new_users_7d} this week · ${o.active_users_7d} active`}
        />
        <Stat icon={BookOpen} label="Lessons" value={o.lessons} sub={`+${o.new_lessons_7d} this week`} tone="info" />
        <Stat
          icon={Clock}
          label="Hours learned"
          value={o.hours_learned}
          sub={`${plural(o.completions, "lesson")} finished`}
          tone="success"
        />
        <Stat
          icon={DollarSign}
          label="Spend (AI and voice)"
          value={money(o.ai_cost_usd)}
          sub={
            can("usage:read") ? (
              <Link href="/admin/credits">{money(o.ai_cost_7d_usd)} in the last 7 days</Link>
            ) : (
              `${money(o.ai_cost_7d_usd)} in the last 7 days`
            )
          }
          tone="warning"
        />
        <Stat
          icon={Inbox}
          label="Open tickets"
          value={o.open_tickets}
          sub={<Link href="/admin/support">Open the queue</Link>}
          tone="neutral"
        />
      </div>
      <div className="admin-grid">
        <Card>
          <SectionTitle title="Sign-ups" sub="Last 14 days" />
          <BarChart
            label="Sign-ups per day"
            data={o.daily.map((d) => ({ label: d.day.slice(8), value: d.signups, hint: d.day }))}
          />
        </Card>
        <Card>
          <SectionTitle title="Lessons created" sub="Last 14 days" />
          <BarChart
            label="Lessons per day"
            data={o.daily.map((d) => ({ label: d.day.slice(8), value: d.lessons, hint: d.day }))}
          />
        </Card>
        <Card>
          <SectionTitle title="Minutes learned" sub="Last 14 days" />
          <BarChart
            label="Minutes learned per day"
            unit=" min"
            data={o.daily.map((d) => ({ label: d.day.slice(8), value: d.minutes, hint: d.day }))}
          />
        </Card>
        <Card>
          <SectionTitle title="People by role" />
          <Donut
            label="People by role"
            parts={Object.entries(o.users_by_role).map(([k, v]) => ({
              label: k,
              value: v,
              color: roleColors[k] ?? "#8d93a8",
            }))}
          />
        </Card>
        <Card>
          <SectionTitle title="Lessons by subject" />
          <ul className="bar-list">
            {Object.entries(o.lessons_by_subject)
              .sort((a, b) => b[1] - a[1])
              .map(([k, v]) => {
                const s = subject(k);
                const max = Math.max(...Object.values(o.lessons_by_subject), 1);
                return (
                  <li key={k}>
                    <span>
                      <s.icon size={14} style={{ color: s.ink }} /> {s.label}
                    </span>
                    <span className="bar-list-bar">
                      <span style={{ width: `${(v / max) * 100}%`, background: s.ink }} />
                    </span>
                    <b>{v}</b>
                  </li>
                );
              })}
          </ul>
        </Card>
        <Card>
          <SectionTitle title="Lessons by status" />
          <ul className="kv-list">
            {Object.entries(o.lessons_by_status).map(([k, v]) => (
              <li key={k}>
                <StatusBadge status={k} />
                <b>{v}</b>
              </li>
            ))}
          </ul>
          <p className="muted small">{o.doubts} doubts answered in total.</p>
        </Card>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------- people

export function AdminUsers() {
  useTitle("People");
  const { can } = useAuth();
  const toast = useToast();
  const [q, setQ] = useState("");
  const [role, setRole] = useState("");
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const term = useDebounced(q, 250);
  const key = `admin:users:${term}:${role}:${status}:${offset}`;
  const res = useResource(can("user:manage") ? key : null, () => v1.users({ q: term, role, status, offset }));
  const [confirm, setConfirm] = useState<{ user: AdminUser; action: "suspend" | "activate" | "signout" } | null>(null);
  const [busy, setBusy] = useState(false);
  if (!can("user:manage")) return <Forbidden />;

  const setRoleOf = async (u: AdminUser, r: Role) => {
    try {
      await v1.patchUser(u.id, { role: r });
      toast.success(`${u.name} is now ${r === "admin" ? "an admin" : r === "educator" ? "an educator" : "a learner"}.`);
      void res.reload();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    }
  };
  const run = async () => {
    if (!confirm) return;
    setBusy(true);
    try {
      if (confirm.action === "signout") {
        const r = await v1.endUserSessions(confirm.user.id);
        toast.success(`Signed out of ${r.signed_out} device(s).`);
      } else {
        await v1.patchUser(confirm.user.id, { status: confirm.action === "suspend" ? "suspended" : "active" });
        toast.success(confirm.action === "suspend" ? "Account suspended and signed out." : "Account active again.");
      }
      setConfirm(null);
      void res.reload();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  const total = res.data?.total ?? 0;
  return (
    <div className="page admin">
      <header className="page-head">
        <div>
          <p className="page-kicker">Administration</p>
          <h1>People</h1>
          <p className="page-sub">{total} accounts</p>
        </div>
      </header>
      <div className="toolbar">
        <SearchBox
          value={q}
          onChange={(v) => {
            setQ(v);
            setOffset(0);
          }}
          placeholder="Search by name or email"
        />
        <div className="toolbar-right">
          <SelectField
            aria-label="Role"
            value={role}
            onChange={(e) => {
              setRole(e.target.value);
              setOffset(0);
            }}
            options={[
              { value: "", label: "All roles" },
              { value: "learner", label: "Learners" },
              { value: "educator", label: "Educators" },
              { value: "admin", label: "Admins" },
            ]}
          />
          <SelectField
            aria-label="Status"
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setOffset(0);
            }}
            options={[
              { value: "", label: "Any status" },
              { value: "active", label: "Active" },
              { value: "suspended", label: "Suspended" },
            ]}
          />
        </div>
      </div>
      <Card padded={false} className="table-card">
        <div className="table-wrap sticky-head">
          <table className="table fixed people-table">
            <colgroup>
              <col />
              <col className="w-role" />
              <col className="w-status" />
              <col className="w-count" />
              <col className="w-date" />
              <col className="w-seen" />
              <col className="w-actions" />
            </colgroup>
            <thead>
              <tr>
                <th>Person</th>
                <th>Role</th>
                <th>Status</th>
                <th className="num">Lessons</th>
                <th>Joined</th>
                <th>Last sign-in</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {res.loading && !res.data ? (
                <SkeletonRows cols={7} rows={6} />
              ) : (
                (res.data?.items ?? []).map((u) => (
                  <tr key={u.id}>
                    <td>
                      <span className="person">
                        <Avatar name={u.name} color={u.avatar} size={34} />
                        <span>
                          <b className="person-name">
                            <span className="truncate" title={u.name}>
                              {u.name}
                            </span>
                            {u.me && <Badge tone="neutral">You</Badge>}
                          </b>
                          <small className="truncate" title={u.email}>
                            {u.email}
                            {u.verified ? "" : " · unconfirmed"}
                          </small>
                        </span>
                      </span>
                    </td>
                    <td>
                      <select
                        className="ui-select compact"
                        aria-label={`Role of ${u.name}`}
                        value={u.role}
                        disabled={u.me}
                        onChange={(e) => void setRoleOf(u, e.target.value as Role)}
                      >
                        <option value="learner">Learner</option>
                        <option value="educator">Educator</option>
                        <option value="admin">Admin</option>
                      </select>
                    </td>
                    <td>
                      <Badge tone={u.status === "active" ? "success" : "danger"}>
                        {u.status === "active" ? "Active" : "Suspended"}
                      </Badge>
                    </td>
                    <td className="num">{u.lessons.toLocaleString()}</td>
                    <td title={date(u.created_at, true)}>{date(u.created_at)}</td>
                    <td title={u.last_login_at ? date(u.last_login_at, true) : undefined}>{ago(u.last_login_at)}</td>
                    <td className="cell-actions">
                      {!u.me && (
                        <span className="row-actions">
                          <button
                            type="button"
                            className="ui-icon-btn"
                            onClick={() => setConfirm({ user: u, action: "signout" })}
                            aria-label={`Sign ${u.name} out everywhere`}
                            title="Sign out everywhere"
                          >
                            <LogOut size={17} />
                          </button>
                          {u.status === "active" ? (
                            <Button
                              size="sm"
                              variant="ghost"
                              icon={Ban}
                              className="danger-ghost"
                              onClick={() => setConfirm({ user: u, action: "suspend" })}
                            >
                              Suspend
                            </Button>
                          ) : (
                            <Button
                              size="sm"
                              variant="ghost"
                              icon={UserCheck}
                              onClick={() => setConfirm({ user: u, action: "activate" })}
                            >
                              Activate
                            </Button>
                          )}
                        </span>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        {res.data && res.data.items.length === 0 && (
          <EmptyState icon={Users} title="No one matches" text="Try another name or email, role or status." />
        )}
      </Card>
      <Pager offset={offset} size={25} total={total} onChange={setOffset} />
      <Confirm
        open={!!confirm}
        onClose={() => setConfirm(null)}
        onConfirm={() => void run()}
        busy={busy}
        danger={confirm?.action !== "activate"}
        title={
          confirm?.action === "suspend"
            ? "Suspend this account?"
            : confirm?.action === "activate"
              ? "Activate this account?"
              : "Sign out everywhere?"
        }
        text={
          confirm?.action === "suspend"
            ? `${confirm.user.name} is signed out at once and cannot sign in until you activate the account.`
            : confirm?.action === "activate"
              ? `${confirm?.user.name} can sign in again.`
              : `${confirm?.user.name} is signed out on every device.`
        }
        confirmLabel={
          confirm?.action === "suspend" ? "Suspend" : confirm?.action === "activate" ? "Activate" : "Sign out"
        }
      />
    </div>
  );
}

/** The pager every admin list shares (it lives with the other table parts). */
export { Pager };

// ---------------------------------------------------------------------- support queue

export function AdminSupport() {
  useTitle("Support queue");
  const { can } = useAuth();
  const [status, setStatus] = useState("active");
  const [q, setQ] = useState("");
  const term = useDebounced(q, 250);
  const res = useResource(
    can("support:manage") ? `admin:tickets:${status}:${term}` : null,
    () => v1.queue({ status, q: term }),
    15_000,
  );
  if (!can("support:manage")) return <Forbidden />;
  return (
    <div className="page admin">
      <header className="page-head">
        <div>
          <p className="page-kicker">Administration</p>
          <h1>Support queue</h1>
        </div>
      </header>
      <div className="toolbar">
        <Tabs
          label="Tickets"
          value={status}
          onChange={setStatus}
          tabs={[
            { value: "active", label: "Needs attention" },
            { value: "open", label: "Open" },
            { value: "pending", label: "Awaiting learner" },
            { value: "resolved", label: "Resolved" },
            { value: "closed", label: "Closed" },
          ]}
        />
        <SearchBox value={q} onChange={setQ} placeholder="Search subject or email" />
      </div>
      {res.loading && !res.data ? (
        <Spinner />
      ) : !res.data?.items.length ? (
        <EmptyState icon={CircleCheck} title="Inbox zero" text="No tickets here." />
      ) : (
        <Card padded={false}>
          <ul className="queue">
            {res.data.items.map((t) => (
              <li key={t.id}>
                <Link href={`/admin/support/${t.id}`}>
                  <span className={`prio prio-${t.priority}`} title={`${t.priority} priority`} />
                  <span className="queue-main">
                    <b>{t.subject}</b>
                    <small>
                      {t.ref} · {t.name ?? t.email} · {t.category} · updated {ago(t.updated_at)}
                    </small>
                  </span>
                  <Badge
                    tone={
                      t.status === "open"
                        ? "info"
                        : t.status === "pending"
                          ? "warning"
                          : t.status === "resolved"
                            ? "success"
                            : "neutral"
                    }
                  >
                    {t.status}
                  </Badge>
                </Link>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------- audit

const ACTIONS = [
  { value: "", label: "All events" },
  { value: "login", label: "Sign-ins" },
  { value: "logout", label: "Sign-outs" },
  { value: "session.", label: "Sessions" },
  { value: "password.", label: "Passwords" },
  { value: "user.", label: "People" },
  { value: "lesson.", label: "Lessons" },
  { value: "explore.", label: "Explore" },
  { value: "objects.", label: "Objects Warehouse" },
  { value: "credits.", label: "Credits" },
  { value: "usage.", label: "Exports" },
  { value: "ticket.", label: "Support" },
];
const AUDIT_PAGE = 50;

/** An event's kind at a glance: failures and removals red, sign-ins blue, accounts amber, lessons
 * violet, the warehouse green (fixed colors: the app's own color could be any of them). */
function eventTone(action: string): string {
  if (/failed|reused|locked|delete|reject/.test(action)) return "danger";
  if (/^(login|logout|session)/.test(action)) return "info";
  if (/^(user|password|credits)\./.test(action)) return "warning";
  if (/^(lesson|explore)\./.test(action)) return "lesson";
  if (/^objects\./.test(action)) return "success";
  return "neutral";
}

/** An event's details on one line ("to: public · bulk: true"). */
function detailLine(d: Record<string, unknown>): string {
  return Object.entries(d)
    .map(([k, v]) => `${k.replaceAll("_", " ")}: ${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(" · ");
}

export function AdminAudit() {
  useTitle("Audit log");
  const { can } = useAuth();
  const [action, setAction] = useState("");
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState<ReadonlySet<string>>(() => new Set());
  const res = useResource(can("audit:read") ? `admin:audit:${action}:${offset}` : null, () =>
    v1.audit({ action, offset }),
  );
  if (!can("audit:read")) return <Forbidden />;
  const items = res.data?.items ?? [];
  const toggle = (id: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  return (
    <div className="page admin">
      <header className="page-head">
        <div>
          <p className="page-kicker">Administration</p>
          <h1>Audit log</h1>
          <p className="page-sub">
            Security and administration events, newest first. Open an event's details to read them whole.
          </p>
        </div>
        <SelectField
          aria-label="Event type"
          value={action}
          onChange={(e) => {
            setAction(e.target.value);
            setOffset(0);
          }}
          options={ACTIONS}
        />
      </header>
      <Card padded={false} className="table-card">
        <div className="table-wrap sticky-head">
          <table className="table fixed audit-table">
            <colgroup>
              <col className="w-when" />
              <col className="w-event" />
              <col className="w-by" />
              <col className="w-target" />
              <col className="w-ip" />
              <col />
            </colgroup>
            <thead>
              <tr>
                <th>When</th>
                <th>Event</th>
                <th>By</th>
                <th>Target</th>
                <th>Address</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {res.loading && !res.data ? (
                <SkeletonRows cols={6} />
              ) : (
                items.map((e) => {
                  const line = Object.keys(e.detail).length ? detailLine(e.detail) : "";
                  const shown = open.has(e.id);
                  return (
                    <Fragment key={e.id}>
                      <tr className={shown ? "expanded" : undefined}>
                        <td title={date(e.created_at, true)}>{ago(e.created_at)}</td>
                        <td>
                          <code className={`audit-event tone-${eventTone(e.action)}`} title={e.action}>
                            {e.action}
                          </code>
                        </td>
                        <td title={e.actor ?? undefined}>{e.actor ?? <span className="muted">–</span>}</td>
                        <td title={e.target}>
                          <code className="muted">{e.target}</code>
                        </td>
                        <td title={e.ip}>{e.ip}</td>
                        <td>
                          {line ? (
                            <button
                              type="button"
                              className="audit-detail"
                              aria-expanded={shown}
                              onClick={() => toggle(e.id)}
                              title={shown ? "Hide the details" : "Show the details"}
                            >
                              <ChevronRight size={14} className="audit-chevron" />
                              <span className="truncate">{line}</span>
                            </button>
                          ) : (
                            <span className="muted">–</span>
                          )}
                        </td>
                      </tr>
                      {shown && (
                        <tr className="detail-row">
                          <td colSpan={6}>
                            <pre>{JSON.stringify(e.detail, null, 2)}</pre>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
        {res.data && items.length === 0 && (
          <EmptyState icon={ShieldCheck} title="No events" text="Nothing of this kind has been recorded." />
        )}
      </Card>
      <nav className="pager" aria-label="Pages">
        <span className="pager-range">
          {items.length > 0 && (
            <>
              <b>
                {(offset + 1).toLocaleString()}–{(offset + items.length).toLocaleString()}
              </b>
              , newest first
            </>
          )}
        </span>
        <span className="pager-steps">
          <Button
            size="sm"
            variant="secondary"
            icon={ChevronLeft}
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - AUDIT_PAGE))}
          >
            Newer
          </Button>
          <Button
            size="sm"
            variant="secondary"
            iconRight={ChevronRight}
            disabled={!res.data?.has_more}
            onClick={() => setOffset(offset + AUDIT_PAGE)}
          >
            Older
          </Button>
        </span>
      </nav>
    </div>
  );
}

// ---------------------------------------------------------------------- system

export function AdminSystem() {
  useTitle("System");
  const { can } = useAuth();
  const res = useResource(can("analytics:read") ? "admin:system" : null, v1.system, 5000);
  if (!can("analytics:read")) return <Forbidden />;
  const s = res.data;
  if (!s)
    return <div className="page">{res.error ? <Alert tone="danger">{res.error.message}</Alert> : <Spinner />}</div>;
  const healthy = s.engine_workers.length > 0 && s.engine_workers.every(Boolean);
  const errorRate = s.requests.requests ? (s.requests.errors / s.requests.requests) * 100 : 0;
  return (
    <div className="page admin">
      <header className="page-head">
        <div>
          <p className="page-kicker">Administration</p>
          <h1>System</h1>
          <p className="page-sub">
            Up for {Math.round(s.requests.uptime_s / 60)} minutes · {s.environment}
          </p>
        </div>
        <Badge tone={healthy ? "success" : "danger"}>{healthy ? "All systems normal" : "Attention needed"}</Badge>
      </header>
      <div className="stat-row">
        <Stat
          icon={Server}
          label="Engine workers"
          value={`${s.engine_workers.filter(Boolean).length} / ${s.engine_workers.length}`}
          sub="healthy"
          tone={healthy ? "success" : "warning"}
        />
        <Stat
          icon={TrendingUp}
          label="Requests per minute"
          value={s.requests.per_minute}
          sub={`${s.requests.requests} since start`}
          tone="info"
        />
        <Stat
          icon={Ban}
          label="Error rate"
          value={`${errorRate.toFixed(2)}%`}
          sub={`${s.requests.errors} server errors`}
          tone={errorRate > 1 ? "warning" : "neutral"}
        />
      </div>
      <div className="admin-grid two">
        <Card>
          <SectionTitle title="Services" />
          <ul className="kv-list">
            <li>
              <span>Database</span>
              <b>{s.database}</b>
            </li>
            <li>
              <span>Mail</span>
              <b>{s.mail}</b>
            </li>
            <li>
              <span>Lesson AI (OpenAI key)</span>
              <Badge tone={s.openai_key ? "success" : "danger"}>{s.openai_key ? "Configured" : "Missing"}</Badge>
            </li>
            <li>
              <span>Narration voice</span>
              <Badge tone={s.tts ? "success" : "neutral"}>{s.tts ? "On" : "Off"}</Badge>
            </li>
            <li>
              <span>Doubts</span>
              <Badge tone={s.doubts ? "success" : "neutral"}>{s.doubts ? "On" : "Off"}</Badge>
            </li>
          </ul>
        </Card>
        <Card>
          <SectionTitle title="Busiest routes" sub="Since the API started" />
          <div className="table-wrap">
            <table className="table compact">
              <thead>
                <tr>
                  <th>Route</th>
                  <th className="num">Calls</th>
                  <th className="num">p50</th>
                  <th className="num">p95</th>
                  <th className="num">5xx</th>
                </tr>
              </thead>
              <tbody>
                {s.requests.routes.map((r) => (
                  <tr key={r.route}>
                    <td>
                      <code>{r.route}</code>
                    </td>
                    <td className="num">{r.count}</td>
                    <td className="num">{r.p50_ms != null ? `${r.p50_ms} ms` : "stream"}</td>
                    <td className="num">{r.p95_ms != null ? `${r.p95_ms} ms` : "–"}</td>
                    <td className="num">{r.errors}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>
    </div>
  );
}
