"""Playground Friday rotation (Nov 27, 2026): the Tuesday draw, the
fresh-week swap, idempotency, and the poller dispatch.

All network is stubbed — no test touches the real MLB API or
Open-Meteo. The stubs return small but realistic payloads: a
schedule with N Final games, one generic boxscore per game, season
stats for every requested player, and an hourly forecast.
"""
from datetime import date

import pytest
from conftest import build_week, make_member

from app import db as dbmod
from app import mlb as mlb_mod
from app import rotation as rotation_mod
from app import weather as wx_mod
from poller import poll as poll_mod

TUE_DEC_1 = date(2026, 12, 1)
TUE_DEC_8 = date(2026, 12, 8)
TUE_DEC_15 = date(2026, 12, 15)
WED_DEC_2 = date(2026, 12, 2)
FRI_DEC_4 = date(2026, 12, 4)
FRIDAY_JUL_10 = "2026-07-10"


def _sched(friday_date: str, n: int, first_pk: int = 1000) -> dict:
    """Schedule payload with n Final games (BOS vs CHC, starters
    Gamboa/Holmes) — enough for parse_schedule to return n games."""
    games = []
    for i in range(n):
        games.append({
            "gamePk": first_pk + i,
            "gameDate": f"{friday_date}T{17 + (i % 5):02d}:05:00Z",
            "status": {"detailedState": "Final"},
            "teams": {
                "home": {"team": {"id": 111},
                         "probablePitcher": {"id": 201,
                                             "fullName": "Alec Gamboa"}},
                "away": {"team": {"id": 112},
                         "probablePitcher": {"id": 202,
                                             "fullName": "Clay Holmes"}}},
            "lineups": {"homePlayers": [], "awayPlayers": []},
        })
    return {"dates": [{"date": friday_date, "games": games}]}


def _boxscore(game_pk: int) -> dict:
    """One generic boxscore: two batters + one starter per side."""
    def _player(mlb_id, name, pos, stats):
        return {"person": {"id": mlb_id, "fullName": name},
                "position": {"abbreviation": pos}, "stats": stats}

    return {"gamePk": game_pk, "teams": {
        "home": {"team": {"abbreviation": "BOS"},
                 "battingOrder": [11, 12],
                 "players": {
                     "ID11": _player(11, "Roman Anthony", "LF",
                                     {"batting": {"atBats": 4, "hits": 2,
                                                 "homeRuns": 0, "rbi": 1}}),
                     "ID12": _player(12, "Adley Rutschman", "DH",
                                     {"batting": {"atBats": 4, "hits": 2,
                                                 "homeRuns": 0, "rbi": 0}}),
                     "ID201": _player(201, "Alec Gamboa", "P",
                                      {"pitching": {"numberOfPitches": 40,
                                                   "strikeOuts": 1,
                                                   "gamesStarted": True}}),
                 }},
        "away": {"team": {"abbreviation": "CHC"},
                 "battingOrder": [14, 15],
                 "players": {
                     "ID14": _player(14, "Michael Busch", "1B",
                                     {"batting": {"atBats": 4, "hits": 2,
                                                 "homeRuns": 0, "rbi": 0}}),
                     "ID15": _player(15, "Matt Shaw", "3B",
                                     {"batting": {"atBats": 4, "hits": 1,
                                                 "homeRuns": 0, "rbi": 0}}),
                     "ID202": _player(202, "Clay Holmes", "P",
                                      {"pitching": {"numberOfPitches": 78,
                                                   "strikeOuts": 3,
                                                   "gamesStarted": True}}),
                 }},
    }}


def _people(person_ids, group, season):
    """Season stats for every requested player."""
    people = []
    for mlb_id in person_ids:
        if group == "hitting":
            stat = {"avg": ".280", "atBats": 100, "hits": 28}
        else:
            stat = {"era": 3.50, "strikeOuts": 120,
                    "inningsPitched": "120.0", "gamesStarted": 20,
                    "gamesPlayed": 20, "earnedRuns": 47}
        people.append({"id": mlb_id,
                       "stats": [{"splits": [{"stat": stat}]}]})
    return {"people": people}


