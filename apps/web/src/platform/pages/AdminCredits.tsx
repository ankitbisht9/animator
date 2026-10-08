/** Credits and cost (ADR-053): what the platform spent on AI and voice today, this week, this month and
 * so far; who spent it (making lessons, learning) against their monthly credits; which lessons cost the
 * most; the ledger as a spreadsheet. Changing someone's credits needs `credits:manage` (the API checks). */
import { Ban, Coins, Download, Gauge, ReceiptText, Settings2, TriangleAlert, Users, Wallet } from "lucide-react";
import { useEffect, useState } from "react";
import {
  type Credits,
  downloadUsageCsv,
  type PersonCredits,
  type Role,
  type UsageDay,
  type UsageKind,
  type UsagePeriod,
  type UsageSummary,
  type UsageTotals,
  v1,
} from "../api";
import { useAuth } from "../auth";
import { ago, credits, date, plural, usd } from "../format";
import { invalidate, useDebounced, useResource, useTitle } from "../hooks";
import { Link } from "../nav";
import { subject } from "../ui/brand";
import { Donut } from "../ui/charts";
import {
  Alert,
  Avatar,
  Badge,
  Button,
  Card,
  Checkbox,
  EmptyState,
  ProgressBar,
  SearchBox,
  SectionTitle,
  Segmented,
  SelectField,
  Spinner,
  Tabs,
  TextField,
} from "../ui/core";
import { Modal, useToast } from "../ui/overlay";
import { Forbidden, Pager } from "./Admin";

const PERIODS: { value: UsagePeriod; label: string; noun: string }[] = [
  { value: "today", label: "Today", noun: "today" },
  { value: "week", label: "This week", noun: "this week" },
  { value: "month", label: "This month", noun: "this month" },
  { value: "total", label: "All time", noun: "so far" },
];

const KINDS: { kind: UsageKind; label: string; color: string }[] = [
  { kind: "lesson_ai", label: "Lessons: AI", color: "#d90429" },
  { kind: "lesson_voice", label: "Lessons: voice", color: "#f4a261" },
  { kind: "doubt_ai", label: "Doubts: AI", color: "#2554c7" },
  { kind: "doubt_voice", label: "Doubts: voice", color: "#7ea6ff" },
  { kind: "transcription", label: "Speech input", color: "#13795b" },
];

export const KIND_LABEL: Record<UsageKind, string> = {
  lesson_ai: "Lesson: AI",
  lesson_voice: "Lesson: voice",
  doubt_ai: "Doubt answer: AI",
  doubt_voice: "Doubt answer: voice",
  transcription: "Speech input",
  adjustment: "Adjustment",
};

const ROLE_LABEL: Record<Role, string> = { learner: "Learners", educator: "Educators", admin: "Admins" };

const VOICE_NAME: Record<string, string> = {
  elevenlabs: "ElevenLabs",
  openai: "OpenAI",
  google: "Google",
  azure: "Azure",
  edge: "Edge",
  kokoro: "Kokoro (this computer)",
};

function periodOf(value: UsagePeriod) {
  return PERIODS.find((p) => p.value === value) ?? (PERIODS[2] as (typeof PERIODS)[number]);
}

