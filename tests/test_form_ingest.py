"""Poller refresh (Nov 25, 2026): form + weather land in the DB from
stubbed MLB/Open-Meteo responses, and a second run is a no-op."""
from conftest import build_week

from app import db as dbmod
from app import form_ingest as fi


def _schedule_payload():
    return {"dates": [{"date": "2026-09-24", "games": [{
        "gamePk": 999, "gameDate": "2026-09-24T23:10:00Z",
        "status": {"detailedState": "Final"},
        "teams": {
            "home": {"team": {"id": 111},
                     "probablePitcher": {"id": 201,
                                         "fullName": "Alec Gamboa"}},
            "away": {"team": {"id": 110},
                     "probablePitcher": {"id": 202,
                                         "fullName": "Clay Holmes"}}},
        "lineups": {"homePlayers": [], "awayPlayers": []},
    }]}]}


def _boxscore_payload():
    return {"gamePk": 999, "teams": {
        "home": {"team": {"abbreviation": "BOS"},
                 "battingOrder": [12],
                 "players": {
                     "ID12": {"person": {"id": 12,
                                          "fullName": "Adley Rutschman"},
                              "position": {"abbreviation": "DH"},
                              "stats": {"batting": {"atBats": 4, "hits": 2,
                                                    "homeRuns": 0, "rbi": 1}}},
                     "ID201": {"person": {"id": 201,
                                           "fullName": "Alec Gamboa"},
                               "position": {"abbreviation": "P"},
                               "stats": {"pitching": {"numberOfPitches": 40,
                                                      "strikeOuts": 1,
                                                      "gamesStarted": True}}}}},
        "away": {"team": {"abbreviation": "BAL"},
                 "battingOrder": [21],
                 "players": {
                     "ID21": {"person": {"id": 21,
                                          "fullName": "Pete Alonso"},
                              "position": {"abbreviation": "1B"},
                              "stats": {"batting": {"atBats": 4, "hits": 1,
                                                    "homeRuns": 0, "rbi": 0}}},
                     "ID202": {"person": {"id": 202,
                                           "fullName": "Clay Holmes"},
                               "position": {"abbreviation": "P"},
                               "stats": {"pitching": {"numberOfPitches": 78,
                                                      "strikeOuts": 3,
                                                      "gamesStarted": True}}}}}}}


def test_refresh_form_and_weather(con, monkeypatch):
    build_week(con, with_stats=False)
    calls = {"sched": 0, "box": 0, "people": 0, "wx": 0}

    import app.mlb as mlb_mod
    import app.weather as wx_mod

    def fake_schedule(date):
        calls["sched"] += 1
        return _schedule_payload()

    def fake_boxscore(game_pk):
        calls["box"] += 1
        return _boxscore_payload()

    def fake_people(ids, group, season, chunk=100):
        calls["people"] += 1
        people = []
        for mlb_id in ids:
            if group == "hitting":
                stat = {"avg": ".270", "atBats": 400, "hits": 108}
            else:
                stat = {"era": 3.50, "strikeOuts": 100,
                        "inningsPitched": "150.0", "gamesStarted": 25,
                        "gamesPlayed": 25}
            people.append({"id": mlb_id,
                           "stats": [{"splits": [{"stat": stat}]}]})
        return {"people": people}

    def fake_forecast(lat, lon, game_date, today=None):
        calls["wx"] += 1
        times, temp, prob, wind, deg, code = [], [], [], [], [], []
        for h in (13, 16, 17):
            times.append(f"2026-09-25T{h:02d}:00")
            temp.append(68.0)
            prob.append(10)
            wind.append(11.0)
            deg.append(315)
            code.append(2)
        return {"hourly": {"time": times, "temperature_2m": temp,
                           "precipitation_probability": prob,
                           "wind_speed_10m": wind,
                           "wind_direction_10m": deg,
                           "weathercode": code}}

    monkeypatch.setattr(mlb_mod, "fetch_schedule", fake_schedule)
    monkeypatch.setattr(mlb_mod, "fetch_boxscore", fake_boxscore)
    monkeypatch.setattr(mlb_mod, "fetch_people_stats", fake_people)
    monkeypatch.setattr(wx_mod, "fetch_forecast", fake_forecast)

    counts = fi.refresh_form_and_weather(con, 2026, 1, "2026-09-25")
    # 12 roster batters + 6 Friday starters.
    assert counts["form"] == 18
    assert counts["weather"] == 3
    form = dbmod.player_form_for_week(con, 2026, 1)
    assert len(form) == 18
    # Rutschman hit 2-for-4 in each of the 7 window games.
    assert form[12]["hits_last_7d"] == 14
    assert form[12]["ab_last_7d"] == 28
    assert form[12]["streak"] == 7
    # h = 0.6*0.270 + 0.4*0.500 = 0.362; ERA 3.50 -> factor 1.014;
    # P = 1 - (1 - 0.362*1.014)^4 = 84.0% -> 84.
    assert form[12]["hit_prob_pct"] == 84
    # Beck: K/9 = 6.0, IP/start = 6.0 -> lambda 4.0 -> P(5+) = 37%.
    # He never appears in the stubbed window, so no last start.
    assert form[203]["five_k_prob_pct"] == 37
    assert form[203]["last_start_pitches"] is None
    # Holmes started in the stubbed window games.
    assert form[202]["last_start_pitches"] == 78
    assert form[202]["last_start_k"] == 3
    weather = dbmod.game_weather_for_week(con, 2026, 1)
    assert weather[101]["venue"] == "Fenway Park"
    assert weather[101]["temp_f"] == 68
    assert weather[101]["wind_compass"] == "NW"
    assert weather[102]["venue"] == "Yankee Stadium"

    # Second run: everything already stored, no further fetches.
    before = dict(calls)
    counts2 = fi.refresh_form_and_weather(con, 2026, 1, "2026-09-25")
    assert counts2 == {"form": 0, "weather": 0}
    assert calls == before
