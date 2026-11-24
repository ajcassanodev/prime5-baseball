# Prime5 Baseball — 2027 core scaffold

Standalone FastAPI + SQLite app for Adam's 2027 Prime5 Baseball game.
Core only, as approved in the Nov 23, 2026 planning session (mock v3):
pick **5 batters + 1 Friday starting pitcher**, with lineup-status dots,
game start times, and opposing pitchers on every row.

**Live site:** not deployed yet. Beta subdomain decided Nov 23:
`staging-baseball.ravixfs.com` (DNS + server setup still to do — see
"Before it can run on a server" below).

## What it does

- **Pick page** (`/picks`): batters and pitchers grouped by game time.
  Green dot = in Friday's starting lineup, gray dot = not. Every row
  shows its game start time (ET) and the opposing starting pitcher.
  Click to pick; **Lock picks** submits when 5 batters + 1 pitcher
  are chosen.
- **Scoring** (computed, never stored): batters 1 pt per hit; pitcher
  0.01/pt per pitch + 1 per strikeout. See `app/scoring.py`.
- **Lock rules**: a pick whose game has already started is rejected,
  and picks for started games are frozen on re-submit (editing
  unstarted picks stays free). See `app/picks.py`.
- **Pick-link gate**: members sign in via `/submit/<token>`, which sets
  a host-only cookie (180 days). No cookie → `/locked`. Test members
  carry `is_test` and are tagged "(test)" in the standings.
- **Registration + approval**: public `/register` page — first/last
  name, email, phone (optional), played-in-2026?, referrer (optional),
  optional invite token. Submitting creates a PENDING member (no pick
  link yet) and shows the approval-pending state. `/admin/<ADMIN_TOKEN>`
  lists pending registrations with Approve / Decline; approving
  activates the member and emails their pick link (Resend SMTP when
  configured, otherwise the link is printed to stdout — approval never
  fails for lack of mail). Pending/declined members cannot sign in.
  No password anywhere: the pick link is the sign-in.
- **Welcome pop-up**: on a member's first visit the pick page shows a
  3-step tutorial (pick 5+1, lineup dots + doubleheader rule, lock at
  first pitch + scoring). Dismissing (X or "Got it — let's pick") POSTs
  `/welcome-seen`, which sets `seen_welcome` on the member row so it
  shows once; "How to play" reopens it any time. Covered in
  `tests/test_welcome.py`.
- **Data**: MLB Stats API (`statsapi.mlb.com/api/v1`, free, no key).
  Schedule with `hydrate=lineups`, boxscore (batting order, hits,
  pitch counts, strikeouts), gameLog, live feed v1.1. See `app/mlb.py`.

## What it does NOT do (later sprinkle-ins, per Nov 23 session)

Captain mechanic (incl. HR/RBI values), hit streak, milestone
mechanic, payments, ticker/news.
The Nov 23 session parked keepers and captain values until the core
is built; the captain gets sprinkled in closer to preseason.

## Layout

- `app/` — FastAPI site
  - `main.py` — routes, pick-link gate, pick page, dashboard, standings,
    registration, admin
  - `db.py` — SQLite helpers, schema init, member/pick/game queries
  - `emailer.py` — SMTP email (stdlib only), mirrors the football app
  - `mlb.py` — MLB Stats API client + pure parsing helpers
  - `scoring.py` — pure scoring functions
  - `lineup.py` — pure lineup-status helpers
  - `picks.py` — pick validation + submit/lock rules
  - `templates/` — Jinja2 pages (pick, dashboard, standings, locked,
    register, register_pending, admin, not_found)
  - `static/style.css` — Ravix dark theme
- `poller/poll.py` — Friday schedule + boxscores → SQLite
- `scripts/fetch_seed.py` — pulls the real Sep 25, 2026 Friday → `data/`
- `scripts/seed.py` — loads the seed file + creates a demo test member
- `scripts/add_member.py` — create a member, print their pick link
- `data/seed_friday_2026-09-25.json` — the full Sep 25, 2026 Friday:
  17 games, 839 players, 340 lineup rows, 952 roster rows, 494 stat
  rows, fetched live from the MLB Stats API (Nov 23, 2026)
- `schema.sql` — full DB schema
- `tests/` — pytest (scoring, lineup, picks/lock rules, MLB parsing)

## Run locally