export function AdminCredits() {
  useTitle("Credits & cost");
  const { can } = useAuth();
  const toast = useToast();
  const allowed = can("usage:read");
  const summary = useResource(allowed ? "admin:usage:summary" : null, () => v1.usageSummary(30), 30_000);
  const [period, setPeriod] = useState<UsagePeriod>("month");
  const [tab, setTab] = useState<"people" | "lessons">("people");
  const [person, setPerson] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  if (!allowed) return <Forbidden />;
  const s = summary.data;
  if (!s)
    return (
      <div className="page">{summary.error ? <Alert tone="danger">{summary.error.message}</Alert> : <Spinner />}</div>
    );
  const chosen = periodOf(period);
  const totals = s.periods[period];

  const exportCsv = async () => {
    setExporting(true);
    try {
      await downloadUsageCsv(period);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setExporting(false);
    }
  };
  const changed = () => {
    void summary.reload();
    invalidate("admin:usage:");
  };

  return (
    <div className="page admin usage">
      <header className="page-head">
        <div>
          <p className="page-kicker">Administration</p>
          <h1>Credits &amp; cost</h1>
          <p className="page-sub">
            What making lessons and learning cost, in dollars and in credits (1 credit = {usd(s.credit_usd)}). Days
            follow {s.timezone}; weeks start on Monday.
          </p>
        </div>
        <div className="page-head-actions">
          <Button variant="secondary" icon={Download} loading={exporting} onClick={() => void exportCsv()}>
            Export {chosen.label.toLowerCase()} (CSV)
          </Button>
        </div>
      </header>

      <PeriodTiles summary={s} value={period} onChange={setPeriod} />

      <div className="admin-grid usage-grid">
        <Card className="usage-wide">
          <SectionTitle title="Spend per day" sub="Last 30 days" />
          <DailySpend days={s.daily} height={250} />
          <SplitLegend />
        </Card>
        <Card>
          <SectionTitle title="Where it went" sub={chosen.label} />
          <KindsDonut totals={totals} noun={chosen.noun} />
        </Card>
        <Card>
          <SectionTitle title="Monthly credits" sub={`They renew on ${date(s.resets_at)}`} />
          <ul className="kv-list">
            <li>
              <span>People spending this month</span>
              <b>{s.people.spending_this_month}</b>
            </li>
            <li>
              <span>Used all their credits</span>
              {s.people.over_limit ? <Badge tone="danger">{s.people.over_limit}</Badge> : <b>0</b>}
            </li>
            <li>
              <span>Above 80% of their credits</span>
              {s.people.near_limit ? <Badge tone="warning">{s.people.near_limit}</Badge> : <b>0</b>}
            </li>
            <li>
              <span>May not spend (blocked)</span>
              <b>{s.people.blocked}</b>
            </li>
          </ul>
          <h3 className="usage-h3">Each month, by role</h3>
          <ul className="kv-list">
            {(Object.keys(ROLE_LABEL) as Role[]).map((r) => {
              const n = s.allowances[r];
              return (
                <li key={r}>
                  <span>{ROLE_LABEL[r]}</span>
                  <b>{n == null ? "No limit" : `${credits(n)} credits`}</b>
                </li>
              );
            })}
          </ul>
        </Card>
        <Card>
          <SectionTitle title="What things cost" sub="This month, on average" />
          <ul className="kv-list">
            <li>
              <span>Making a lesson</span>
              <b>{usd(s.averages.lesson_usd)}</b>
            </li>
            <li>
              <span>Answering a doubt</span>
              <b>{usd(s.averages.doubt_usd)}</b>
            </li>
            <li>
              <span>By accounts since closed (all time)</span>
              <b>{usd(s.unattributed_usd)}</b>
            </li>
          </ul>
          <h3 className="usage-h3">Prices behind the estimates</h3>
          <ul className="kv-list">
            {s.prices.voices.map((v) => (
              <li key={v.provider}>
                <span>
                  Voice: {VOICE_NAME[v.provider] ?? v.provider}
                  {v.configured ? "" : " (before)"}
                </span>
                <b>{v.usd_per_million_chars > 0 ? `${usd(v.usd_per_million_chars)} / 1M characters` : "Free"}</b>
              </li>
            ))}
            <li>
              <span>Speech input</span>
              <b>{usd(s.prices.transcribe_usd_per_min)} / minute</b>
            </li>
          </ul>
          <p className="muted small">Set them to your plan's prices with PLATFORM_VOICE_PRICES.</p>
        </Card>
        <Card>
          <SectionTitle title="Top spenders" sub="This month" />
          {s.top.length === 0 ? (
            <p className="muted small">Nobody has spent anything this month yet.</p>
          ) : (
            <ol className="top-spenders">
              {s.top.map((t) => (
                <li key={t.id}>
                  <button type="button" onClick={() => setPerson(t.id)}>
                    <span className="top-spenders-name">
                      <b>{t.name}</b>
                      <small>{t.email}</small>
                    </span>
                    <span className="top-spenders-num">
                      <b>{usd(t.cost_usd)}</b>
                      <small>{credits(t.credits)} credits</small>
                    </span>
                  </button>
                </li>
              ))}
            </ol>
          )}
        </Card>
      </div>

      <div className="toolbar usage-tabs">
        <Tabs
          label="Spend by"
          value={tab}
          onChange={setTab}
          tabs={[
            { value: "people", label: `People · ${chosen.label.toLowerCase()}`, icon: Users },
            { value: "lessons", label: `Lessons · ${chosen.label.toLowerCase()}`, icon: ReceiptText },
          ]}
        />
      </div>
      {tab === "people" ? <PeopleTable period={period} onOpen={setPerson} /> : <LessonsTable period={period} />}
      {person && <PersonPanel id={person} onClose={() => setPerson(null)} onChanged={changed} />}
    </div>
  );
}

