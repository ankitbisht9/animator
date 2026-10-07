# The platform: accounts, roles, the gateway, the web app

The lesson pipeline (plan, scenes, voice, doubts) is wrapped in a learning platform. People sign up and
sign in; lessons belong to someone and are shared on purpose; learners keep progress, notes, a library and
streaks; support and administration have their own pages. The pipeline's code is unchanged: the platform
calls it, reads its records and guards its routes. Decisions and measurements: ADR-051.

## First run

1. `make dev` as before. The API creates `var/platform/platform.db` (SQLite) and a signing key in
   `var/platform/secret.key` on its first start, and adds every lesson already in `var/generations/` to the
   catalog.
2. Open http://localhost:5173 and **Create a free account**. In development the first account on an empty
   platform is an **administrator**, and the lessons made before accounts become its lessons.
3. Everyone who signs up after that is a learner. Make someone an educator or an admin in
   **Administration → People**.

Other ways to make an admin (production):

```
uv run python -m animator.platform create-admin --email you@school.org --name "Your Name"
uv run python -m animator.platform set-role someone@school.org educator
```

or list emails in `PLATFORM_ADMIN_EMAILS` (they are admins as soon as they sign up).

Without SMTP settings, mails (confirm your email, reset your password) are written to
`var/platform/outbox/` as JSON: open the newest file to follow the link.

## What people can do

| | Learner | Educator | Admin |
|---|---|---|---|
| Watch, save, take notes, rate, ask doubts | yes | yes | yes |
| Create lessons (per 24 h; longest lesson) | 5; 10 min | 25; 20 min | no limit; 20 min |
| Doubt answers per 24 h | 40 | 120 | no limit |
| Share a lesson with a link | yes | yes | yes |
| Publish to Explore | no | yes | yes |
| People, every lesson, featuring, support queue, audit log, system | no | no | yes |
| The pipeline's raw API, call logs, debug pages, DSL playground | no | no | yes |

The numbers are settings (`PLATFORM_LESSONS_PER_DAY_LEARNER`, ...). Permissions live in one table
(`animator/platform/rbac.py`); code asks for a permission, never for a role.

## Pages

- **Public:** landing page (with a live lesson drawn by the engine), Explore, a sample lesson, shared lessons
  (`/lessons/<id>` with a link), help center, terms, privacy, sign in, sign up, password reset, email
  confirmation.
- **Learning:** dashboard (continue where you left off, weekly goal, streak, recommendations, a getting-started
  checklist), Create lesson, My lessons, the lesson page, Library (saved lessons, notes, doubts), Progress
  (minutes, streaks, a 12-week calendar, achievements), Profile, Notifications, Settings (account, learning,
  playback, appearance, notifications, security with signed-in devices, privacy with data download and account
  deletion), Support (tickets with replies).
- **Administration:** overview (people, lessons, hours learned, AI spend, charts), People (roles, suspend, sign
  out everywhere), All lessons (feature, unpublish, remove, restore), Support queue (reply, status, priority),
  Audit log, System (engine workers, services, busiest routes with p50/p95), DSL playground, and each lesson's
  pipeline view and debug page.
- Keyboard: `Space`, `←`/`→`, `C`, `M`, `F` in the player as before; `D` raises a hand; `N` writes a note at the
  current moment; `Ctrl K` or `/` searches lessons.

## How a request is handled

```
browser ──► CORS ──► Gateway ──► FastAPI routes
                     │            ├─ /api/v1/...        the platform (accounts, lessons, learning, support, admin)
                     │            ├─ /api/generations   the pipeline (unchanged)
                     │            ├─ /api/doubts        doubts (unchanged)
                     │            └─ /api/tts           narration audio (unchanged)
                     └─ request id · size limits · Origin + CSRF checks · who is asking · rate limit ·
                        access rules for the pipeline's routes · security headers · metrics · access log
```

