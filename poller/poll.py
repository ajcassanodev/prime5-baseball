"""Friday poller: schedule + boxscores -> SQLite.

Usage (from the project root):
    venv/bin/python poller/poll.py --date 2027-03-26 [--season 2027] [--week 1]

- Fetches the day's schedule (hydrate=lineups,probablePitcher) for games,
  lineups (once announced), and probable pitchers.
- For games that are final (or live), fetches the boxscore for the
  batting order, per-player stats, and the full game roster.
- Creates the week row if needed (week looked up by Friday date first).
- Safe to re-run: upserts only.

Daily driver (no --date: the systemd timer calls it this way, every
15 minutes — see deploy/prime5bb-staging-poll.service, whose comment
already said poll.py defaults to today):
- Tuesday: rotate the playground to a new random 2026 Friday
  (app/rotation.py), if due. No ingest — ingesting a Tuesday would
  invent a week.
- Friday: ingest today, but only when the schedule has games, so
  offseason Fridays don't create empty weeks.
- Any other day: no-op.

    venv/bin/python poller/poll.py --rotate [--date 2026-12-01]

Runs the playground rotation check for that Tuesday (default: today)
on demand, e.g. to force the first rotation early.
"""
import argparse
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import auto_pick as auto_pick_mod
from app import db as dbmod
from app import form_ingest as form_ingest_mod
from app import mlb as mlb_mod
from app import rotation as rotation_mod


def ingest_date(con, season: int, week: int, friday_date: str,
                playground: bool = False) -> None:
    dbmod.ensure_week(con, season, week, friday_date, playground=playground)
    games = mlb_mod.parse_schedule(mlb_mod.fetch_schedule(friday_date))
    for g in games:
        home_starter = g["home_starter"] or {}
        away_starter = g["away_starter"] or {}
        dbmod.upsert_game(con, {
            "game_pk": g["game_pk"], "season": season, "week": week,
            "friday_date": friday_date, "game_date_utc": g["game_date_utc"],
            "status": g["status"],
            "home_team": g["home"]["abbr"], "away_team": g["away"]["abbr"],
            "home_starter_mlb_id": home_starter.get("mlb_id"),
            "away_starter_mlb_id": away_starter.get("mlb_id"),
        })
        # Pre-game lineups from the schedule (empty until announced).
        for side in ("home", "away"):
            for p in g["lineups"][side]:
                dbmod.upsert_player(con, {**p, "team": g[side]["abbr"]})
                dbmod.add_roster(con, g["game_pk"], p["mlb_id"])
                dbmod.set_lineup(con, g["game_pk"],
                                 "pitcher" if p["is_pitcher"] else "batter",
                                 p["mlb_id"], None)
        if g["status"] in ("Final", "In Progress", "Game Over"):
            box = mlb_mod.parse_boxscore(mlb_mod.fetch_boxscore(g["game_pk"]))
            for player in box["players"]:
                dbmod.upsert_player(con, player)
                dbmod.add_roster(con, g["game_pk"], player["mlb_id"])
            for side in ("home", "away"):
                for order, mlb_id in enumerate(box["batting_order"][side], 1):
                    dbmod.set_lineup(con, g["game_pk"], "batter", mlb_id, order)
            for mlb_id in box["starters"]:
                dbmod.set_lineup(con, g["game_pk"], "pitcher", mlb_id, None)
                # Keep the starters on the game row in sync.
                game = dbmod.game_by_pk(con, g["game_pk"])
                player = dbmod.player_by_id(con, mlb_id)
                if game and player:
                    col = ("home_starter_mlb_id" if player["team"] == game["home_team"]
                           else "away_starter_mlb_id")
                    con.execute(f"UPDATE games SET {col} = ? WHERE game_pk = ?",
                                (mlb_id, g["game_pk"]))
            for stat in box["stats"]:
                dbmod.upsert_stats(con, g["game_pk"], stat)
    con.commit()
    print(f"ingested {len(games)} games for {friday_date}"
          f" (season {season}, week {week})")


