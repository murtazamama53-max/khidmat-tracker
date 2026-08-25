# Murtaza — Khidmat Earnings Tracker

A private, deterministic earnings and time-tracking system for irregular
Khidmat sessions, private tuition, and other income. Built from the
project blueprint: **record once → interpret → calculate exactly → save
history → analyze**.

> **Status: Phase 1 through Phase 7 complete.** Manual sessions, the
> deterministic calculation engine, historical rates, private tuition,
> a premium dashboard with charts and reports, Google Calendar sync,
> full security hardening — CSRF protection, an optional PIN/app
> lock, encrypted backups, and a systematically-verified owner/guest
> data boundary — a local, deterministic AI assistant for
> natural-language questions about your own data, and production/
> deployment readiness (PostgreSQL migration support via Flask-Migrate,
> Vercel serverless entry point, hardened session/proxy config) are all
> built and tested (243 tests).

## Why this exists

Money math is never done by AI. The parser (`app/services/parser.py`)
only turns shorthand like `Sbhs(7) & sghs(5-6:20)` into structured
drafts; all duration, rate resolution, and currency arithmetic happens in
`app/services/calculation_engine.py` and `app/services/rate_service.py`,
using `Decimal` throughout. Every session snapshots the rate that was
actually applied, so changing today's rate never rewrites yesterday's
earnings.

## Requirements

- Python 3.10+
- pip

## Setup

```bash
# 1. Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment variables
cp .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"   # paste into SECRET_KEY in .env

# 4. Run the app
python run.py
```

Open `http://127.0.0.1:5000`. The first visit redirects you to
`/setup` to create your one-and-only owner account (a second owner
account cannot be created afterwards). The database file is created
automatically at `instance/khidmat.db`.

### Try it with demo data instead

```bash
python seed_demo_data.py
```

This refuses to run if an owner account already exists (so it can never
overwrite your real data). It creates `demo@example.com` /
`demo-password-123` with two sources (SBHS/SGHS), a rate change on
1 July 2026, and ten days of sample sessions.

### Guest mode

Visit `/guest` (linked from the login page) to try the shorthand
calculator without logging in. Guest calculations are never written to
the database — they exist only in that request/response cycle — so
there is nothing to clean up afterwards.

## Using it

Type a session into the **+ Add Session** box on the Dashboard or
Sessions page, e.g.:

```
Sbhs(7) & sghs(5-6:20)
```

The app parses it, resolves the historical rate for the selected date,
and shows you exact chip-by-chip amounts before anything is saved.
Nothing is written until you click **Confirm & Save**. If a source
doesn't exist yet or has no rate covering that date, the app tells you
exactly what's missing instead of guessing.

Add sources and rates first, under **Rates** in the sidebar — a source
needs at least one rate period before sessions can be logged against it.

### Private tuition

Under **Tuition**, add a student, then set a fee period (amount +
effective dates). Generate an **Invoice** for a billing period (e.g. a
month) — the fee is resolved from whichever fee period covers the
invoice's start date and snapshotted onto the invoice, so a later fee
change never rewrites an invoice you already generated. Record payments
against an invoice (partial payments are supported; overpayment is
rejected); status (pending/partial/paid/overdue) is derived automatically
from payments received and the due date, not hand-set. Bonuses and
deductions can attach to a session, an invoice, or stand alone as "other
income" — each requires a reason and never mutates the original
calculated amount it relates to.

## Running tests

```bash
pytest -v
```

202 tests cover the calculation engine, rate resolution, tuition/fee
resolution, analytics/date-range logic, the parser, calendar event
reconciliation, token/backup encryption, PIN lock/auto-lock, CSRF
enforcement, and full route-level flows: the exact blueprint worked
examples, historical rate/fee isolation, guest isolation, tuition
payment states, the full calendar sync lifecycle against realistic
mocked Google API responses, and a systematic security sweep that walks
every one of the app's ~39 routes as an unauthenticated visitor, a
guest, and the owner to confirm each is authorized correctly — not a
manual spot check.

## Project structure

