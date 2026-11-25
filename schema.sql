-- 2027 Prime5 Baseball — core schema (scaffold).
-- Points are computed, never stored; see app/scoring.py.
-- One "week" = one Friday. The playground week is 2026 week 1 (Fri Sep 25, 2026).

CREATE TABLE IF NOT EXISTS members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT,
    pick_token TEXT NOT NULL UNIQUE,
    is_test INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    seen_welcome INTEGER NOT NULL DEFAULT 0,  -- 1 once the first-visit
                                               -- welcome pop-up was dismissed
    -- Registration (Nov 24, 2026): invite -> register -> commissioner
    -- approval -> pick link. status: 'pending' | 'active' | 'declined'.
    -- Pending/declined members cannot sign in (see member_by_token).
    status TEXT NOT NULL DEFAULT 'active',
    phone TEXT,
    played_2026 INTEGER,  -- 1 played in 2026, 0 new, NULL unknown
    referrer TEXT,
    invite_token TEXT,
    -- Screen name (Nov 24, 2026): what other members see; NULL for
    -- legacy members, who fall back to the short name.
    screen_name TEXT,
    show_full_name INTEGER NOT NULL DEFAULT 0,  -- 1 = show the full
    -- name under the screen name instead of first name + last initial
    -- Division (Nov 24, 2026): 'red' = Red Hots, 'white' = White Hots.
    division TEXT NOT NULL DEFAULT 'red',
    -- Auto-pick (Nov 24, 2026): 1 = the poller fills the member's
    -- empty slots from Friday's lineup (random draw); off by default.
    auto_pick INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS weeks (
    season INTEGER NOT NULL,
    week INTEGER NOT NULL,
    friday_date TEXT NOT NULL,          -- YYYY-MM-DD, the Friday this week covers
    label TEXT,                          -- e.g. "Week 1 — Fri Sep 25, 2026"
    playground INTEGER NOT NULL DEFAULT 0,  -- 1 = sandbox week on past data; picks allowed after games end
    PRIMARY KEY (season, week)
);

CREATE TABLE IF NOT EXISTS games (
    game_pk INTEGER PRIMARY KEY,
    season INTEGER NOT NULL,
    week INTEGER NOT NULL,
    friday_date TEXT NOT NULL,
    game_date_utc TEXT NOT NULL,         -- ISO, first pitch (UTC)
    status TEXT NOT NULL DEFAULT 'Scheduled',
    home_team TEXT NOT NULL,             -- 3-letter abbreviation
    away_team TEXT NOT NULL,
    home_starter_mlb_id INTEGER,
    away_starter_mlb_id INTEGER,
    FOREIGN KEY (season, week) REFERENCES weeks (season, week)
);

CREATE TABLE IF NOT EXISTS players (
    mlb_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    team TEXT NOT NULL,                  -- 3-letter abbreviation
    position TEXT,                       -- e.g. LF, 1B, P
    is_pitcher INTEGER NOT NULL DEFAULT 0
);

-- Full game roster (from the boxscore players list, including bench
-- players who did not appear). Lineup status comes from `lineups`;
-- anyone on the roster but not in `lineups` gets the gray dot.
CREATE TABLE IF NOT EXISTS rosters (
    game_pk INTEGER NOT NULL,
    mlb_id INTEGER NOT NULL,
    PRIMARY KEY (game_pk, mlb_id),
    FOREIGN KEY (game_pk) REFERENCES games (game_pk)
);

-- Starting lineups, one row per player in a game's starting nine (+ starter).
-- role: 'batter' (batting_order 1-9) or 'pitcher' (batting_order NULL).
CREATE TABLE IF NOT EXISTS lineups (
    game_pk INTEGER NOT NULL,
    mlb_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    batting_order INTEGER,
    PRIMARY KEY (game_pk, mlb_id),
    FOREIGN KEY (game_pk) REFERENCES games (game_pk)
);

-- Per-game player stats (final or live). One row per player who appeared.
CREATE TABLE IF NOT EXISTS player_game_stats (
    game_pk INTEGER NOT NULL,
    mlb_id INTEGER NOT NULL,
    at_bats INTEGER NOT NULL DEFAULT 0,
    hits INTEGER NOT NULL DEFAULT 0,
    hr INTEGER NOT NULL DEFAULT 0,
    rbi INTEGER NOT NULL DEFAULT 0,
    pitches INTEGER NOT NULL DEFAULT 0,
    strikeouts INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (game_pk, mlb_id),
    FOREIGN KEY (game_pk) REFERENCES games (game_pk)
);

-- Member picks. slot: b1..b5 (batters), p1 (pitcher). kind: 'batter' | 'pitcher'.
-- auto: 1 when the pick was filled by the auto-picker (Nov 24, 2026),
-- 0 for a member-chosen pick.
CREATE TABLE IF NOT EXISTS picks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL,
    season INTEGER NOT NULL,
    week INTEGER NOT NULL,
    slot TEXT NOT NULL,
    kind TEXT NOT NULL,
    mlb_id INTEGER NOT NULL,
    game_pk INTEGER NOT NULL,
    auto INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (member_id, season, week, slot),
    FOREIGN KEY (member_id) REFERENCES members (id),
    FOREIGN KEY (game_pk) REFERENCES games (game_pk)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Head-to-head grants (Nov 24, 2026): winning a weekly matchup earns
-- +1 batter pick for the following week (use_week), use-it-or-lose-it.
-- One grant per (season, week_earned, member); used = 1 once the 6th
-- batter is saved. Ties and byes earn nothing.
CREATE TABLE IF NOT EXISTS head_to_head_grants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    season INTEGER NOT NULL,
    week_earned INTEGER NOT NULL,
    member_id INTEGER NOT NULL,
    use_week INTEGER NOT NULL,
    used INTEGER NOT NULL DEFAULT 0,
    UNIQUE (season, week_earned, member_id),
    FOREIGN KEY (member_id) REFERENCES members (id)
);

-- Auto-pick declines (Nov 24, 2026): when a member's submit drops a
-- pick the auto-picker had filled, the slot is recorded here and the
-- poller never fills that slot again for that member-week. Slot
-- numbers are the ones in force when the pick was dropped.
CREATE TABLE IF NOT EXISTS auto_pick_declines (
    member_id INTEGER NOT NULL,
    season INTEGER NOT NULL,
    week INTEGER NOT NULL,
    slot TEXT NOT NULL,
    PRIMARY KEY (member_id, season, week, slot),
    FOREIGN KEY (member_id) REFERENCES members (id)
);