// ---------------------------------------------------------------------- the period tiles

function PeriodTiles({
  summary,
  value,
  onChange,
}: {
  summary: UsageSummary;
  value: UsagePeriod;
  onChange: (p: UsagePeriod) => void;
}) {
  return (
    <fieldset className="usage-periods">
      <legend className="sr-only">Period</legend>
      {PERIODS.map((p) => {
        const t = summary.periods[p.value];
        return (
          <button
            key={p.value}
            type="button"
            aria-pressed={value === p.value}
            className={`usage-period ${value === p.value ? "on" : ""}`}
            onClick={() => onChange(p.value)}
          >
            <span className="usage-period-label">{p.label}</span>
            <span className="usage-period-value">{usd(t.cost_usd)}</span>
            <span className="usage-period-sub">
              {credits(t.credits)} credits · {plural(t.entries, "charge")}
            </span>
            <Split totals={t} />
          </button>
        );
      })}
    </fieldset>
  );
}

/** Making lessons vs learning, as one thin bar. */
function Split({ totals }: { totals: UsageTotals }) {
  const create = Math.max(0, totals.by_area.create.cost_usd);
  const learn = Math.max(0, totals.by_area.learn.cost_usd);
  const all = create + learn;
  return (
    <span className="usage-split" aria-hidden="true">
      {all > 0 && (
        <>
          <span className="split-create" style={{ width: `${(create / all) * 100}%` }} />
          <span className="split-learn" style={{ width: `${(learn / all) * 100}%` }} />
        </>
      )}
    </span>
  );
}

function SplitLegend() {
  return (
    <p className="usage-legend">
      <span className="dot split-create" /> Making lessons <span className="dot split-learn" /> Learning (doubts, speech
      input)
    </p>
  );
}

