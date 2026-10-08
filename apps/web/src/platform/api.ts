/** The platform API (/api/v1): types and calls. */
import { HttpError, http, qs } from "./http";

export type Role = "learner" | "educator" | "admin";
export type Visibility = "private" | "unlisted" | "public";
export type ThemePref = "light" | "dark" | "system";

export interface Preferences {
  language: "English" | "Hinglish";
  audience: string;
  duration_min: number;
  layout: "classic" | "planned" | null;
  interests: string[];
  weekly_goal_min: number;
  playback_rate: number;
  captions: boolean;
  voice: boolean;
  autoplay: boolean;
  theme: ThemePref;
  reduced_motion: boolean;
  notify_lesson_ready: boolean;
  notify_support: boolean;
  notify_weekly_summary: boolean;
}

export interface User {
  id: string;
  email: string;
  name: string;
  role: Role;
  status: "active" | "suspended";
  bio: string;
  avatar: string;
  verified: boolean;
  onboarded: boolean;
  created_at: number;
  last_login_at: number | null;
  preferences: Preferences;
  permissions: string[];
  limits: { lessons_per_day: number; doubts_per_day: number; max_minutes: number; require_verified: boolean };
  usage: { lessons: number; doubts: number };
  credits: Credits;
}

/** Credits per calendar month (ADR-053): a role's allowance, a person's own, no limit, or none. */
export interface Credits {
  mode: "role" | "custom" | "unlimited" | "blocked";
  limit: number | null;
  used: number;
  remaining: number | null;
  unlimited: boolean;
  blocked: boolean;
  exhausted: boolean;
  period_start: number;
  resets_at: number;
}

export interface MyCredits extends Credits {
  by_area: { create: number; learn: number; adjustment: number };
  items: { area: "create" | "learn"; lesson_id: string | null; lesson_title: string; credits: number; at: number }[];
  adjustments: { credits: number; note: string; at: number }[];
}

export interface LessonProgress {
  position_s: number;
  percent: number;
  completed: boolean;
  last_watched_at: number;
}

export interface Lesson {
  id: string;
  title: string;
  subtitle: string;
  topic: string;
  audience: string;
  language: string;
  duration_min: number;
  length_s: number;
  scene_count: number;
  status: "queued" | "planning" | "generating" | "complete" | "failed" | "cancelled" | "paused";
  subject: string;
  subject_label: string;
  visibility: Visibility;
  featured: boolean;
  views: number;
  rating: number | null;
  rating_count: number;
  created_at: number;
  updated_at: number;
  completed_at: number | null;
  deleted: boolean;
  owner: { id: string; name: string } | null;
  mine: boolean;
  can_edit: boolean;
  cost_usd: number | null;
  progress: LessonProgress | null;
  saved: boolean;
  my_rating?: { stars: number; comment: string } | null;
  can_rate: boolean;
  in_explore: boolean;
  /** For the owner and the people who curate: why it is in Explore, and the owner's choice. */
  explore: LessonExplore | null;
}

export interface ExploreRule {
  auto: boolean;
  min_raters: number;
  min_average: number;
}

export type ExploreSource = "" | "owner" | "auto" | "admin";

export interface LessonExplore {
  source: ExploreSource;
  since: number | null;
  opt_out: boolean;
  /** Only on a single lesson (not in lists). */
  included?: boolean;
  excluded?: boolean;
  rule?: ExploreRule;
}

export interface PageOf<T> {
  items: T[];
  has_more?: boolean;
  total?: number;
}

export interface Stats {
  today: string;
  minutes_total: number;
  minutes_today: number;
  minutes_week: number;
  weekly_goal_min: number;
  streak: number;
  best_streak: number;
  lessons_completed: number;
  lessons_started: number;
  lessons_created: number;
  doubts_asked: number;
  notes: number;
  saved: number;
  activity: { day: string; minutes: number }[];
  achievements: { key: string; title: string; description: string; unlocked: boolean }[];
}

export interface Note {
  id: string;
  lesson_id: string;
  lesson_title?: string;
  t_s: number;
  text: string;
  created_at: number;
  updated_at?: number;
}

export interface DoubtItem {
  session_id: string;
  lesson_id: string;
  lesson_title?: string;
  turn: number;
  question: string;
  title: string;
  kind: string;
  created_at: number;
}

export interface Notice {
  id: string;
  kind: string;
  title: string;
  body: string;
  link: string;
  read: boolean;
  created_at: number;
}