def _report_rotation(result: dict) -> None:
    if result["rotated"]:
        print(f"playground rotated to {result['friday']}:"
              f" {result['games']} games, {result['form']} form rows,"
              f" {result['weather']} weather rows")
        if result["refresh_error"]:
            print(f"playground form/weather incomplete:"
                  f" {result['refresh_error']}")
    else:
        print(f"playground rotation skipped: {result['reason']}")


def main(argv=None, today: date | None = None) -> None:
    """argv: CLI args (None = sys.argv). today: the date to act as
    (None = today in America/New_York) — injected by tests so the
    dispatch is deterministic regardless of the real run date."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None,
                    help="Friday date YYYY-MM-DD (default: today, ET)")
    ap.add_argument("--season", type=int, default=None)
    ap.add_argument("--week", type=int, default=None)
    ap.add_argument("--playground", action="store_true",
                    help="mark the week as a playground (sandbox) week")
    ap.add_argument("--rotate", action="store_true",
                    help="run the playground Tuesday rotation check"
                    " instead of ingesting (for --date, that Tuesday)")
    args = ap.parse_args(argv)
    dbmod.init_db()
    con = dbmod.connect()
    try:
        if args.rotate:
            target = (date.fromisoformat(args.date) if args.date
                      else (today or rotation_mod.today_et()))
            _report_rotation(rotation_mod.rotate(con, target))
            return
        if args.date is None:
            # Daily driver: Tuesdays rotate, Fridays ingest, other
            # days do nothing.
            day = today or rotation_mod.today_et()
            if day.weekday() == 1:  # Tuesday
                _report_rotation(rotation_mod.rotate(con, day))
                return
            if day.weekday() != 4:  # not Friday either
                print(f"poll: {day.isoformat()} is not a Tuesday or"
                      " Friday — nothing to do")
                return
            # Friday: only ingest when the schedule has games, so
            # offseason Fridays don't create empty weeks.
            try:
                games = mlb_mod.parse_schedule(
                    mlb_mod.fetch_schedule(day.isoformat()))
            except mlb_mod.MlbError as exc:
                print(f"poll: schedule fetch failed for"
                      f" {day.isoformat()}: {exc}")
                return
            if not games:
                print(f"poll: no games on {day.isoformat()} —"
                      " nothing to ingest")
                return
            args.date = day.isoformat()
        _ingest(con, args)
    finally:
        con.close()


def _ingest(con, args) -> None:
    season = args.season or dbmod.current_season(con)
    if args.week is None:
        row = con.execute(
            "SELECT week FROM weeks WHERE friday_date = ?",
            (args.date,)).fetchone()
        if row:
            week = row["week"]
        else:
            nxt = con.execute(
                "SELECT MAX(week) AS m FROM weeks WHERE season = ?",
                (season,)).fetchone()
            week = (nxt["m"] or 0) + 1
    else:
        week = args.week
    ingest_date(con, season, week, args.date, playground=args.playground)
    # Pick-help form + weather (Nov 25, 2026): computed from the
    # week's boxscores + season stats + Open-Meteo, stored in the DB
    # for the pick page. A failure here must never take down the
    # ingest — the data above is already committed — so it is
    # reported, not raised.
    try:
        counts = form_ingest_mod.refresh_form_and_weather(
            con, season, week, args.date)
    except Exception as exc:  # noqa: BLE001 — ingest already landed
        print(f"form/weather refresh failed for {args.date}: {exc}",
              flush=True)
    else:
        if counts["form"] or counts["weather"]:
            print(f"pick-help: {counts['form']} form row(s),"
                  f" {counts['weather']} weather row(s) for {args.date}")
    # Auto-pick (Nov 24, 2026): fill opted-in members' empty slots
    # from the lineups just ingested. A fill failure must never take
    # down the ingest — the data above is already committed — so it
    # is reported, not raised.
    try:
        filled = auto_pick_mod.auto_fill_week(con, season, week)["filled"]
    except Exception as exc:  # noqa: BLE001 — ingest already landed
        print(f"auto-pick failed for {args.date}: {exc}", flush=True)
    else:
        total = sum(len(slots) for slots in filled.values())
        if total:
            print(f"auto-picked {total} slot(s) for {len(filled)}"
                  f" member(s) for {args.date}")


if __name__ == "__main__":
    main()
