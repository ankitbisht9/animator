/** Administration: the platform at a glance, people, all lessons, the support queue, the audit log
 * and the system. Each page is shown only to roles with its permission (the API checks again). */
import {
  Ban,
  BookOpen,
  CircleCheck,
  Clock,
  DollarSign,
  ExternalLink,
  Inbox,
  LogOut,
  RotateCcw,
  Server,
  Star,
  Trash,
  TrendingUp,
  UserCheck,
  Users,
} from "lucide-react";
import { useState } from "react";
import { type AdminUser, type Lesson, type Role, v1 } from "../api";
import { useAuth } from "../auth";
import { ago, date, money, plural } from "../format";
import { useDebounced, useResource, useTitle } from "../hooks";
import { Link, navigate } from "../nav";
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

function Forbidden() {
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
  const roleColors: Record<string, string> = { learner: "#8d93a8", educator: "#f4a261", admin: "#d90429" };
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
          label="AI spend"
          value={money(o.ai_cost_usd)}
          sub={`${money(o.ai_cost_7d_usd)} this week`}
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
      <Card padded={false}>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Person</th>
                <th>Role</th>
                <th>Status</th>
                <th>Lessons</th>
                <th>Joined</th>
                <th>Last sign-in</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {(res.data?.items ?? []).map((u) => (
                <tr key={u.id}>
                  <td>
                    <span className="person">
                      <Avatar name={u.name} color={u.avatar} size={32} />
                      <span>
                        <b>
                          {u.name} {u.me && <Badge tone="neutral">You</Badge>}
                        </b>
                        <small>
                          {u.email} {u.verified ? "" : "· unconfirmed"}
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
                  <td>{u.lessons}</td>
                  <td>{date(u.created_at)}</td>
                  <td>{ago(u.last_login_at)}</td>
                  <td className="table-actions">
                    {!u.me && (
                      <>
                        <Button
                          size="sm"
                          variant="ghost"
                          icon={LogOut}
                          onClick={() => setConfirm({ user: u, action: "signout" })}
                          aria-label={`Sign ${u.name} out everywhere`}
                        />
                        {u.status === "active" ? (
                          <Button
                            size="sm"
                            variant="ghost"
                            icon={Ban}
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
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {res.loading && !res.data && (
          <div className="table-empty">
            <Spinner />
          </div>
        )}
        {res.data && res.data.items.length === 0 && <div className="table-empty">No one matches.</div>}
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

function Pager({
  offset,
  size,
  total,
  onChange,
}: {
  offset: number;
  size: number;
  total: number;
  onChange: (o: number) => void;
}) {
  if (total <= size) return null;
  const page = Math.floor(offset / size) + 1;
  const pages = Math.ceil(total / size);
  return (
    <div className="pager">
      <Button size="sm" variant="secondary" disabled={page <= 1} onClick={() => onChange(Math.max(0, offset - size))}>
        Previous
      </Button>
      <span>
        Page {page} of {pages}
      </span>
      <Button size="sm" variant="secondary" disabled={page >= pages} onClick={() => onChange(offset + size)}>
        Next
      </Button>
    </div>
  );
}

// ---------------------------------------------------------------------- lessons

export function AdminLessons() {
  useTitle("All lessons");
  const { can } = useAuth();
  const toast = useToast();
  const [q, setQ] = useState("");
  const [tab, setTab] = useState<"all" | "public" | "deleted">("all");
  const [offset, setOffset] = useState(0);
  const term = useDebounced(q, 250);
  const key = `admin:lessons:${term}:${tab}:${offset}`;
  const res = useResource(can("lesson:any") ? key : null, () =>
    v1.adminLessons({
      q: term,
      visibility: tab === "public" ? "public" : undefined,
      deleted: tab === "deleted",
      offset,
    }),
  );
  if (!can("lesson:any")) return <Forbidden />;
  const moderate = async (
    l: Lesson,
    body: { visibility?: "private" | "unlisted" | "public"; featured?: boolean; deleted?: boolean },
    done: string,
  ) => {
    try {
      await v1.moderate(l.id, body);
      toast.success(done);
      void res.reload();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    }
  };
  const total = res.data?.total ?? 0;
  return (
    <div className="page admin">
      <header className="page-head">
        <div>
          <p className="page-kicker">Administration</p>
          <h1>All lessons</h1>
          <p className="page-sub">{total} lessons</p>
        </div>
      </header>
      <div className="toolbar">
        <Tabs
          label="Lessons"
          value={tab}
          onChange={(v) => {
            setTab(v);
            setOffset(0);
          }}
          tabs={[
            { value: "all", label: "All" },
            { value: "public", label: "Public" },
            { value: "deleted", label: "Deleted" },
          ]}
        />
        <SearchBox
          value={q}
          onChange={(v) => {
            setQ(v);
            setOffset(0);
          }}
          placeholder="Search lessons"
        />
      </div>
      <Card padded={false}>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Lesson</th>
                <th>Owner</th>
                <th>Status</th>
                <th>Sharing</th>
                <th>Views</th>
                <th>AI cost</th>
                <th>Created</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {(res.data?.items ?? []).map((l) => (
                <tr key={l.id}>
                  <td>
                    <Link href={`/lessons/${l.id}`} className="table-title">
                      <b>{l.title}</b>
                      <small>
                        {subject(l.subject).label} · {l.id}
                      </small>
                    </Link>
                  </td>
                  <td>{l.owner?.name ?? <span className="muted">nobody</span>}</td>
                  <td>
                    <StatusBadge status={l.status} />
                  </td>
                  <td>
                    {l.visibility}
                    {l.featured && (
                      <Badge tone="brand" icon={Star}>
                        Featured
                      </Badge>
                    )}
                  </td>
                  <td>{l.views}</td>
                  <td>{money(l.cost_usd)}</td>
                  <td>{date(l.created_at)}</td>
                  <td className="table-actions">
                    <Button
                      size="sm"
                      variant="ghost"
                      icon={ExternalLink}
                      onClick={() => navigate(`/admin/raw/${l.id}`)}
                      aria-label="Pipeline view"
                    />
                    {tab === "deleted" ? (
                      <Button
                        size="sm"
                        variant="ghost"
                        icon={RotateCcw}
                        onClick={() => void moderate(l, { deleted: false }, "Lesson restored.")}
                      >
                        Restore
                      </Button>
                    ) : (
                      <>
                        {l.visibility === "public" && (
                          <Button
                            size="sm"
                            variant="ghost"
                            icon={Star}
                            onClick={() =>
                              void moderate(
                                l,
                                { featured: !l.featured },
                                l.featured ? "No longer featured." : "Featured in Explore.",
                              )
                            }
                          >
                            {l.featured ? "Unfeature" : "Feature"}
                          </Button>
                        )}
                        {l.visibility === "public" && (
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => void moderate(l, { visibility: "private" }, "Removed from Explore.")}
                          >
                            Unpublish
                          </Button>
                        )}
                        <Button
                          size="sm"
                          variant="ghost"
                          icon={Trash}
                          onClick={() => void moderate(l, { deleted: true }, "Lesson removed.")}
                          aria-label="Remove"
                        />
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {res.data && res.data.items.length === 0 && <div className="table-empty">No lessons.</div>}
      </Card>
      <Pager offset={offset} size={25} total={total} onChange={setOffset} />
    </div>
  );
}

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

const ACTIONS = ["", "login", "user.", "password.", "session.", "lesson.", "ticket."];

export function AdminAudit() {
  useTitle("Audit log");
  const { can } = useAuth();
  const [action, setAction] = useState("");
  const [offset, setOffset] = useState(0);
  const res = useResource(can("audit:read") ? `admin:audit:${action}:${offset}` : null, () =>
    v1.audit({ action, offset }),
  );
  if (!can("audit:read")) return <Forbidden />;
  return (
    <div className="page admin">
      <header className="page-head">
        <div>
          <p className="page-kicker">Administration</p>
          <h1>Audit log</h1>
          <p className="page-sub">Security and administration events, newest first.</p>
        </div>
        <SelectField
          aria-label="Event type"
          value={action}
          onChange={(e) => {
            setAction(e.target.value);
            setOffset(0);
          }}
          options={ACTIONS.map((a) => ({ value: a, label: a ? a.replace(/\.$/, "") : "All events" }))}
        />
      </header>
      <Card padded={false}>
        <div className="table-wrap">
          <table className="table">
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
              {(res.data?.items ?? []).map((e) => (
                <tr key={e.id}>
                  <td title={date(e.created_at, true)}>{ago(e.created_at)}</td>
                  <td>
                    <code>{e.action}</code>
                  </td>
                  <td>{e.actor ?? <span className="muted">–</span>}</td>
                  <td>
                    <code className="muted">{e.target.slice(0, 18)}</code>
                  </td>
                  <td>{e.ip}</td>
                  <td className="muted small">{Object.keys(e.detail).length ? JSON.stringify(e.detail) : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <div className="pager">
        <Button
          size="sm"
          variant="secondary"
          disabled={offset === 0}
          onClick={() => setOffset(Math.max(0, offset - 50))}
        >
          Newer
        </Button>
        <Button size="sm" variant="secondary" disabled={!res.data?.has_more} onClick={() => setOffset(offset + 50)}>
          Older
        </Button>
      </div>
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
                  <th>Calls</th>
                  <th>p50</th>
                  <th>p95</th>
                  <th>5xx</th>
                </tr>
              </thead>
              <tbody>
                {s.requests.routes.map((r) => (
                  <tr key={r.route}>
                    <td>
                      <code>{r.route}</code>
                    </td>
                    <td>{r.count}</td>
                    <td>{r.p50_ms != null ? `${r.p50_ms} ms` : "stream"}</td>
                    <td>{r.p95_ms != null ? `${r.p95_ms} ms` : "–"}</td>
                    <td>{r.errors}</td>
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