export interface Ticket {
  id: string;
  ref: string;
  subject: string;
  category: string;
  status: "open" | "pending" | "resolved" | "closed";
  priority: "low" | "normal" | "high" | "urgent";
  lesson_id: string | null;
  email: string;
  name: string | null;
  created_at: number;
  updated_at: number;
  last_message: string | null;
  messages?: { id: string; staff: boolean; author: string; mine: boolean; body: string; created_at: number }[];
}

export interface DeviceSession {
  id: string;
  user_agent: string;
  ip: string;
  created_at: number;
  last_used_at: number;
  expires_at: number;
  current: boolean;
}

export interface PublicInfo {
  signup: boolean;
  /** Sign-ups are closed except for invited emails (the form stays for them). */
  invite_only: boolean;
  mail: boolean;
  doubts: boolean;
  voice: boolean;
  languages: string[];
  subjects: { key: string; label: string }[];
  password_min_length: number;
}

export interface AdminUser {
  id: string;
  email: string;
  name: string;
  role: Role;
  status: "active" | "suspended";
  verified: boolean;
  avatar: string;
  created_at: number;
  last_login_at: number | null;
  lessons: number;
  me: boolean;
}

export interface Overview {
  users: number;
  new_users_7d: number;
  active_users_7d: number;
  lessons: number;
  new_lessons_7d: number;
  ai_cost_usd: number;
  ai_cost_7d_usd: number;
  doubts: number;
  open_tickets: number;
  completions: number;
  hours_learned: number;
  lessons_by_status: Record<string, number>;
  users_by_role: Record<string, number>;
  lessons_by_subject: Record<string, number>;
  daily: { day: string; signups: number; lessons: number; minutes: number }[];
}

export interface AuditItem {
  id: string;
  action: string;
  actor: string | null;
  target: string;
  ip: string;
  detail: Record<string, unknown>;
  created_at: number;
}

export interface SystemInfo {
  engine_workers: boolean[];
  database: string;
  mail: string;
  environment: string;
  openai_key: boolean;
  tts: boolean;
  doubts: boolean;
  started_at: number;
  requests: {
    uptime_s: number;
    requests: number;
    errors: number;
    per_minute: number;
    routes: { route: string; count: number; errors: number; p50_ms: number | null; p95_ms: number | null }[];
  };
}

// ---------------------------------------------------------------------- credits and cost (admin)

export type UsageKind = "lesson_ai" | "lesson_voice" | "doubt_ai" | "doubt_voice" | "transcription" | "adjustment";
export type UsagePeriod = "today" | "week" | "month" | "total";
type Money = { cost_usd: number; credits: number };

export interface UsageTotals extends Money {
  entries: number;
  estimated_usd: number;
  by_kind: Partial<Record<UsageKind, Money & { count: number }>>;
  by_area: Record<"create" | "learn" | "adjustment", Money>;
}

export interface UsageDay extends Money {
  day: string;
  create_usd: number;
  learn_usd: number;
}

export interface UsageSummary {
  timezone: string;
  credit_usd: number;
  now: number;
  starts: Record<"today" | "week" | "month", number>;
  resets_at: number;
  periods: Record<UsagePeriod, UsageTotals>;
  daily: UsageDay[];
  people: { spending_this_month: number; over_limit: number; near_limit: number; blocked: number };
  top: ({ id: string; name: string; email: string } & Money)[];
  averages: { lesson_usd: number | null; doubt_usd: number | null };
  unattributed_usd: number;
  allowances: Record<Role, number | null>;
  prices: {
    voices: { provider: string; usd_per_million_chars: number; configured: boolean }[];
    transcribe_usd_per_min: number;
  };
}

export interface PersonCredits {
  mode: Credits["mode"];
  limit: number | null;
  role_limit: number | null;
  used: number;
  percent: number | null;
  exhausted: boolean;
}

export interface UsagePerson {
  id: string;
  name: string;
  email: string;
  role: Role;
  status: "active" | "suspended";
  avatar: string;
  credits: PersonCredits;
  spent: Money & { create_usd: number; learn_usd: number; last_at: number | null };
  lessons: number;
  doubts: number;
}

export interface UsageEntryItem extends Money {
  id: string;
  kind: UsageKind;
  lesson_id: string | null;
  lesson_title: string | null;
  estimated: boolean;
  quantity: number;
  note: string;
  by: string | null;
  at: number;
}

