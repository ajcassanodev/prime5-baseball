"""Playground Friday rotation (Nov 27, 2026): every Tuesday the
playground draws a new random Friday from the 2026 season.

Decided by Adam Nov 27, 2026; first rotation Tue Dec 1, 2026; runs
weekly through the January testing window until he stops it.

Rules:
- Pool: 2026 regular-season Fridays with a FULL slate (10+
  scheduled games, doubleheaders counted game by game). The pool is
  fetched once from the MLB Stats API and cached in the
  playground_fridays table; the current week (Sep 25, 2026) is
  recorded as drawn at pool build so it is never redrawn.
- Draw: random, seeded deterministically by the Tuesday date, so a
  failed rotation retries the same Friday and the draw is
  reproducible. No repeats until the pool is exhausted; on
  exhaustion the used marks reset (except the current Friday) and
  drawing resumes.
- Each rotation is a FRESH week: the new Friday's games, lineups and
  results replace the playground week, all picks for the week are
  cleared, and week state (grants, auto-pick declines, form,
  weather) resets. Members and pairings stay the same.
- Form + weather come along for the new Friday, computed the same
  way the poller does after ingest (7-day window + season stats;
  ballpark weather at first pitch).
- Failure mode: the new Friday is fetched and validated fully
  BEFORE anything is wiped. If the fetch fails, the current week
  stays exactly as it is and the next run retries the same draw.
  Nothing is ever left half-loaded.

Weather caveat: Open-Meteo's forecast API only reaches ~92 days
back (past_days); older Fridays fall back to the archive, which has
no precipitation_probability — those rows carry temperature,
conditions and wind, but precip_prob is NULL and the delay-risk
flag falls back to "any precipitation in the first-pitch hour" (see
app/weather.py). Nothing is invented.

Runs from the poller (poller/poll.py, no --date): on Tuesdays it
rotates if due; on Fridays it ingests. `poll.py --rotate` runs a
rotation check on demand.
"""
import random
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import db as dbmod
from . import form_ingest as form_ingest_mod
from . import mlb as mlb_mod

ET = ZoneInfo("America/New_York")

# A playable Friday needs a full slate: 10+ scheduled games,
# doubleheaders counted game by game.
MIN_GAMES = 10

# 2026 regular season ran Mar 26–Sep 27; Mar 27 is its first Friday,
# Sep 25 its last. Mar 27 itself had only 8 games, so the 10-game
# rule drops it from the pool in practice.
SEASON_FIRST_FRIDAY = date(2026, 3, 27)
SEASON_LAST_FRIDAY = date(2026, 9, 25)


def today_et(now: datetime | None = None) -> date:
    """Today's date in America/New_York (the league's timezone)."""
    return (now or datetime.now(ET)).date()


def season_fridays() -> list:
    """Every Friday of the 2026 regular season, earliest first."""
    out = []
    d = SEASON_FIRST_FRIDAY
    while d <= SEASON_LAST_FRIDAY:
        out.append(d)
        d += timedelta(days=7)
    return out


def playground_label(friday: date) -> str:
    """'Playground — Fri Jul 10, 2026' — same shape as the seed label."""
    return f"Playground — Fri {friday.strftime('%b')} {friday.day}, {friday.year}"


def playground_week(con) -> dict | None:
    """The playground week row, or None when there is no playground."""
    row = con.execute(
        "SELECT season, week, friday_date, label, playground FROM weeks"
        " WHERE playground = 1 ORDER BY week LIMIT 1").fetchone()
    return dict(row) if row else None


def _pool_rows(con) -> list:
    rows = con.execute(
        "SELECT friday_date, games, drawn_on FROM playground_fridays"
        " ORDER BY friday_date").fetchall()
    return [dict(r) for r in rows]


def ensure_pool(con) -> list:
    """Build the eligible-Friday pool on first use (27 schedule
    fetches, cached in the DB afterwards) and return its rows.

    The build fetches every 2026 Friday's schedule and keeps the
    ones with 10+ games. It is built fully in memory first, so a
    failed fetch inserts nothing and the next run retries the whole
    build. The current playground week is recorded as drawn (with
    its own date — a Friday, so it can never match a Tuesday
    rotation check) so it is never redrawn.
    """
    rows = _pool_rows(con)
    if rows:
        return rows
    week = playground_week(con)
    current = week["friday_date"] if week else None
    eligible = []
    for friday in season_fridays():
        games = mlb_mod.parse_schedule(
            mlb_mod.fetch_schedule(friday.isoformat()))
        if len(games) >= MIN_GAMES:
            eligible.append((friday.isoformat(), len(games)))
    if current and current not in {d for d, _ in eligible}:
        # A short-slate current week still counts as used.
        current_games = len(mlb_mod.parse_schedule(
            mlb_mod.fetch_schedule(current)))
        eligible.append((current, current_games))
    for friday_date, games in sorted(eligible):
        drawn_on = friday_date if friday_date == current else None
        con.execute(
            "INSERT OR IGNORE INTO playground_fridays"
            " (friday_date, games, drawn_on) VALUES (?, ?, ?)",
            (friday_date, games, drawn_on))
    con.commit()
    print(f"playground pool: {len(eligible)} eligible Fridays"
          f" (current week {current} marked used)")
    return _pool_rows(con)


