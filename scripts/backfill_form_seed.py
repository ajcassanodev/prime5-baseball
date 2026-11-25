"""Backfill real pick-help form + weather into the playground seed.

Usage (from the project root):
    venv/bin/python scripts/backfill_form_seed.py

Reads data/seed_friday_2026-09-25.json, computes REAL form for the
seed Friday's roster (Sep 18–24, 2026 boxscores + 2026 season stats
via the MLB Stats API) and REAL Sep 25, 2026 weather for each game's
ballpark (Open-Meteo forecast API, past_days — the archive API has
no precipitation_probability, so it can't drive the delay-risk
flag), and writes them back as the seed file's "form" and "weather"
keys. Re-running recomputes and overwrites both keys.

Nothing is invented: a player with no season stats gets a form row
with season_avg None and no probability; a game whose forecast is
unavailable gets no weather row (the pick page renders blanks).
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import form_guide as fg
from app import form_ingest as fi
from app import mlb as mlb_mod
from app import weather as wx

SEED = os.path.join(os.path.dirname(__file__), "..", "data",
                    "seed_friday_2026-09-25.json")


def main() -> None:
    with open(SEED) as f:
        seed = json.load(f)
    friday_date = seed["friday_date"]
    season = seed["season"]

    players = {p["mlb_id"]: p for p in seed["players"]}
    rosters = {}
    for row in seed.get("rosters", []):
        rosters.setdefault(row["game_pk"], set()).add(row["mlb_id"])
    batters = sorted({mid for ids in rosters.values() for mid in ids
                      if not players[mid].get("is_pitcher")})
    starters = sorted({g.get("home_starter_mlb_id")
                       for g in seed["games"]}
                      | {g.get("away_starter_mlb_id")
                         for g in seed["games"]} - {None})
    print(f"{len(batters)} roster batters, {len(starters)} Friday starters")

    print("fetching window boxscores (Sep 18–24)...")
    window_games = fi.fetch_window(friday_date)
    print(f"  {len(window_games)} games")
    window = fg.aggregate_window(window_games)

    print("fetching season stats...")
    season_hit = mlb_mod.parse_people_stats(
        mlb_mod.fetch_people_stats(batters, "hitting", season), "hitting")
    season_pit = mlb_mod.parse_people_stats(
        mlb_mod.fetch_people_stats(starters, "pitching", season), "pitching")
    print(f"  hitting stats for {len(season_hit)} batters,"
          f" pitching stats for {len(season_pit)} starters")

    # Opposing-starter ERA per (game, side).
    opp_era = {}
    for g in seed["games"]:
        for side, col in (("home", "away_starter_mlb_id"),
                          ("away", "home_starter_mlb_id")):
            opp_era[(g["game_pk"], side)] = (
                season_pit.get(g.get(col)) or {}).get("era")

    form = []
    no_season = 0
    for mlb_id in batters:
        era = None
        for g in sorted(seed["games"], key=lambda x: x["game_date_utc"]):
            if mlb_id not in rosters.get(g["game_pk"], set()):
                continue
            side = ("home" if players[mlb_id]["team"] == g["home_team"]
                    else "away")
            era = opp_era.get((g["game_pk"], side))
            break
        season_row = season_hit.get(mlb_id, {})
        if not season_row:
            no_season += 1
        form.append(fg.batter_form_row(
            mlb_id, window.get(mlb_id, {"hitting": [], "pitching": []}),
            season_row, era))
    for mlb_id in starters:
        form.append(fg.pitcher_form_row(
            mlb_id, window.get(mlb_id, {"hitting": [], "pitching": []}),
            season_pit.get(mlb_id, {})))
    print(f"  {len(form)} form rows ({no_season} batters without"
          " season stats — probability left blank)")

    print("fetching game weather...")
    weather = []
    forecasts: dict = {}
    for g in sorted(seed["games"], key=lambda x: x["game_date_utc"]):
        venue = wx.VENUES.get(g["home_team"])
        if venue is None:
            print(f"  no venue for {g['home_team']} — skipping")
            continue
        name, lat, lon, roof = venue
        first_pitch = wx.first_pitch_local(g["game_date_utc"])
        key = (g["home_team"], first_pitch.strftime("%Y-%m-%d"))
        if key not in forecasts:
            forecasts[key] = wx.fetch_forecast(
                lat, lon, first_pitch.strftime("%Y-%m-%d"))
        row = wx.parse_forecast(forecasts[key], first_pitch, roof)
        weather.append({"game_pk": g["game_pk"], "venue": name, **row})
    print(f"  {len(weather)} weather rows")

    seed["form"] = form
    seed["weather"] = weather
    with open(SEED, "w") as f:
        json.dump(seed, f, indent=1)
    print(f"wrote {SEED}")


if __name__ == "__main__":
    main()