export interface UsagePersonDetail {
  user: { id: string; name: string; email: string; role: Role; status: string; avatar: string; created_at: number };
  credits: Credits & { role_limit: number | null };
  periods: Record<UsagePeriod, UsageTotals>;
  daily: UsageDay[];
  lessons: ({ id: string; title: string; removed: boolean; made: boolean; last_at: number } & Money)[];
  recent: UsageEntryItem[];
}

export interface UsageLesson extends Money {
  id: string;
  title: string;
  subject: string | null;
  status: string | null;
  removed: boolean;
  owner: string | null;
  create_usd: number;
  learn_usd: number;
  answers: number;
  last_at: number;
}

// ---------------------------------------------------------------------- ratings and Explore (admin)

export type ReviewStatus = "" | "flagged" | "working" | "resolved";
export type RatingsView = "all" | "rated" | "top" | "poor" | "rule" | "near" | "listed" | "excluded" | "review";

export interface RatedLesson {
  id: string;
  title: string;
  subject: string;
  subject_label: string;
  status: Lesson["status"];
  visibility: Visibility;
  featured: boolean;
  views: number;
  created_at: number;
  owner: { id: string; name: string | null; status: string | null } | null;
  rating: {
    average: number | null;
    count: number;
    score: number;
    stars: number[];
    last_rated_at: number | null;
    meets_rule: boolean;
  };
  explore: {
    listed: boolean;
    source: ExploreSource;
    since: number | null;
    pin: "" | "include" | "exclude";
    pin_note: string;
    pinned_at: number | null;
    opt_out: boolean;
    reason: string;
    reason_text: string;
  };
  review: { status: ReviewStatus; note: string; at: number | null };
}

export interface RatedLessonDetail extends RatedLesson {
  rule: ExploreRule;
  ratings_total: number;
  ratings: { name: string; stars: number; comment: string; at: number; counts: boolean; why_not: string | null }[];
  history: { action: string; by: string | null; detail: Record<string, unknown>; at: number }[];
}

export interface RatingsSummary {
  rule: ExploreRule;
  rated_lessons: number;
  ratings: number;
  average: number | null;
  platform_mean: number;
  stars: number[];
  in_explore: { total: number; owner: number; auto: number; admin: number };
  meets_rule: number;
  near_rule: number;
  poor: number;
  excluded: number;
  review: Record<"flagged" | "working" | "resolved", number>;
  recent_comments: {
    lesson_id: string;
    lesson_title: string;
    name: string;
    stars: number;
    comment: string;
    at: number;
  }[];
}

export interface CreateLesson {
  topic: string;
  audience: string;
  duration_min: number;
  style?: string | null;
  language: string;
  paced: boolean;
  layout?: "classic" | "planned" | null;
  visibility: Visibility;
  subject?: string | null;
}

type U = { user: User };