- **The gateway** (`animator/platform/gateway.py`) is plain ASGI, so live event streams pass through
  unbuffered. The pipeline's routes kept their code; their rules are a table in the gateway:
  - a lesson's stream is for its owner, for anyone when it is shared (link or public), and for admins;
  - cancel, resume and export are for the owner and admins;
  - the pipeline's own list, create and call-log routes are for admins;
  - doubts need a signed-in learner who may read the lesson, within the daily quota, and only the person who
    asked a doubt may continue or read it;
  - narration audio is public (named by its content);
  - **any API route not in the table is for admins only** (deny by default).
- **Sign-in.**
  - A short-lived access token (15 min, signed JWT) and a refresh token (30 days with "keep me signed in", 12 h
    without) live in httpOnly cookies. Page scripts never see a token.
  - The refresh token changes at every use. Using a replaced one again ends the session, as a stolen token
    would.
  - The page renews the access token in the background; a request that meets an expired one refreshes once
    and retries.
  - Scripts may use `Authorization: Bearer <access token>` instead.
- **Cross-site protection:** state-changing requests must come from our origin and, when signed by cookie,
  carry the CSRF token (double-submit cookie `anim_csrf` and header `X-CSRF-Token`). Cookies are SameSite.
- **Passwords:** Argon2id, at least 8 characters mixing letters with numbers or symbols, not common, not the
  name or email. Five failed sign-ins lock the account for 15 minutes; sign-in and recovery are rate limited
  per address and per email. Failed sign-ins and reset requests answer the same whether or not an account
  exists.
- **Records:** the audit log keeps sign-ins, failures, lockouts, password changes and resets, role and status
  changes, sharing changes, lesson removals and support triage.

## Data

Lessons stay where the pipeline writes them (`var/generations/<id>/`). The platform's database holds:

| Table | What |
|---|---|
| users, auth_sessions, one_time_tokens | accounts, signed-in devices (refresh token hashes), email and reset links (hashes) |
| lessons | the catalog: owner, sharing, subject, status, length, cost, views, rating; soft-deleted rows stay |
| progress, activity_days | where each learner is in each lesson; minutes per day (streaks, charts) |
| saved, notes, ratings, doubt_log | the library, notes pinned to moments, star ratings, doubts asked |
| notifications, tickets, ticket_messages | in-app notices; support conversations |
| audit_log | security and administration events |

- The schema is migrated by Alembic at start (`animator/platform/migrations/`).
- A new migration: `uv run python -m animator.platform revision -m "what changed"`.
- Every list is one query with its joins, indexed on what it filters and sorts by (owner and date,
  visibility and date, user and time).
- SQLite runs in WAL mode with foreign keys on.
- PostgreSQL works by setting `PLATFORM_DATABASE_URL=postgresql+asyncpg://...` (install `asyncpg`).
- Back up SQLite with `sqlite3 var/platform/platform.db ".backup backup.db"` while it runs.

## Settings

All optional; defaults suit development.