```
app/
  config.py              # env-driven config, no hard-coded secrets
  extensions.py          # SQLAlchemy instance
  models/                # users (incl. pin_hash), income_sources, rate_history,
                          # sessions, calendar_accounts, calendar_mappings,
                          # calendar_drafts, calendar_links, sync_logs,
                          # students, fee_periods, invoices, payments,
                          # adjustments, audit_log, goals
  services/
    calculation_engine.py  # THE financial core — deterministic, tested
    rate_service.py        # date-aware historical rate resolution
    tuition_service.py     # date-aware fee resolution + invoice status
    parser.py               # shorthand -> structured draft (never guesses)
    analytics_service.py    # pure metric functions (monthly/yearly, etc.)
    earnings_query.py       # DB queries shaped for analytics_service
    date_range.py            # This Month/Last Month/This Year/Custom
    goals_service.py         # shared goal-progress computation
    google_calendar_client.py # the ONLY module that talks to Google's servers
    calendar_sync.py          # pure event reconciliation logic (no network, no money math)
    token_crypto.py           # encrypts OAuth tokens and backup files at rest
    backup_service.py         # create/list/restore encrypted DB backups
    assistant_service.py      # local, deterministic NL Q&A over your own data
  routes/
    auth.py, dashboard.py, sessions.py, rates.py, students.py,
    reports.py, goals.py, calendar.py, settings.py, guest.py, assistant.py
  templates/, static/
migrations/           # Flask-Migrate/Alembic schema migrations (flask db upgrade)
api/
  index.py             # Vercel serverless entry point (re-exports the same Flask app)
vercel.json            # Vercel build/routing config
tests/
seed_demo_data.py
run.py
```

## Environment variables

See `.env.example`. `SECRET_KEY` is the only one required to start the
app at all. Everything else has a sane local default:

| Variable | Local default | Production |
|---|---|---|
| `SECRET_KEY` | dev placeholder (app still runs) | **required** — app refuses to start if left as the placeholder while `FLASK_ENV=production` |
| `FLASK_ENV` | `development` | set to `production` |
| `DATABASE_URL` | local SQLite file | PostgreSQL connection string (see **Deployment** below) |
| `SESSION_COOKIE_SECURE` | `false` | `true` (requires HTTPS, which Vercel provides) |
| `GOOGLE_CLIENT_ID` / `SECRET` / `REDIRECT_URI` | blank (Calendar sync disabled) | set once you enable Calendar sync — see below |

## Deployment (GitHub + Vercel)

The app is structured so the same Flask code runs three ways with zero
changes: `python run.py` locally, `gunicorn run:app` on a traditional
host, or as a Vercel serverless function via `api/index.py` +
`vercel.json`. Production **must** use PostgreSQL, not SQLite — Vercel's
deployment filesystem is read-only and ephemeral, so a local SQLite file
cannot persist data between requests there.

### 1. Push to GitHub

```bash
git init
git add .
git commit -m "Khidmat Earnings Tracker"
git branch -M main
git remote add origin https://github.com/<you>/khidmat-tracker.git
git push -u origin main
```

`.gitignore` already excludes `.env`, `instance/`, and `__pycache__/` —
double-check `git status` doesn't show your local `.env` before pushing.

### 2. Provision a PostgreSQL database