def draw_for_tuesday(tuesday: date, unused: list) -> str:
    """The Friday this Tuesday draws, from the unused pool.

    Seeded by the Tuesday date: the same Tuesday always draws the
    same Friday, so a failed rotation retries the same draw and the
    result is reproducible.
    """
    rng = random.Random(f"playground-rotation-{tuesday.isoformat()}")
    return rng.choice(sorted(unused))


def fetch_friday(friday_date: str) -> dict:
    """Fetch one Friday fully into memory (schedule + every boxscore),
    seed-file shaped: {games, players, rosters, lineups, stats}.

    Raises mlb_mod.MlbError on any fetch failure, and when the
    Friday turns out short of a full slate — the caller treats
    either as 'do not rotate, keep the current week'.
    """
    games = mlb_mod.parse_schedule(mlb_mod.fetch_schedule(friday_date))
    if len(games) < MIN_GAMES:
        raise mlb_mod.MlbError(
            f"{friday_date} has {len(games)} games, need {MIN_GAMES}")
    out = {"games": [], "players": [], "lineups": [], "stats": [],
           "rosters": []}
    seen_players: dict = {}
    for g in games:
        home_starter = g["home_starter"] or {}
        away_starter = g["away_starter"] or {}
        out["games"].append({
            "game_pk": g["game_pk"], "game_date_utc": g["game_date_utc"],
            "status": g["status"],
            "home_team": g["home"]["abbr"], "away_team": g["away"]["abbr"],
            "home_starter_mlb_id": home_starter.get("mlb_id"),
            "away_starter_mlb_id": away_starter.get("mlb_id"),
        })
        box = mlb_mod.parse_boxscore(mlb_mod.fetch_boxscore(g["game_pk"]))
        for player in box["players"]:
            seen_players[player["mlb_id"]] = player
            out["rosters"].append({"game_pk": g["game_pk"],
                                    "mlb_id": player["mlb_id"]})
        for side in ("home", "away"):
            for order, mlb_id in enumerate(box["batting_order"][side], 1):
                out["lineups"].append({"game_pk": g["game_pk"],
                                        "mlb_id": mlb_id, "role": "batter",
                                        "batting_order": order})
        for mlb_id in box["starters"]:
            out["lineups"].append({"game_pk": g["game_pk"], "mlb_id": mlb_id,
                                    "role": "pitcher", "batting_order": None})
        for stat in box["stats"]:
            out["stats"].append({"game_pk": g["game_pk"], **stat})
    out["players"] = sorted(seen_players.values(), key=lambda p: p["name"])
    return out


def load_friday(con, season: int, week: int, friday_date: str,
                data: dict) -> None:
    """Load fetched Friday data into the week (same helpers and order
    as scripts/seed.py). Assumes the week was already wiped."""
    for game in data["games"]:
        dbmod.upsert_game(con, {**game, "season": season, "week": week,
                                 "friday_date": friday_date})
    for player in data["players"]:
        dbmod.upsert_player(con, player)
    for row in data.get("rosters", []):
        dbmod.add_roster(con, row["game_pk"], row["mlb_id"])
    for row in data["lineups"]:
        dbmod.set_lineup(con, row["game_pk"], row["role"], row["mlb_id"],
                         row.get("batting_order"))
    for stat in data["stats"]:
        dbmod.upsert_stats(con, stat["game_pk"], stat)
    con.commit()


def clear_week(con, season: int, week: int) -> None:
    """Wipe one week's games, lineups, stats, form, weather, picks,
    grants and auto-pick declines. Members, players and the weeks
    row itself stay — the weeks row is re-pointed by the caller.
    Children first (foreign keys are ON)."""
    game_pks = [g["game_pk"]
                for g in dbmod.games_for_week(con, season, week)]
    con.execute("DELETE FROM picks WHERE season = ? AND week = ?",
                (season, week))
    con.execute(
        "DELETE FROM head_to_head_grants WHERE season = ?"
        " AND (week_earned = ? OR use_week = ?)",
        (season, week, week))
    con.execute(
        "DELETE FROM auto_pick_declines WHERE season = ? AND week = ?",
        (season, week))
    con.execute(
        "DELETE FROM player_form WHERE season = ? AND week = ?",
        (season, week))
    if game_pks:
        marks = ",".join("?" for _ in game_pks)
        con.execute(
            f"DELETE FROM game_weather WHERE game_pk IN ({marks})",
            game_pks)
        con.execute(
            f"DELETE FROM player_game_stats WHERE game_pk IN ({marks})",
            game_pks)
        con.execute(
            f"DELETE FROM lineups WHERE game_pk IN ({marks})", game_pks)
        con.execute(
            f"DELETE FROM rosters WHERE game_pk IN ({marks})", game_pks)
    con.execute("DELETE FROM games WHERE season = ? AND week = ?",
                (season, week))
    con.commit()