function DailySpend({ days, height = 170 }: { days: UsageDay[]; height?: number }) {
  const max = Math.max(0.000_001, ...days.map((d) => Math.max(0, d.create_usd) + Math.max(0, d.learn_usd)));
  const w = 100 / Math.max(1, days.length);
  return (
    <figure className="ui-bars usage-days">
      <svg
        viewBox={`0 0 100 ${height}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={`Spend per day over the last ${days.length} days`}
        style={{ height }}
      >
        {[0.25, 0.5, 0.75].map((g) => (
          <line key={g} x1="0" x2="100" y1={height * g} y2={height * g} className="ui-bars-grid" />
        ))}
        {days.map((d, i) => {
          const hc = (Math.max(0, d.create_usd) / max) * (height - 6);
          const hl = (Math.max(0, d.learn_usd) / max) * (height - 6);
          const x = i * w + w * 0.18;
          return (
            <g key={d.day}>
              <title>
                {`${d.day}: ${usd(d.cost_usd)} (${credits(d.credits)} credits) · making lessons ${usd(d.create_usd)} · learning ${usd(d.learn_usd)}`}
              </title>
              <rect x={i * w} y={0} width={w} height={height} fill="transparent" />
              {hc > 0 && (
                <rect
                  x={x}
                  y={height - hc}
                  width={w * 0.64}
                  height={Math.max(hc, 1.5)}
                  rx="1"
                  className="usage-bar split-create"
                />
              )}
              {hl > 0 && (
                <rect
                  x={x}
                  y={height - hc - Math.max(hl, 1.5)}
                  width={w * 0.64}
                  height={Math.max(hl, 1.5)}
                  rx="1"
                  className="usage-bar split-learn"
                />
              )}
            </g>
          );
        })}
      </svg>
      <figcaption className="ui-bars-axis">
        {days.map((d, i) => (
          <span key={d.day} style={{ visibility: i % 5 === (days.length - 1) % 5 ? "visible" : "hidden" }}>
            {d.day.slice(8)}
          </span>
        ))}
      </figcaption>
    </figure>
  );
}

function KindsDonut({ totals, noun }: { totals: UsageTotals; noun: string }) {
  const parts = KINDS.map((k) => ({
    label: k.label,
    value: totals.by_kind[k.kind]?.cost_usd ?? 0,
    color: k.color,
  })).filter((p) => p.value > 0);
  if (!parts.length) return <EmptyState icon={Wallet} title="Nothing spent" text={`No AI or voice spend ${noun}.`} />;
  const adjusted = totals.by_kind.adjustment;
  return (
    <>
      <Donut label="Spend by kind" parts={parts} format={usd} />
      {totals.estimated_usd > 0 && (
        <p className="muted small">
          Voice and speech input ({usd(totals.estimated_usd)}) are estimated from the characters spoken and the length
          of recordings; the AI is counted from each call's tokens.
        </p>
      )}
      {adjusted && adjusted.credits !== 0 && (
        <p className="muted small">
          {plural(adjusted.count, "adjustment")} {noun}: {credits(Math.abs(adjusted.credits))} credits{" "}
          {adjusted.credits < 0 ? "given back" : "charged"} in all (no dollars).
        </p>
      )}
    </>
  );
}

// ---------------------------------------------------------------------- people

function CreditMeter({
  c,
}: {
  c: PersonCredits | (Credits & { role_limit?: number | null; percent?: number | null });
}) {
  if (c.mode === "blocked")
    return (
      <Badge tone="danger" icon={Ban}>
        Blocked
      </Badge>
    );
  if (c.limit == null)
    return (
      <span className="credit-meter">
        <span>
          <b>{credits(c.used)}</b> <small>· no limit</small>
        </span>
      </span>
    );
  const pct = c.limit > 0 ? Math.max(0, c.used) / c.limit : 1;
  return (
    <span className={`credit-meter ${c.exhausted ? "over" : pct >= 0.8 ? "near" : ""}`}>
      <span>
        <b>{credits(c.used)}</b> <small>of {credits(c.limit)}</small>
        {c.mode === "custom" && <small className="credit-own"> · own</small>}
      </span>
      <ProgressBar value={pct} label={`${Math.round(pct * 100)}% of this month's credits used`} />
    </span>
  );
}

function PeopleTable({ period, onOpen }: { period: UsagePeriod; onOpen: (id: string) => void }) {
  const [q, setQ] = useState("");
  const [role, setRole] = useState("");
  const [sort, setSort] = useState("credits");
  const [active, setActive] = useState(true);
  const [offset, setOffset] = useState(0);
  const term = useDebounced(q, 250);
  // biome-ignore lint/correctness/useExhaustiveDependencies: reset when these change
  useEffect(() => setOffset(0), [period, term, role, sort, active]);
  const key = `admin:usage:people:${period}:${term}:${role}:${sort}:${active}:${offset}`;
  const res = useResource(key, () => v1.usagePeople({ period, q: term, role, sort, active, offset }));
  const noun = periodOf(period).noun;
  const total = res.data?.total ?? 0;
  return (
    <>
      <div className="toolbar">
        <SearchBox value={q} onChange={setQ} placeholder="Search by name or email" />
        <div className="toolbar-right">
          <Checkbox checked={active} onChange={setActive}>
            Only people who spent {noun}
          </Checkbox>
          <SelectField
            aria-label="Role"
            value={role}
            onChange={(e) => setRole(e.target.value)}
            options={[
              { value: "", label: "All roles" },
              { value: "learner", label: "Learners" },
              { value: "educator", label: "Educators" },
              { value: "admin", label: "Admins" },
            ]}
          />
          <SelectField
            aria-label="Sort"
            value={sort}
            onChange={(e) => setSort(e.target.value)}
            options={[
              { value: "credits", label: `Most credits ${noun}` },
              { value: "cost", label: `Most cost ${noun}` },
              { value: "used", label: "Most of this month's credits" },
              { value: "recent", label: "Spent most recently" },
              { value: "name", label: "Name" },
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
                <th>Credits this month</th>
                <th>Spent {noun}</th>
                <th>Making</th>
                <th>Learning</th>
                <th>Activity</th>
                <th>Last spend</th>
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
                          {u.name} {u.status === "suspended" && <Badge tone="danger">Suspended</Badge>}
                        </b>
                        <small>
                          {u.email} · {u.role}
                        </small>
                      </span>
                    </span>
                  </td>
                  <td>
                    <CreditMeter c={u.credits} />
                  </td>
                  <td>
                    <span className="num-pair">
                      <b>{usd(u.spent.cost_usd)}</b>
                      <small>{credits(u.spent.credits)} credits</small>
                    </span>
                  </td>
                  <td>{usd(u.spent.create_usd)}</td>
                  <td>{usd(u.spent.learn_usd)}</td>
                  <td>
                    <span className="num-pair">
                      <span>{plural(u.lessons, "lesson")}</span>
                      <small>{plural(u.doubts, "doubt")}</small>
                    </span>
                  </td>
                  <td>{u.spent.last_at ? ago(u.spent.last_at) : <span className="muted">–</span>}</td>
                  <td className="table-actions">
                    <Button
                      size="sm"
                      variant="ghost"
                      icon={Settings2}
                      aria-label={`Details for ${u.name}`}
                      title="Details and credits"
                      onClick={() => onOpen(u.id)}
                    />
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
        {res.error && <div className="table-empty">{res.error.message}</div>}
        {res.data && res.data.items.length === 0 && (
          <div className="table-empty">{active ? `Nobody spent anything ${noun}.` : "No one matches."}</div>
        )}
      </Card>
      <Pager offset={offset} size={25} total={total} onChange={setOffset} />
    </>
  );
}

// ---------------------------------------------------------------------- lessons

function LessonsTable({ period }: { period: UsagePeriod }) {
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const term = useDebounced(q, 250);
  // biome-ignore lint/correctness/useExhaustiveDependencies: reset when these change
  useEffect(() => setOffset(0), [period, term]);
  const res = useResource(`admin:usage:lessons:${period}:${term}:${offset}`, () =>
    v1.usageLessons({ period, q: term, offset }),
  );
  const noun = periodOf(period).noun;
  const total = res.data?.total ?? 0;
  return (
    <>
      <div className="toolbar">
        <SearchBox value={q} onChange={setQ} placeholder="Search lessons" />
        <p className="muted small toolbar-note">
          Making: the lesson's own AI and voice, charged to its owner. Doubts: answers to anyone who asked on it.
        </p>
      </div>
      <Card padded={false}>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Lesson</th>
                <th>Owner</th>
                <th>Making</th>
                <th>Doubts</th>
                <th>Total {noun}</th>
                <th>Credits</th>
                <th>Last spend</th>
              </tr>
            </thead>
            <tbody>
              {(res.data?.items ?? []).map((l) => (
                <tr key={l.id}>
                  <td>
                    {l.removed ? (
                      <span className="table-title">
                        <b>{l.title}</b>
                        <small>Removed · {l.id}</small>
                      </span>
                    ) : (
                      <Link href={`/lessons/${l.id}`} className="table-title">
                        <b>{l.title}</b>
                        <small>
                          {l.subject ? subject(l.subject).label : "–"} · {l.id}
                        </small>
                      </Link>
                    )}
                  </td>
                  <td>{l.owner ?? <span className="muted">No account</span>}</td>
                  <td>{usd(l.create_usd)}</td>
                  <td>
                    <span className="num-pair">
                      <b>{usd(l.learn_usd)}</b>
                      <small>{plural(l.answers, "answer")}</small>
                    </span>
                  </td>
                  <td>
                    <b>{usd(l.cost_usd)}</b>
                  </td>
                  <td>{credits(l.credits)}</td>
                  <td>{ago(l.last_at)}</td>
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
        {res.data && res.data.items.length === 0 && <div className="table-empty">No lesson cost anything {noun}.</div>}
      </Card>
      <Pager offset={offset} size={25} total={total} onChange={setOffset} />
    </>
  );
}

// ---------------------------------------------------------------------- one person

function PersonPanel({ id, onClose, onChanged }: { id: string; onClose: () => void; onChanged: () => void }) {
  const { can } = useAuth();
  const res = useResource(`admin:usage:person:${id}`, () => v1.usagePerson(id), 10_000);
  const d = res.data;
  const manage = can("credits:manage");
  const saved = () => {
    void res.reload();
    onChanged();
  };
  return (
    <Modal
      open
      onClose={onClose}
      size="lg"
      title={d ? d.user.name : "Credits and cost"}
      description={
        d ? `${d.user.email} · ${d.user.role}${d.user.status === "suspended" ? " · suspended" : ""}` : undefined
      }
    >
      {!d ? (
        res.error ? (
          <Alert tone="danger">{res.error.message}</Alert>
        ) : (
          <Spinner />
        )
      ) : (
        <div className="person-usage">
          <section className="person-credits">
            <div>
              <h3>This month's credits</h3>
              <CreditMeter c={d.credits} />
              <p className="muted small">
                {d.credits.remaining != null && d.credits.mode !== "blocked"
                  ? `${credits(Math.max(0, d.credits.remaining))} left · `
                  : ""}
                renew on {date(d.credits.resets_at)}
              </p>
            </div>
            {manage && <AllowanceForm id={id} current={d.credits} onSaved={saved} />}
          </section>

          <div className="person-periods">
            {PERIODS.map((p) => (
              <div key={p.value} className="person-period">
                <span>{p.label}</span>
                <b>{usd(d.periods[p.value].cost_usd)}</b>
                <small>{credits(d.periods[p.value].credits)} credits</small>
                <Split totals={d.periods[p.value]} />
              </div>
            ))}
          </div>

          <div>
            <h3>Last 30 days</h3>
            <DailySpend days={d.daily} />
            <SplitLegend />
          </div>

          {manage && <AdjustForm id={id} onSaved={saved} />}

          <div>
            <h3>Lessons by cost (all time)</h3>
            {d.lessons.length === 0 ? (
              <p className="muted small">No lesson spend yet.</p>
            ) : (
              <div className="table-wrap">
                <table className="table compact">
                  <thead>
                    <tr>
                      <th>Lesson</th>
                      <th>As</th>
                      <th>Cost</th>
                      <th>Credits</th>
                      <th>Last</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.lessons.map((l) => (
                      <tr key={l.id}>
                        <td>
                          {l.removed ? (
                            <span className="muted">{l.title} (removed)</span>
                          ) : (
                            <Link href={`/lessons/${l.id}`}>{l.title}</Link>
                          )}
                        </td>
                        <td>{l.made ? "Made it" : "Asked doubts"}</td>
                        <td>{usd(l.cost_usd)}</td>
                        <td>{credits(l.credits)}</td>
                        <td>{ago(l.last_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div>
            <h3>Latest charges</h3>
            {d.recent.length === 0 ? (
              <p className="muted small">Nothing yet.</p>
            ) : (
              <div className="table-wrap">
                <table className="table compact">
                  <thead>
                    <tr>
                      <th>When</th>
                      <th>What</th>
                      <th>Lesson</th>
                      <th>Cost</th>
                      <th>Credits</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.recent.map((e) => (
                      <tr key={e.id}>
                        <td title={date(e.at, true)}>{ago(e.at)}</td>
                        <td>
                          {KIND_LABEL[e.kind] ?? e.kind}
                          {e.estimated && (
                            <small className="muted" title="Estimated from characters spoken or recording length">
                              {" "}
                              · est.
                              {e.quantity > 0 &&
                                (e.kind === "transcription"
                                  ? ` ${e.quantity} s`
                                  : ` ${e.quantity.toLocaleString()} characters`)}
                            </small>
                          )}
                          {e.kind === "adjustment" && (
                            <small className="muted">
                              {" "}
                              · {e.note}
                              {e.by ? ` (${e.by})` : ""}
                            </small>
                          )}
                        </td>
                        <td>
                          {e.lesson_id ? (
                            <Link href={`/lessons/${e.lesson_id}`}>{e.lesson_title ?? e.lesson_id}</Link>
                          ) : (
                            <span className="muted">–</span>
                          )}
                        </td>
                        <td>{e.kind === "adjustment" ? <span className="muted">–</span> : usd(e.cost_usd)}</td>
                        <td>{credits(e.credits)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}
    </Modal>
  );
}

function AllowanceForm({
  id,
  current,
  onSaved,
}: {
  id: string;
  current: Credits & { role_limit: number | null };
  onSaved: () => void;
}) {
  const toast = useToast();
  const [mode, setMode] = useState<Credits["mode"]>(current.mode);
  const [limit, setLimit] = useState(
    String(current.mode === "custom" && current.limit != null ? current.limit : (current.role_limit ?? 1000)),
  );
  const [busy, setBusy] = useState(false);
  const n = Number(limit);
  const valid = mode !== "custom" || (Number.isInteger(n) && n >= 1 && n <= 10_000_000);
  const dirty = mode !== current.mode || (mode === "custom" && n !== current.limit);
  const save = async () => {
    setBusy(true);
    try {
      await v1.setCredits(id, mode, mode === "custom" ? n : undefined);
      toast.success("Monthly credits saved; they were told.");
      onSaved();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="allowance-form">
      <h3>Credits each month</h3>
      <Segmented
        label="Credits each month"
        value={mode}
        onChange={setMode}
        options={[
          {
            value: "role",
            label: `The role's (${current.role_limit == null ? "no limit" : credits(current.role_limit)})`,
          },
          { value: "custom", label: "Their own" },
          { value: "unlimited", label: "No limit" },
          { value: "blocked", label: "None" },
        ]}
      />
      {mode === "custom" && (
        <TextField
          label="Credits per month"
          type="number"
          inputMode="numeric"
          min={1}
          max={10_000_000}
          step={1}
          value={limit}
          onChange={(e) => setLimit(e.target.value)}
          error={valid ? null : "A whole number from 1 to 10,000,000."}
        />
      )}
      {mode === "blocked" && (
        <p className="muted small">
          <TriangleAlert size={13} /> They can still watch lessons, but not create lessons or ask doubts.
        </p>
      )}
      <Button
        variant="primary"
        size="sm"
        icon={Gauge}
        disabled={!dirty || !valid}
        loading={busy}
        onClick={() => void save()}
      >
        Save
      </Button>
    </div>
  );
}

function AdjustForm({ id, onSaved }: { id: string; onSaved: () => void }) {
  const toast = useToast();
  const [direction, setDirection] = useState<"give" | "charge">("give");
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const n = Number(amount);
  const amountOk = amount.trim() !== "" && Number.isFinite(n) && n >= 0.001 && n <= 1_000_000;
  const valid = amountOk && note.trim().length >= 3;
  const apply = async () => {
    setBusy(true);
    try {
      await v1.adjustCredits(id, direction === "give" ? n : -n, note.trim());
      toast.success(direction === "give" ? `${credits(n)} credits given back.` : `${credits(n)} credits charged.`);
      setAmount("");
      setNote("");
      onSaved();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="adjust-form">
      <h3>Give back or charge credits</h3>
      <p className="muted small">
        Changes only what they have left this month; no money moves. They are told, with your reason.
      </p>
      <div className="adjust-row">
        <Segmented
          label="Give back or charge"
          value={direction}
          onChange={setDirection}
          options={[
            { value: "give", label: "Give back" },
            { value: "charge", label: "Charge" },
          ]}
        />
        <TextField
          label="Credits"
          type="number"
          inputMode="decimal"
          min={0.001}
          step="any"
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          error={amount && !amountOk ? "A number above 0." : null}
        />
        <TextField
          label="Reason"
          value={note}
          maxLength={200}
          placeholder="A lesson that stopped half way"
          onChange={(e) => setNote(e.target.value)}
        />
        <Button variant="secondary" icon={Coins} disabled={!valid} loading={busy} onClick={() => void apply()}>
          {direction === "give" ? "Give back" : "Charge"}
        </Button>
      </div>
    </section>
  );
}