Any managed Postgres works — pick one:
- **Vercel Postgres** (Storage tab in your Vercel project)
- [Neon](https://neon.tech) or [Supabase](https://supabase.com) (both have a free tier)
- Railway, Render, or your own server

Copy the connection string it gives you — it will look like
`postgresql://user:password@host:5432/dbname` (if a provider hands you
`postgres://` instead, the app normalizes that automatically).

### 3. Create the schema

Run this once, from your machine, pointed at the production database
(swap in your real connection string):

```bash
pip install -r requirements.txt
FLASK_APP=run.py DATABASE_URL="postgresql://user:password@host:5432/dbname" \
  SECRET_KEY="temporary-for-this-command" flask db upgrade
```

This applies `migrations/versions/` and creates every table, index, and
constraint. For any future schema change: edit the models, then run
`flask db migrate -m "description"` followed by `flask db upgrade`
against production the same way.

### 4. Deploy to Vercel

Either via the dashboard (import the GitHub repo, Vercel auto-detects
`vercel.json`) or the CLI:

```bash
npm install -g vercel
vercel login
vercel --prod
```

### 5. Set environment variables in Vercel

Project → **Settings → Environment Variables** — add:

```
SECRET_KEY=<output of: python -c "import secrets; print(secrets.token_hex(32))">
FLASK_ENV=production
DATABASE_URL=<your postgresql:// connection string>
SESSION_COOKIE_SECURE=true
```

Add the Google OAuth variables too if you use Calendar sync (next
section). Redeploy after saving so the new variables take effect.

### 6. Google OAuth production callback

Google OAuth redirect URIs must match **exactly** — a local
`http://127.0.0.1:5000/...` credential will not work on your live
domain. In [Google Cloud Console → APIs & Services →
Credentials](https://console.cloud.google.com/apis/credentials), open
your OAuth client and add a **second** Authorized redirect URI for
production:

```
https://your-app.vercel.app/calendar/oauth/callback
```

(Keep the `127.0.0.1` one too — one OAuth client can have several
redirect URIs, so local dev keeps working.) Then in Vercel's
environment variables, set:

```
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret
GOOGLE_REDIRECT_URI=https://your-app.vercel.app/calendar/oauth/callback
```

If your OAuth consent screen is still in **Testing** status, only the
Google accounts you've added as test users can sign in — publish it (or
add your account as a test user) before relying on this in daily use.

### Known limitations of the Vercel deployment specifically

- **Backups are SQLite-only.** Once `DATABASE_URL` points at Postgres,
  the Settings → Backups feature is intentionally disabled (it would
  otherwise misinterpret a Postgres connection string as a file path).
  Back up a production Postgres database with your provider's own
  export/backup tools instead.
- **Calendar sync duration.** Vercel serverless functions have a
  execution time limit (10s on the Hobby plan; higher on Pro). A sync
  covering the full default 90-day-back/30-day-forward window with a
  very large number of calendar events could theoretically approach
  that limit — not something a personal calendar is likely to hit, but
  worth knowing before syncing an unusually event-heavy calendar for
  the first time.

## Google Calendar setup

Google Calendar sync is optional — everything else works fully without
it. To enable it:

### 1. Google Cloud Console setup

1. Go to [console.cloud.google.com](https://console.cloud.google.com/)
   and create a new project (or pick an existing one).
2. **APIs & Services → Library**: search for "Google Calendar API" and
   click **Enable**.
3. **APIs & Services → OAuth consent screen**:
   - User type: **External** (unless you have a Google Workspace org, in
     which case Internal also works).
   - Fill in an app name, your email as support contact, and your email
     again under developer contact.
   - Scopes: you don't need to add any here manually — the app requests
     `calendar.readonly` and a basic email/profile scope at connect time.
   - Test users: while the app is in "Testing" publishing status, add
     your own Google account's email here, or Google will refuse to let
     you sign in.
4. **APIs & Services → Credentials → Create Credentials → OAuth client
   ID**:
   - Application type: **Web application**.
   - Authorized redirect URIs: add exactly the value you'll put in
     `GOOGLE_REDIRECT_URI` below, e.g.
     `http://127.0.0.1:5000/calendar/oauth/callback` for local use.
   - Save, then copy the **Client ID** and **Client Secret**.

### 2. Configure the app

In your `.env`:

```bash
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret
GOOGLE_REDIRECT_URI=http://127.0.0.1:5000/calendar/oauth/callback
```

Restart the app after editing `.env`.

### 3. Connect and use it

On the **Calendar** page, click **Connect Google Calendar**, sign in,
and grant read-only access. Then set up **Mapping Rules** — e.g. "any
event whose title contains SBHS → the SBHS source" — before your first
sync, so events can be auto-imported instead of landing as drafts.
Click **Sync Now** to pull events from the last 90 days through the
next 30 days.

**What sync does, precisely:**
- Every event is read-only from Google's side — this app never writes
  anything back to your calendar.
- An event's duration is computed directly from its actual start/end
  timestamps (Google always gives full datetimes), then run through the
  exact same rate-resolution and Decimal calculation as a manually
  typed session — Calendar is an input source, not a calculator.
- An event you've already imported is never imported twice, even if you
  sync the same date range repeatedly (matched on calendar ID + event ID).
- If you edit an event's time in Google Calendar, the next sync updates
  the linked session's duration and recalculates its amount.
- If you delete an event in Google Calendar, the next sync does **not**
  delete the financial record — it flags the linked session as
  "source deleted" for you to review, and the session's amount is left
  exactly as it was.
- Events that don't match any mapping rule (or are all-day events with
  no specific time) become **drafts** on the Calendar page for you to
  manually map to a source or ignore — nothing is ever guessed.
- Every sync run is logged (events found/imported/updated/skipped/
  needing review, and any error) and shown in "Recent Syncs."

**Security:** only a refresh token is stored, encrypted at rest
(`services/token_crypto.py`, derived from `SECRET_KEY`) — never your
Google password, and access tokens are requested fresh for each sync
and held only in memory, never persisted or sent to the browser.



| Phase | Scope | Status |
|---|---|---|
| V1 Core | Manual sessions, deterministic engine, dashboard | **Done** |
| V1.1 History | Rate history UI (incl. editing), tuition, payments, adjustments | **Done** |
| V2.1 Reports/UX | Charts, calendar widget, Goals, Reports (PDF/CSV), responsive dashboard | **Done** |
| V2 Calendar | Google OAuth, sync, mapping, dedupe, edit/delete reconciliation | **Done** |
| V4 Hardening | CSRF, PIN lock, encrypted backups, security-reviewed auth boundary | **Done** |
| V3 AI | Natural-language Q&A over your own data | **Done** |
| Phase 7 QA/Deployment | Full audit, cleanup, DB migrations, PostgreSQL + Vercel readiness | **Done** |

## Assistant (Phase 6 / V3 AI)

A chat widget on the dashboard answers plain-English questions about
your own recorded data — e.g. *"how much did I earn from SGHS in
July?"*, *"how many hours did I work last month?"*, *"how much is
pending from tuition?"*, *"why is this month lower than last month?"*.

This is **not** an external AI model. `services/assistant_service.py`
is local, deterministic pattern matching: it decides which metric,
date range, and source you mean, then hands that off to the exact same
`earnings_query.py` / `analytics_service.py` / `calculation_engine.py`
functions the dashboard and reports already use. No question, session
data, or earnings figure is ever sent anywhere, and no number in an
answer is ever generated except by that deterministic code — matching
the blueprint's "AI assists interpretation, never the money math"
principle exactly. Trend questions ("why is X lower than Y") are
answered by computing the real delta between two periods and reporting
which category (Khidmat / Tuition / Other) moved the most — never a
guessed explanation.

## Reports, charts, and goals

**Reports** (`/reports`) filters by date range (This Month / Last Month /
This Year / Custom) and by source or student, shows Total Earnings /
Paid / Pending / Hours / Sessions / Effective Rate, and exports to CSV
or a formatted PDF — the export always uses the exact same query as the
page you're looking at, so they can never disagree.

**Charts** on the dashboard (earnings + hours trend, source breakdown)
are rendered client-side with Chart.js from real database values passed
down from the server — nothing is fabricated, and an empty account shows
empty charts rather than demo numbers.

**Goals** (`/goals`) lets you set a monthly income/hours/sessions target;
progress is computed from actual recorded data for the current month.

**Calendar widget** on the dashboard shows the current month with a dot
on any day that has a session; clicking a day filters the Sessions page
to that date.

## Backups

**Only applies to local SQLite.** If `DATABASE_URL` is set to
PostgreSQL (as it should be for a Vercel/production deployment — see
**Deployment** above), the Settings page shows a notice instead of
these controls, and backs up nothing itself — use your Postgres
provider's own backup/export tools there.

On the **Settings** page (SQLite deployments only):

- **Backup Now** creates a timestamped, encrypted snapshot of the whole
  database under `instance/backups/` (never inside `app/static/`, so
  Flask never serves it as a public file — the only way to reach a
  backup is through an owner-authenticated route).
- **Export** downloads a chosen backup as a real, plain `.db` file
  (decrypted on the fly, server-side, for that one authenticated
  request only).
- **Restore** decrypts and validates a chosen backup, automatically
  takes one more safety backup of whatever's live *right before*
  overwriting it, then replaces the live database. A restore is never a
  one-way door — if you restore the wrong file, the state from just
  before that restore is itself sitting in the backup list.
- Backup files are encrypted at rest with the same Fernet scheme as
  Google refresh tokens (`services/token_crypto.py`), keyed from
  `SECRET_KEY`. Filenames only ever match a strict internally-generated
  pattern (timestamp + random suffix) — arbitrary or path-traversal
  filenames (`../../etc/passwd`, etc.) are rejected before any
  filesystem access happens.
- **Recommended procedure:** back up before any risky change (bulk rate
  edits, restoring an old backup, upgrading the app), and periodically
  copy the `instance/backups/` folder itself somewhere off this machine
  — these backups protect against mistakes inside the app, not against
  losing the whole disk.

## App lock (PIN)

Separate from your login password: set a 4-8 digit PIN on the Settings
page to enable an idle auto-lock (default 10 minutes, configurable via
`PIN_AUTO_LOCK_MINUTES` in `.env`). While locked, the app stays fully
loaded server-side but every page redirects to a PIN entry screen until
you unlock it — no data is visible in between. You can also lock
manually any time via the sidebar. Guests are never subject to the
owner's PIN, and the PIN is completely optional.

## Security notes (current state)

- **Passwords & PINs:** hashed with Werkzeug's `scrypt`-based hasher —
  never stored in plaintext, checked by the test suite.
- **Authorization:** every route that touches financial data is
  decorated and scoped to `session["user_id"]`; there is no route that
  relies on hiding a UI element instead of a server-side check. This is
  verified by an automated sweep (`tests/test_security_review.py`) that
  walks every registered route as an unauthenticated visitor, a guest,
  and the owner, and confirms each is treated correctly — not a manual
  spot check.
- **CSRF:** every state-changing form and AJAX request carries a
  per-session token (Flask-WTF); a POST without a valid token is
  rejected with 400 before it reaches any view logic.
- **XSS:** Jinja2's autoescaping is on everywhere (no `|safe` filters
  anywhere in the codebase); verified with a test that stores a
  `<script>` payload in an adjustment reason and a student name and
  confirms it renders escaped, not executed.