def rotate(con, tuesday: date) -> dict:
    """Rotate the playground to this Tuesday's draw, if due.

    Returns {"rotated": bool, "friday": str|None, "reason": str,
    "games": int, "form": int, "weather": int, "refresh_error":
    str|None}. Never raises for an expected miss (not Tuesday, no
    playground, already rotated, fetch failed) — those come back as
    rotated False with a reason, and the current week is untouched.
    """
    result = {"rotated": False, "friday": None, "reason": "",
              "games": 0, "form": 0, "weather": 0, "refresh_error": None}
    if tuesday.weekday() != 1:  # 1 = Tuesday
        result["reason"] = "not Tuesday"
        return result
    week = playground_week(con)
    if week is None:
        result["reason"] = "no playground week"
        return result
    season, wk = week["season"], week["week"]
    current = week["friday_date"]
    tuesday_iso = tuesday.isoformat()
    drawn = con.execute(
        "SELECT friday_date FROM playground_fridays WHERE drawn_on = ?",
        (tuesday_iso,)).fetchone()
    if drawn:
        result["reason"] = "already rotated this week"
        result["friday"] = drawn["friday_date"]
        return result
    pool = None
    try:
        pool = ensure_pool(con)
    except mlb_mod.MlbError as exc:
        # Pool build failed: nothing was inserted (the build is
        # all-or-nothing), the current week is untouched, and the
        # next run retries the build.
        print(f"playground pool build failed: {exc}")
        result["reason"] = "pool build failed"
        return result
    unused = [r["friday_date"] for r in pool
              if r["drawn_on"] is None and r["friday_date"] != current]
    if not unused:
        # Pool exhausted: reset the used marks (except the current
        # Friday) and draw again — repeats only after every eligible
        # Friday has been used once. Rows drawn by a Tuesday keep a
        # Tuesday in drawn_on; the pre-existing week keeps its own
        # (Friday) date there, so `drawn_on != friday_date` resets
        # only real draws and Sep 25 stays used forever.
        con.execute(
            "UPDATE playground_fridays SET drawn_on = NULL"
            " WHERE friday_date != ? AND drawn_on != friday_date",
            (current,))
        con.commit()
        print("playground pool exhausted — used marks reset")
        unused = [r["friday_date"] for r in _pool_rows(con)
                  if r["drawn_on"] is None and r["friday_date"] != current]
        if not unused:
            result["reason"] = "pool exhausted"
            return result
    target = draw_for_tuesday(tuesday, unused)
    try:
        data = fetch_friday(target)
    except mlb_mod.MlbError as exc:
        # Fetch failed: keep the current week exactly as it is. No
        # draw is recorded, so the next run retries the same draw.
        print(f"playground rotation fetch failed for {target}: {exc}")
        result["reason"] = f"fetch failed for {target}"
        return result
    clear_week(con, season, wk)
    load_friday(con, season, wk, target, data)
    con.execute(
        "UPDATE weeks SET friday_date = ?, label = ?"
        " WHERE season = ? AND week = ?",
        (target, playground_label(date.fromisoformat(target)), season, wk))
    con.execute(
        "UPDATE playground_fridays SET drawn_on = ? WHERE friday_date = ?",
        (tuesday_iso, target))
    con.commit()
    result["rotated"] = True
    result["friday"] = target
    result["games"] = len(data["games"])
    # Form + weather come along, same as after a Friday ingest. A
    # failure here must not re-wipe the fresh week: retry once (the
    # refresh only fills missing rows), then keep the week and log.
    counts = {"form": 0, "weather": 0}
    for attempt in (1, 2):
        try:
            counts = form_ingest_mod.refresh_form_and_weather(
                con, season, wk, target)
            break
        except Exception as exc:  # noqa: BLE001 — games already landed
            print(f"playground form/weather refresh failed for {target}"
                  f" (attempt {attempt}): {exc}", flush=True)
            if attempt == 2:
                result["refresh_error"] = str(exc)
    result["form"] = counts["form"]
    result["weather"] = counts["weather"]
    return result
