"""Poller-side pick-help refresh (Nov 25, 2026): form + weather.

Runs after the Friday lineup ingest (poller/poll.py). Idempotent:
players and games that already have rows for the week are skipped,
so a re-run costs nothing and a late-announced lineup still gets
its form on the next ingest.

Form window: the 7 days before Friday (prior Friday through
Thursday) — the "last 7 days" the pick page shows. Season stats
come from the batched /people endpoint (one call per ~100 players).
Weather comes from Open-Meteo at each game's first-pitch hour.

A failure here must never take down the ingest — the poller calls
refresh_form_and_weather inside try/except and reports, like
auto-pick. Partial results still land (form without weather, or
weather without form) because each row upserts on its own.
"""
from datetime import date, timedelta

from . import db as dbmod
from . import form_guide as fg
from . import mlb as mlb_mod
from . import weather as wx

WINDOW_DAYS = 7


def window_dates(friday_date: str) -> list:
    """The 7 dates before Friday, earliest first (prior Friday
    through Thursday)."""
    friday = date.fromisoformat(friday_date)
    return [(friday - timedelta(days=d)).isoformat()
            for d in range(WINDOW_DAYS, 0, -1)]


def fetch_window(friday_date: str) -> list:
    """[{date, datetime, boxscore}] for every game in the window.
    A game whose boxscore fails to load is skipped, not fatal —
    the player simply has a thinner window."""
    games = []
    for day in window_dates(friday_date):
        try:
            sched = mlb_mod.parse_schedule(mlb_mod.fetch_schedule(day))
        except mlb_mod.MlbError:
            continue
        for g in sched:
            try:
                box = mlb_mod.parse_boxscore(
                    mlb_mod.fetch_boxscore(g["game_pk"]))
            except mlb_mod.MlbError:
                continue
            games.append({"date": day,
                          "datetime": g["game_date_utc"],
                          "boxscore": box})
    return games


def friday_roster(con, season: int, week: int) -> tuple:
    """(batter mlb_ids, starter mlb_ids) for the week's games, from
    the rosters and lineups already ingested."""
    batters, starters = set(), set()
    for g in dbmod.games_for_week(con, season, week):
        for p in dbmod.players_for_game(con, g["game_pk"]):
            if p["is_pitcher"]:
                continue
            batters.add(p["mlb_id"])
        starters |= set(dbmod.lineup_for_game(con, g["game_pk"])["starters"])
        starters |= {g.get("home_starter_mlb_id"),
                     g.get("away_starter_mlb_id")} - {None}
    return batters, starters


def refresh_form_and_weather(con, season: int, week: int,
                             friday_date: str) -> dict:
    """Compute and store form rows (missing players only) and weather
    rows (missing games only) for the week. Returns
    {"form": n_rows, "weather": n_rows}."""
    games = dbmod.games_for_week(con, season, week)
    if not games:
        return {"form": 0, "weather": 0}
    existing_form = dbmod.player_form_for_week(con, season, week)
    existing_weather = dbmod.game_weather_for_week(con, season, week)
    formed = 0

    batters, starters = friday_roster(con, season, week)
    missing_batters = sorted(batters - set(existing_form))
    missing_starters = sorted(starters - set(existing_form))
    if missing_batters or missing_starters:
        window = fg.aggregate_window(fetch_window(friday_date))
        season_hit = mlb_mod.parse_people_stats(
            mlb_mod.fetch_people_stats(missing_batters, "hitting", season),
            "hitting") if missing_batters else {}
        # Opposing-starter ERAs come from every Friday starter's
        # season pitching stats — a batter's probability needs the
        # ERA of the pitcher he faces, whether or not that starter
        # already has a form row.
        season_pit = mlb_mod.parse_people_stats(
            mlb_mod.fetch_people_stats(sorted(starters), "pitching", season),
            "pitching") if starters else {}
        opp_era = {}
        for g in games:
            for side, opp_col in (("home", "away_starter_mlb_id"),
                                  ("away", "home_starter_mlb_id")):
                opp_id = g.get(opp_col)
                era = (season_pit.get(opp_id) or {}).get("era")
                opp_era[(g["game_pk"], side)] = era
        # A batter's opposing starter is the starter of the other
        # team in the game he plays in — resolve it once per game.
        roster_by_game = {}
        team_by_player = {}
        for g in games:
            ids = set()
            for p in dbmod.players_for_game(con, g["game_pk"]):
                ids.add(p["mlb_id"])
                team_by_player[p["mlb_id"]] = p["team"]
            roster_by_game[g["game_pk"]] = ids
        for mlb_id in missing_batters:
            era = None
            for g in games:
                if mlb_id not in roster_by_game[g["game_pk"]]:
                    continue
                # Doubleheader players face two starters; the first
                # game wins for the single per-week probability.
                side = ("home" if team_by_player.get(mlb_id) == g["home_team"]
                        else "away")
                era = opp_era.get((g["game_pk"], side))
                break
            row = fg.batter_form_row(
                mlb_id, window.get(mlb_id, {"hitting": [], "pitching": []}),
                season_hit.get(mlb_id, {}), era)
            dbmod.upsert_player_form(con, season, week, row)
            formed += 1
        for mlb_id in missing_starters:
            row = fg.pitcher_form_row(
                mlb_id, window.get(mlb_id, {"hitting": [], "pitching": []}),
                season_pit.get(mlb_id, {}))
            dbmod.upsert_player_form(con, season, week, row)
            formed += 1
        con.commit()

    weathered = 0
    forecasts: dict = {}
    for g in games:
        if g["game_pk"] in existing_weather:
            continue
        venue = wx.VENUES.get(g["home_team"])
        if venue is None:
            continue
        name, lat, lon, roof = venue
        first_pitch = wx.first_pitch_local(g["game_date_utc"])
        key = (g["home_team"], first_pitch.strftime("%Y-%m-%d"))
        if key not in forecasts:
            forecasts[key] = wx.fetch_forecast(
                lat, lon, first_pitch.strftime("%Y-%m-%d"))
        row = wx.parse_forecast(forecasts[key], first_pitch, roof)
        dbmod.upsert_game_weather(con, g["game_pk"],
                                  {"venue": name, **row})
        weathered += 1
    con.commit()
    return {"form": formed, "weather": weathered}