def _forecast(lat, lon, game_date, today=None):
    """Hourly forecast for one date: 68F, 10% precip, 12 mph from S."""
    hours = [f"{game_date}T{h:02d}:00" for h in range(24)]
    return {"hourly": {
        "time": hours,
        "temperature_2m": [68] * 24,
        "precipitation_probability": [10] * 24,
        "wind_speed_10m": [12] * 24,
        "wind_direction_10m": [180] * 24,
        "weathercode": [2] * 24,
    }}


def _schedule_stub(counts=None, season_default=12):
    """fetch_schedule stub: per-date game counts for season Fridays
    (counts overrides), 1 game for any other date (window days)."""
    counts = counts or {}
    fridays = {d.isoformat() for d in rotation_mod.season_fridays()}

    def fake(friday_date, hydrate="lineups,probablePitcher"):
        n = counts.get(friday_date,
                       season_default if friday_date in fridays else 1)
        return _sched(friday_date, n)

    return fake


@pytest.fixture()
def network(monkeypatch):
    """Stub every network call the rotation touches."""
    monkeypatch.setattr(mlb_mod, "fetch_schedule",
                        _schedule_stub({"2026-03-27": 8}))
    monkeypatch.setattr(mlb_mod, "fetch_boxscore",
                        lambda game_pk: _boxscore(game_pk))
    monkeypatch.setattr(mlb_mod, "fetch_people_stats", _people)
    monkeypatch.setattr(wx_mod, "fetch_forecast", _forecast)
    return monkeypatch


def _playground_with_picks(con):
    """Sep 25 week (build_week) + a member with a full lineup, a
    head-to-head grant, an auto-pick decline and a form row — the
    server state a rotation must wipe."""
    build_week(con)
    member = make_member(con, "Adam Cassano")
    for slot, mlb_id, game_pk in (
            ("b1", 11, 101), ("b2", 12, 101), ("b3", 14, 101),
            ("b4", 21, 102), ("b5", 24, 102), ("p1", 201, 101)):
        dbmod.save_pick(con, member["id"], 2026, 1, slot,
                        "pitcher" if slot == "p1" else "batter",
                        mlb_id, game_pk)
    con.execute(
        "INSERT INTO head_to_head_grants (season, week_earned, member_id,"
        " use_week, used) VALUES (2026, 1, ?, 2, 0)", (member["id"],))
    dbmod.record_auto_decline(con, member["id"], 2026, 1, "b1")
    dbmod.upsert_player_form(con, 2026, 1,
                             {"mlb_id": 11, "is_pitcher": False,
                              "hits_last_7d": 6, "ab_last_7d": 20,
                              "streak": 1, "season_avg": 0.28,
                              "hit_prob_pct": 60})
    con.commit()
    return member


# ---------------------------------------------------------------- pool

def test_pool_build_keeps_only_full_slates(con, network):
    build_week(con)
    rows = rotation_mod.ensure_pool(con)
    dates = [r["friday_date"] for r in rows]
    assert "2026-03-27" not in dates  # 8 games — short slate
    assert len(rows) == 26  # 27 Fridays minus Mar 27
    assert all(r["games"] >= 10 for r in rows)
    sep25 = [r for r in rows if r["friday_date"] == "2026-09-25"][0]
    assert sep25["drawn_on"] == "2026-09-25"  # already used
    # Second build is a no-op — same rows, no duplicates.
    assert len(rotation_mod.ensure_pool(con)) == 26