| Setting | Default | |
|---|---|---|
| `PLATFORM_ENABLED` | true | false: the API exactly as before accounts (development only) |
| `PLATFORM_ENVIRONMENT` | development | `production`: Secure cookies, strict origins, no first-account admin |
| `PLATFORM_DATABASE_URL` | SQLite in `var/platform/` | any SQLAlchemy async URL |
| `PLATFORM_SECRET_KEY` | generated into `var/platform/secret.key` | set it in production (48+ random chars) |
| `PLATFORM_ADMIN_EMAILS` | (empty) | comma-separated emails that are admins on sign-up |
| `PLATFORM_FIRST_USER_ADMIN` | on in development | the first account on an empty platform is an admin |
| `PLATFORM_ALLOW_SIGNUP` | true | false: invite-only (admins create accounts with the CLI) |
| `PLATFORM_REQUIRE_VERIFIED_EMAIL` | false | true: confirm the email before creating lessons |
| `PLATFORM_PUBLIC_BASE_URL` | http://localhost:5173 | used in mail links; also an allowed origin |
| `PLATFORM_SMTP_HOST`, `_PORT`, `_USER`, `_PASSWORD`, `_FROM`, `_STARTTLS` | (empty) | outgoing mail; empty: the outbox folder |
| `PLATFORM_LESSONS_PER_DAY_LEARNER` / `_EDUCATOR` / `_ADMIN` | 5 / 25 / 0 | 0: no limit |
| `PLATFORM_MAX_MINUTES_LEARNER` / `_EDUCATOR` / `_ADMIN` | 10 / 20 / 20 | longest lesson |
| `PLATFORM_DOUBTS_PER_DAY_LEARNER` / `_EDUCATOR` / `_ADMIN` | 40 / 120 / 0 | |
| `PLATFORM_RATE_LIMIT_PER_MIN` | 600 | requests per person (or address) per minute |
| `PLATFORM_ACCESS_TTL_MIN`, `PLATFORM_REFRESH_TTL_DAYS` | 15, 30 | token lifetimes |
| `PLATFORM_MAX_FAILED_LOGINS`, `PLATFORM_LOCKOUT_MINUTES` | 5, 15 | |
| `PLATFORM_TRUSTED_HOSTS` | * | e.g. `learn.example.org` |
| `PLATFORM_SERVE_WEB` | false | serve the built web app from the API (one origin) with a strict CSP |
| `PLATFORM_HSTS` | false | Strict-Transport-Security (behind https only) |
| `PLATFORM_DOCS` | true | `/docs` for admins; false hides it |

## Going to production

1. `PLATFORM_ENVIRONMENT=production`, a `PLATFORM_SECRET_KEY`, `PLATFORM_PUBLIC_BASE_URL=https://your.domain`,
   `CORS_ORIGINS` the same, `PLATFORM_TRUSTED_HOSTS=your.domain`, `PLATFORM_HSTS=true`.
2. SMTP settings, so people can confirm emails and reset passwords.
3. `pnpm build:web`, then `PLATFORM_SERVE_WEB=true`: the API serves the app and the API on one origin.
4. Run uvicorn behind https (a reverse proxy) with `--proxy-headers --forwarded-allow-ips <proxy>` so the
   gateway sees real client addresses.
5. Create the first admin with `create-admin`; set `PLATFORM_ALLOW_SIGNUP` as you like.
6. Back up `var/platform/platform.db` (or use PostgreSQL) and `var/generations/`.
7. One API process: rate limits and caches are in memory. Several processes would need a shared store (Redis)
   for the rate limiter.
8. Have the terms and privacy pages reviewed: they are templates.

## Where things are

- API: `apps/api/src/animator/platform/`
  - `gateway.py`: the gateway;
  - `core.py`: the platform object, who is asking, caches;
  - `api/`: the `/api/v1` routes (auth, account, lessons, learning, support, admin);
  - `accounts.py`: sessions and cookies;
  - `security.py`: passwords and tokens;
  - `rbac.py`: roles and permissions;
  - `catalog.py`: the lesson catalog and subjects;
  - `models.py`, `migrations/`: the database;
  - `mail.py`, `metrics.py`, `ratelimit.py`;
  - `web.py`: serving the built app;
  - `__main__.py`: the command line.
- Web: `apps/web/src/platform/`
  - `http.ts`: CSRF and refresh around every request;
  - `auth.tsx`: who is signed in;
  - `nav.tsx`: routing helpers;
  - `ui/`: the components;
  - `layout/`: the shell, public and sign-in frames;
  - `pages/`: every page;
  - `lesson/`: cards, sharing, notes, progress, the lesson-complete card;
  - `styles/`: tokens and styles.
- Routes: `apps/web/src/App.tsx`.
- Tests: `apps/api/tests/test_platform.py`.
