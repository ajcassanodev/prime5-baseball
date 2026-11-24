"""Fetch the real Sep 25, 2026 Friday and write the playground seed file.

Usage (from the project root):
    venv/bin/python scripts/fetch_seed.py

Writes data/seed_friday_2026-09-25.json — the 4 games that day
(Cubs @ Red Sox doubleheader, Orioles @ Yankees doubleheader) with
rosters, batting orders, starters, and per-player stats, exactly as
the MLB Stats API returns them.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import mlb as mlb_mod

DATE = "2026-09-25"
OUT = os.path.join(os.path.dirname(__file__), "..", "data",
                   "seed_friday_2026-09-25.json")


def main() -> None:
    games = mlb_mod.parse_schedule(mlb_mod.fetch_schedule(DATE))
    out = {
        "season": 2026, "week": 1, "friday_date": DATE,
        "label": "Playground — Fri Sep 25, 2026", "playground": True,
        "games": [], "players": [], "lineups": [], "stats": [], "rosters": [],
    }
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
                out["lineups"].append({"game_pk": g["game_pk"], "mlb_id": mlb_id,
                                        "role": "batter", "batting_order": order})
        for mlb_id in box["starters"]:
            out["lineups"].append({"game_pk": g["game_pk"], "mlb_id": mlb_id,
                                    "role": "pitcher", "batting_order": None})
        for stat in box["stats"]:
            out["stats"].append({"game_pk": g["game_pk"], **stat})
    out["players"] = sorted(seen_players.values(), key=lambda p: p["name"])
    with open(OUT, "w") as f:
        json.dump(out, f, indent=1)
    print(f"wrote {OUT}: {len(out['games'])} games,"
          f" {len(out['players'])} players, {len(out['lineups'])} lineup rows,"
          f" {len(out['rosters'])} roster rows, {len(out['stats'])} stat rows")


if __name__ == "__main__":
    main()