def test_draw_is_deterministic_and_never_repeats(con):
    for d in ("2026-04-03", "2026-04-10", "2026-04-17", "2026-09-25"):
        con.execute(
            "INSERT INTO playground_fridays (friday_date, games, drawn_on)"
            " VALUES (?, 15, ?)",
            (d, "2026-09-25" if d == "2026-09-25" else None))
    con.commit()
    unused = ["2026-04-03", "2026-04-10", "2026-04-17"]
    first = rotation_mod.draw_for_tuesday(TUE_DEC_1, unused)
    assert rotation_mod.draw_for_tuesday(TUE_DEC_1, unused) == first
    assert first != "2026-09-25"
    remaining = [d for d in unused if d != first]
    second = rotation_mod.draw_for_tuesday(TUE_DEC_8, remaining)
    assert second != first
    assert rotation_mod.draw_for_tuesday(TUE_DEC_8, remaining) == second


def test_sep_25_never_redrawn(con, network):
    build_week(con)
    rotation_mod.ensure_pool(con)
    rows = rotation_mod._pool_rows(con)
    unused = [r["friday_date"] for r in rows if r["drawn_on"] is None]
    assert "2026-09-25" not in unused
    for tuesday in (date(2026, 12, 1), date(2026, 12, 8),
                    date(2026, 12, 15), date(2026, 12, 22),
                    date(2026, 12, 29), date(2027, 1, 5)):
        assert rotation_mod.draw_for_tuesday(tuesday, unused) != "2026-09-25"


# ---------------------------------------------------------------- rotation

def test_rotation_replaces_week_and_clears_picks(con, network):
    member = _playground_with_picks(con)
    result = rotation_mod.rotate(con, TUE_DEC_1)
    assert result["rotated"] is True
    assert result["friday"] != "2026-09-25"
    assert result["games"] == 12
    assert result["form"] > 0
    assert result["weather"] == 12
    assert result["refresh_error"] is None
    week = rotation_mod.playground_week(con)
    assert week["friday_date"] == result["friday"]
    assert week["label"] == rotation_mod.playground_label(
        date.fromisoformat(result["friday"]))
    assert {g["game_pk"] for g in dbmod.games_for_week(con, 2026, 1)} == set(
        range(1000, 1012))
    assert dbmod.picks_for_member_week(con, member["id"], 2026, 1) == []
    assert con.execute(
        "SELECT COUNT(*) FROM head_to_head_grants").fetchone()[0] == 0
    assert con.execute(
        "SELECT COUNT(*) FROM auto_pick_declines").fetchone()[0] == 0
    # The old Sep 25 form row is gone; the new Friday has form rows.
    assert dbmod.player_form_for_week(con, 2026, 1)
    assert len(dbmod.game_weather_for_week(con, 2026, 1)) == 12
    drawn = con.execute(
        "SELECT drawn_on FROM playground_fridays WHERE friday_date = ?",
        (result["friday"],)).fetchone()
    assert drawn["drawn_on"] == "2026-12-01"


def test_rotation_idempotent_within_week(con, network):
    _playground_with_picks(con)
    first = rotation_mod.rotate(con, TUE_DEC_1)
    assert first["rotated"] is True
    games_after_first = dbmod.games_for_week(con, 2026, 1)
    second = rotation_mod.rotate(con, TUE_DEC_1)
    assert second["rotated"] is False
    assert second["reason"] == "already rotated this week"
    assert second["friday"] == first["friday"]
    assert dbmod.games_for_week(con, 2026, 1) == games_after_first
    assert con.execute(
        "SELECT COUNT(*) FROM playground_fridays"
        " WHERE drawn_on = '2026-12-01'").fetchone()[0] == 1


def test_rotation_skips_non_tuesday(con, network):
    _playground_with_picks(con)
    result = rotation_mod.rotate(con, WED_DEC_2)
    assert result["rotated"] is False
    assert result["reason"] == "not Tuesday"
    assert rotation_mod.playground_week(con)["friday_date"] == "2026-09-25"
    assert rotation_mod._pool_rows(con) == []  # pool never built
    assert dbmod.games_for_week(con, 2026, 1)


