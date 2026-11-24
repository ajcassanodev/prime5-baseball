"""Friday poller: schedule + boxscores -> SQLite.

Usage (from the project root):
    venv/bin/python poller/poll.py --date 2027-03-26 [--season 2027] [--week 1]

- Fetches the day's schedule (hydrate=lineups,probablePitcher) for games,
  lineups (once announced), and probable pitchers.
- For games that are final (or live), fetches the boxscore for the
  batting order, per-player stats, and the full game roster.
- Creates the week row if needed (week looked up by Friday date first).
- Safe to re-run: upserts only.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import db as dbmod
from app import mlb as mlb_mod


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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="Friday date YYYY-MM-DD")
    ap.add_argument("--season", type=int, default=None)
    ap.add_argument("--week", type=int, default=None)
    ap.add_argument("--playground", action="store_true",
                    help="mark the week as a playground (sandbox) week")
    args = ap.parse_args()
    dbmod.init_db()
    con = dbmod.connect()
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
    con.close()


if __name__ == "__main__":
    main()