- **SQL injection:** all queries go through the SQLAlchemy ORM; there is
  no raw, string-formatted SQL anywhere in the app.
- **Guest isolation:** guest mode is a separate code path that clears
  the session on entry and never queries or writes the owner's tables —
  verified for every route, not just the obvious ones.
- **Google OAuth:** only a refresh token is stored (never your Google
  password), encrypted at rest via Fernet with a key derived from
  `SECRET_KEY`. Access tokens are requested fresh per sync, held only in
  memory for that one request, and never appear in any template, JSON
  response, or log line — verified by a test that plants a fake token in
  the database and confirms it's absent from every owner-facing page's
  rendered output.
- `SECRET_KEY` and all credentials load from `.env`, never hard-coded.
- Session cookies are `HttpOnly` and `SameSite=Lax`; `Secure` is
  configurable for HTTPS deployments.
- **Known limitation:** backups live in one shared `instance/backups/`
  folder rather than being namespaced per owner. This is safe *only*
  because the app enforces exactly one owner account system-wide (see
  `/setup`) — if that constraint were ever relaxed for a genuinely
  multi-tenant deployment, backup storage would need to become
  per-owner too. Flagged explicitly rather than silently assumed safe.
- **Not yet implemented:** rate limiting on login attempts, and
  dependency-vulnerability scanning in CI. HTTPS/secure-cookie behavior
  behind a reverse proxy (Vercel or otherwise) is handled via
  `ProxyFix` + `SESSION_COOKIE_SECURE`; see **Deployment** above.
  Reasonable for personal use as-is; add login rate limiting before
  exposing this on a public network with a guessable owner email.

## Troubleshooting

- **"No rate is defined for this source covering <date>"** — add a rate
  period on the Rates page that covers that date before saving the
  session.
- **Overlapping rate period error** — rate periods for the same source
  can't overlap; either close the previous period's "effective to" date
  or choose a start date after it ends.
- **Forgot the owner password** — there's no reset flow yet (V4). Stop
  the app, delete `instance/khidmat.db`, and run `/setup` again (this
  discards all data, so only do this in development).