def test_failed_fetch_keeps_current_week(con, network, monkeypatch):
    member = _playground_with_picks(con)
    rotation_mod.ensure_pool(con)  # pool cached before the outage
    monkeypatch.setattr(mlb_mod, "fetch_schedule",
                        lambda *a, **k: (_ for _ in ()).throw(
                            mlb_mod.MlbError("api down")))
    result = rotation_mod.rotate(con, TUE_DEC_1)
    assert result["rotated"] is False
    assert result["reason"].startswith("fetch failed")
    week = rotation_mod.playground_week(con)
    assert week["friday_date"] == "2026-09-25"
    assert {g["game_pk"] for g in dbmod.games_for_week(con, 2026, 1)} == {
        101, 102, 103}
    assert len(dbmod.picks_for_member_week(con, member["id"], 2026, 1)) == 6
    assert con.execute(
        "SELECT COUNT(*) FROM playground_fridays"
        " WHERE drawn_on = '2026-12-01'").fetchone()[0] == 0


def test_pool_exhaustion_resets(con, network):
    build_week(con)
    for d, drawn in (("2026-04-03", None), ("2026-04-10", None),
                     ("2026-09-25", "2026-09-25")):
        con.execute(
            "INSERT INTO playground_fridays (friday_date, games, drawn_on)"
            " VALUES (?, 15, ?)", (d, drawn))
    con.commit()
    first = rotation_mod.rotate(con, TUE_DEC_1)
    second = rotation_mod.rotate(con, TUE_DEC_8)
    assert first["rotated"] and second["rotated"]
    assert first["friday"] != second["friday"]
    third = rotation_mod.rotate(con, TUE_DEC_15)
    assert third["rotated"] is True
    # Pool reset: the third draw goes back to the first Friday (the
    # second is the current week, so it is excluded).
    assert third["friday"] == first["friday"]
    assert third["friday"] != second["friday"]


# ---------------------------------------------------------------- poller dispatch

def test_poller_noop_on_wednesday(con, network, capsys):
    _playground_with_picks(con)
    poll_mod.main(argv=[], today=WED_DEC_2)
    assert rotation_mod.playground_week(con)["friday_date"] == "2026-09-25"
    assert rotation_mod._pool_rows(con) == []
    assert "nothing to do" in capsys.readouterr().out


def test_poller_rotates_on_tuesday(con, network):
    _playground_with_picks(con)
    poll_mod.main(argv=[], today=TUE_DEC_1)
    week = rotation_mod.playground_week(con)
    assert week["friday_date"] != "2026-09-25"
    assert len(dbmod.games_for_week(con, 2026, 1)) == 12


def test_poller_rotate_flag(con, network):
    _playground_with_picks(con)
    poll_mod.main(argv=["--rotate", "--date", "2026-12-01"])
    assert rotation_mod.playground_week(con)["friday_date"] != "2026-09-25"
    poll_mod.main(argv=["--rotate", "--date", "2026-12-02"])
    # Wednesday flag run is a no-op — the Dec 1 week stands.
    assert rotation_mod.playground_week(con)["friday_date"] != "2026-09-25"


def test_poller_skips_empty_friday(con, network, monkeypatch):
    build_week(con)
    monkeypatch.setattr(mlb_mod, "fetch_schedule",
                        lambda *a, **k: {"dates": []})
    poll_mod.main(argv=[], today=FRI_DEC_4)
    assert dbmod.all_weeks(con, 2026) == [
        {"season": 2026, "week": 1, "friday_date": "2026-09-25",
         "label": "Week 1 — 2026-09-25", "playground": 1}]


def test_poller_explicit_date_still_ingests(con, network):
    build_week(con)
    con.commit()  # poll.main opens its own connection — release the lock
    poll_mod.main(argv=["--date", FRIDAY_JUL_10, "--week", "2"])
    games = dbmod.games_for_week(con, 2026, 2)
    assert len(games) == 12
    assert all(g["friday_date"] == FRIDAY_JUL_10 for g in games)
    # The playground week is untouched by an explicit ingest.
    assert rotation_mod.playground_week(con)["friday_date"] == "2026-09-25"