```bash
python3 -m venv venv && venv/bin/pip install -r requirements.txt
venv/bin/python scripts/seed.py        # loads the playground week + demo member
PRIME5BB_DB=$PWD/baseball.db venv/bin/uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000 — the seed script prints a demo pick link
(`/submit/<token>`) to sign in as the demo test member. A seeded
`baseball.db` is already in the project root from the scaffold run.

Run the tests:

```bash
venv/bin/python -m pytest tests/ -q
```

Poll a Friday into the DB (creates the week if needed):

```bash
PRIME5BB_DB=$PWD/baseball.db venv/bin/python poller/poll.py --date 2027-03-26
```

Add a member:

```bash
venv/bin/python scripts/add_member.py "Ada Lovelace" --email ada@example.com [--test]
```

Environment: `PRIME5BB_DB` (default `<project>/baseball.db`),
`PRIME5BB_BASE_URL` (default `http://127.0.0.1:8000`, used only when
printing pick links), `ADMIN_TOKEN` (required for `/admin/*`; without
it the admin pages 404).

Email (pick-link on approval) uses the same env names as the football
app: `RAVIX_SMTP_HOST` (default smtpout.secureserver.net),
`RAVIX_SMTP_PORT` (default 465), `RAVIX_SMTP_USER`,
`RAVIX_SMTP_PASS`, `RAVIX_EMAIL_FROM` (default info@ravixfs.com),
`RAVIX_SMTP_STARTTLS=1` for explicit TLS (e.g. Resend on port 587).
With no SMTP user/pass configured, approving still succeeds and the
pick link is printed to stdout instead of emailed.

Invite links: Adam sends `/register?invite=<token>&email=<email>` —
the token is stored on the member record (no validation against a
list yet) and the email is prefilled. Registration is also linked
from the `/locked` page.

## Playground week

The seed is season 2026, week 1 — Friday Sep 25, 2026 — flagged
`playground`. On a playground week picks are allowed even after the
games end, so members can try picks against real past data and see
immediate scores. Verified examples in the seed: Roman Anthony 3 H,
Pete Alonso 3 H (2 HR), Clay Holmes 78 pitches / 3 K (3.78 pts),
Brendan Beck 97 pitches / 8 K (8.97 pts). Bench players who did not
play that Friday (e.g. Willson Contreras, Ceddanne Rafaela, Heston
Kjerstad) are on the rosters with no stats — they render with gray
dots and score 0 if picked.

## Known limitations / decisions still open

- **Doubleheaders — DECIDED Nov 23, 2026 (Adam)**: on a doubleheader
  day a batter appears as one pick row per game (each row shows that
  game's start time + opposing pitcher). A member may pick either game
  or both; picking both games of the same player uses 2 of the 5
  batter slots. Each row scores only its own game's hits (1 pt/hit)
  and locks at its own game's first pitch. Pitchers are unaffected (a
  starter only starts one game). Implemented in `app/picks.py`,
  `app/main.py`, `app/templates/pick.html`; covered in
  `tests/test_picks.py` (validation, per-game scoring, per-game freeze,
  HTTP flow). Seed example: Michael Busch, 2 H in each game of the
  Sep 25, 2026 Cubs @ Red Sox doubleheader.
- **Game-2 times**: the API lists the second Yankees game of the
  Sep 25 doubleheader at 4:10 PM ET, five minutes after the first
  (4:05 PM). That is the API's own time; the mock dropped that game
  rather than show it, but the seed keeps all real data.
- **Lineup timing**: lineups publish a few hours before first pitch,
  not at pick time during the week. Until a lineup lands, every player
  shows gray. The poller should run Friday morning/early afternoon to
  pick up announced lineups.
- **Live scoring**: no live poller timer yet — `poller/poll.py` is a
  one-shot ingest; a systemd timer (like football's 15-min poller)
  is a deploy-time decision.

## Before it can run on a server (not done — do not attempt blindly)

- **DNS**: DONE Nov 23 — GoDaddy A record `staging-baseball` → 5.161.193.98
  (Hetzner CX22, Ashburn), verified live via Google DNS (TTL 600).
- **Caddy**: reverse-proxy entry for `staging-baseball.ravixfs.com` →
  the app's port (pick one, e.g. 8002 — 8000 is football prod, 8001
  is football staging). The football `deploy/Caddyfile` is stale vs
  live; start Caddy changes from the live version.
- **systemd**: app service + a poller timer for Friday ingestion.
  Mirror football's `deploy/` units; no units exist for baseball yet.
- **Deploy flow**: the football flow is GitHub pull-based deploys
  (public repo, no credentials). Repo DECIDED Nov 24 (Adam): baseball
  gets its own repo (name TBD — `prime5-baseball` suggested, under
  `ajcassanodev`); no repo exists yet, so no deploy is wired.
- **Secrets**: none needed for the core (MLB API is keyless). Do not
  put any credential in the repo; the old `prime5_full_system.py`
  (hardcoded Gmail app password) was deliberately NOT reused here.