export const v1 = {
  info: () => http.get<PublicInfo>("/api/v1/public/info"),
  // auth
  me: () => http.get<U>("/api/v1/auth/me"),
  login: (email: string, password: string, remember: boolean) =>
    http.post<U>("/api/v1/auth/login", { email, password, remember }),
  register: (name: string, email: string, password: string) =>
    http.post<U>("/api/v1/auth/register", { name, email, password, remember: true }),
  logout: () => http.post<void>("/api/v1/auth/logout"),
  forgot: (email: string) => http.post<{ ok: boolean; message: string }>("/api/v1/auth/password/forgot", { email }),
  reset: (token: string, password: string) =>
    http.post<{ ok: boolean }>("/api/v1/auth/password/reset", { token, password }),
  verify: (token: string) => http.post<{ ok: boolean }>("/api/v1/auth/verify-email", { token }),
  resendVerification: () => http.post<{ ok: boolean }>("/api/v1/auth/verify-email/resend"),
  // account
  updateMe: (
    body: Partial<Pick<User, "name" | "bio" | "avatar" | "onboarded">> & { preferences?: Partial<Preferences> },
  ) => http.patch<U>("/api/v1/me", body),
  changePassword: (current: string, next: string) =>
    http.post<{ ok: boolean; signed_out: number }>("/api/v1/me/password", { current, new: next }),
  sessions: () => http.get<DeviceSession[]>("/api/v1/me/sessions"),
  endSession: (id: string) => http.del<void>(`/api/v1/me/sessions/${id}`),
  endOtherSessions: () => http.post<{ signed_out: number }>("/api/v1/me/sessions/revoke-others"),
  myCredits: () => http.get<MyCredits>("/api/v1/me/credits"),
  deleteAccount: (password: string) => http.del<void>("/api/v1/me", { password, confirm: "DELETE" }),
  // lessons
  createLesson: (body: CreateLesson) => http.post<Lesson>("/api/v1/lessons", body),
  myLessons: (p: { q?: string; status?: string; sort?: string; offset?: number; limit?: number }) =>
    http.get<PageOf<Lesson>>(`/api/v1/lessons${qs(p)}`),
  lesson: (id: string) => http.get<Lesson>(`/api/v1/lessons/${id}`),
  patchLesson: (
    id: string,
    body: { visibility?: Visibility; subject?: string; featured?: boolean; explore_opt_out?: boolean },
  ) => http.patch<Lesson>(`/api/v1/lessons/${id}`, body),
  deleteLesson: (id: string) => http.del<void>(`/api/v1/lessons/${id}`),
  explore: (p: { q?: string; subject?: string; sort?: string; offset?: number; limit?: number }) =>
    http.get<PageOf<Lesson>>(`/api/v1/explore${qs(p)}`),
  featured: () => http.get<Lesson[]>("/api/v1/explore/featured"),
  subjects: () => http.get<{ key: string; label: string; count: number }[]>("/api/v1/explore/subjects"),
  progress: (id: string, body: { position_s: number; duration_s: number; delta_s: number; day: string }) =>
    http.put<{ position_s: number; percent: number; completed: boolean; newly_completed: boolean }>(
      `/api/v1/lessons/${id}/progress`,
      body,
    ),
  save: (id: string) => http.put<void>(`/api/v1/lessons/${id}/save`),
  unsave: (id: string) => http.del<void>(`/api/v1/lessons/${id}/save`),
  notes: (id: string) => http.get<Note[]>(`/api/v1/lessons/${id}/notes`),
  addNote: (id: string, t_s: number, text: string) => http.post<Note>(`/api/v1/lessons/${id}/notes`, { t_s, text }),
  editNote: (noteId: string, text: string) => http.patch<Note>(`/api/v1/notes/${noteId}`, { text }),
  deleteNote: (noteId: string) => http.del<void>(`/api/v1/notes/${noteId}`),
  /** Without a comment, the comment already given stays. */
  rate: (id: string, stars: number, comment?: string) =>
    http.put<{ rating: number | null; rating_count: number }>(`/api/v1/lessons/${id}/rating`, { stars, comment }),
  lessonDoubts: (id: string) => http.get<DoubtItem[]>(`/api/v1/lessons/${id}/doubts`),
  // learning
  stats: (today: string) => http.get<Stats>(`/api/v1/me/stats${qs({ today })}`),
  learning: () => http.get<{ continue: Lesson[]; completed: Lesson[]; created: Lesson[] }>("/api/v1/me/learning"),
  library: (offset = 0) => http.get<PageOf<Lesson>>(`/api/v1/me/library${qs({ offset })}`),
  allNotes: (q?: string) => http.get<Note[]>(`/api/v1/me/notes${qs({ q })}`),
  myDoubts: () => http.get<DoubtItem[]>("/api/v1/me/doubts"),
  notifications: () => http.get<{ unread: number; items: Notice[] }>("/api/v1/notifications"),
  readNotice: (id: string) => http.post<void>(`/api/v1/notifications/${id}/read`),
  readAll: () => http.post<void>("/api/v1/notifications/read-all"),
  // support
  openTicket: (body: { subject: string; category: string; message: string; lesson_id?: string | null }) =>
    http.post<Ticket>("/api/v1/support/tickets", body),
  tickets: () => http.get<Ticket[]>("/api/v1/support/tickets"),
  ticket: (id: string) => http.get<Ticket>(`/api/v1/support/tickets/${id}`),
  reply: (id: string, body: string) => http.post<Ticket>(`/api/v1/support/tickets/${id}/messages`, { body }),
  closeTicket: (id: string) => http.post<Ticket>(`/api/v1/support/tickets/${id}/close`),
  // admin
  overview: () => http.get<Overview>("/api/v1/admin/overview"),
  users: (p: { q?: string; role?: string; status?: string; offset?: number }) =>
    http.get<PageOf<AdminUser>>(`/api/v1/admin/users${qs(p)}`),
  patchUser: (id: string, body: { role?: Role; status?: "active" | "suspended" }) =>
    http.patch<{ id: string; role: Role; status: string }>(`/api/v1/admin/users/${id}`, body),
  endUserSessions: (id: string) => http.post<{ signed_out: number }>(`/api/v1/admin/users/${id}/revoke-sessions`),
  adminLessons: (p: { q?: string; status?: string; visibility?: string; deleted?: boolean; offset?: number }) =>
    http.get<PageOf<Lesson>>(`/api/v1/admin/lessons${qs(p)}`),
  moderate: (id: string, body: { visibility?: Visibility; featured?: boolean; deleted?: boolean; subject?: string }) =>
    http.patch<Lesson>(`/api/v1/admin/lessons/${id}`, body),
  queue: (p: { status?: string; q?: string }) => http.get<PageOf<Ticket>>(`/api/v1/admin/tickets${qs(p)}`),
  triage: (id: string, body: { status?: string; priority?: string }) =>
    http.patch<Ticket>(`/api/v1/admin/tickets/${id}`, body),
  audit: (p: { action?: string; offset?: number }) => http.get<PageOf<AuditItem>>(`/api/v1/admin/audit${qs(p)}`),
  system: () => http.get<SystemInfo>("/api/v1/admin/system"),
  // credits and cost
  usageSummary: (days = 30) => http.get<UsageSummary>(`/api/v1/admin/usage/summary${qs({ days })}`),
  usagePeople: (p: {
    period: UsagePeriod;
    q?: string;
    role?: string;
    sort?: string;
    active?: boolean;
    offset?: number;
  }) => http.get<PageOf<UsagePerson>>(`/api/v1/admin/usage/people${qs(p)}`),
  usagePerson: (id: string) => http.get<UsagePersonDetail>(`/api/v1/admin/usage/people/${id}`),
  setCredits: (id: string, mode: Credits["mode"], limit?: number) =>
    http.put<Credits & { role_limit: number | null }>(`/api/v1/admin/usage/people/${id}/credits`, {
      mode,
      limit: mode === "custom" ? limit : undefined,
    }),
  /** Positive: credits given back; negative: charged. */
  adjustCredits: (id: string, credits: number, note: string) =>
    http.post<Credits & { role_limit: number | null }>(`/api/v1/admin/usage/people/${id}/adjustments`, {
      credits,
      note,
    }),
  usageLessons: (p: { period: UsagePeriod; q?: string; offset?: number }) =>
    http.get<PageOf<UsageLesson>>(`/api/v1/admin/usage/lessons${qs(p)}`),
  // ratings and Explore
  ratingsSummary: () => http.get<RatingsSummary>("/api/v1/admin/ratings/summary"),
  ratedLessons: (p: { view: RatingsView; subject?: string; q?: string; sort?: string; offset?: number }) =>
    http.get<PageOf<RatedLesson> & { rule: ExploreRule; sort: string }>(`/api/v1/admin/ratings/lessons${qs(p)}`),
  ratedLesson: (id: string, p: { stars?: number; offset?: number } = {}) =>
    http.get<RatedLessonDetail>(`/api/v1/admin/ratings/lessons/${id}${qs(p)}`),
  curate: (
    id: string,
    body: { action: "include" | "exclude" | "automatic"; note?: string; share?: boolean; subject?: string },
  ) => http.put<RatedLesson>(`/api/v1/admin/explore/${id}`, body),
  review: (id: string, status: ReviewStatus, note: string) =>
    http.put<{ status: ReviewStatus; note: string; at: number }>(`/api/v1/admin/ratings/lessons/${id}/review`, {
      status,
      note,
    }),
};

/** The usage ledger of a period as a CSV file, downloaded through the session-aware fetch (it renews an
 * expired sign-in like every other request). */
export async function downloadUsageCsv(period: UsagePeriod): Promise<void> {
  const res = await fetch(`/api/v1/admin/usage/export.csv${qs({ period })}`);
  if (!res.ok) {
    let detail = `Export failed (${res.status})`;
    try {
      const data = (await res.json()) as { detail?: unknown };
      if (typeof data.detail === "string") detail = data.detail;
    } catch {
      /* not JSON */
    }
    throw new HttpError(detail, res.status);
  }
  const blob = await res.blob();
  const name = /filename="([^"]+)"/.exec(res.headers.get("content-disposition") ?? "")?.[1] ?? `usage-${period}.csv`;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.append(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 2000);
}
