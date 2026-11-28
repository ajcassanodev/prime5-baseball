"""Shared fixtures: one throwaway SQLite DB for the whole session
(DB path is fixed at import time), wiped clean before every test.
"""
import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="prime5bb-test-")
os.environ["PRIME5BB_DB"] = os.path.join(_TMP, "test.db")

from app import db as dbmod  # noqa: E402
from app import mlb as mlb_mod  # noqa: E402


@pytest.fixture(autouse=True)
def no_mlb_network(monkeypatch):
    """No test touches the real MLB API — the dashboard's next-lock
    lookup falls back to plain 'Next Friday'. (Tests that need a
    schedule stub it themselves, as test_dashboard.py does.)"""
    def _boom(date):
        raise mlb_mod.MlbError("no network in tests")
    monkeypatch.setattr(mlb_mod, "fetch_schedule", _boom)


_TABLES = ["head_to_head_grants", "auto_pick_declines", "picks",
           "player_game_stats", "player_form", "game_weather",
           "lineups", "rosters", "games", "players",
           "weeks", "settings", "members", "playground_fridays"]


@pytest.fixture()
def con():
    dbmod.init_db()
    c = dbmod.connect()
    for t in _TABLES:
        c.execute(f"DELETE FROM {t}")
    c.commit()
    yield c
    c.close()


def make_member(con, name="Ada", email=None, is_test=False):
    return dbmod.create_member(con, name, email=email, is_test=is_test)


def build_week(con, season=2026, week=1, friday_date="2026-09-25",
               playground=True, g1_time="2026-09-25T17:05:00Z",
               g2_time="2026-09-25T20:05:00Z",
               g3_time="2026-09-25T21:35:00Z", with_stats=True):
    """Three-game synthetic Friday mirroring the real Sep 25, 2026 shape.

    Game 101: CHC @ BOS, starters Holmes (CHC) vs Gamboa (BOS).
    Game 102: BAL @ NYY, starters Rogers (BAL) vs Beck (NYY).
    Game 103: CHC @ BOS, game 2 of the doubleheader, starters
    Peterson (CHC) vs Bello (BOS). Michael Busch plays both games
    of the doubleheader (101 and 103), 2 hits in each.
    Returns {"games": {101: ..., 102: ..., 103: ...},
    "batters": {name: mlb_id}, "pitchers": {name: mlb_id},
    "batter_game": {name: game_pk}} (Busch defaults to game 101).
    """
    dbmod.ensure_week(con, season, week, friday_date,
                      label=f"Week {week} — {friday_date}", playground=playground)
    players = [
        # batters, game 101 (BOS)
        (11, "Roman Anthony", "BOS", "LF", 0, 3),
        (12, "Adley Rutschman", "BOS", "DH", 0, 2),
        (13, "Willson Contreras", "BOS", "1B", 0, None),  # DNP
        # batters, game 101 (CHC)
        (14, "Michael Busch", "CHC", "1B", 0, 2),
        (15, "Matt Shaw", "CHC", "3B", 0, 2),
        (16, "Seiya Suzuki", "CHC", "RF", 0, 2),
        # batters, game 102 (BAL)
        (21, "Pete Alonso", "BAL", "1B", 0, 3),
        (22, "Coby Mayo", "BAL", "3B", 0, 3),
        (23, "Heston Kjerstad", "BAL", "LF", 0, None),  # DNP
        # batters, game 102 (NYY)
        (24, "Heliot Ramos", "NYY", "LF", 0, 2),
        (25, "Amed Rosario", "NYY", "RF", 0, 2),
        (26, "Anthony Volpe", "NYY", "3B", 0, 2),
        # pitchers
        (201, "Alec Gamboa", "BOS", "P", 1, (40, 1)),
        (202, "Clay Holmes", "CHC", "P", 1, (78, 3)),
        (203, "Brendan Beck", "NYY", "P", 1, (97, 8)),
        (204, "Trevor Rogers", "BAL", "P", 1, (55, 4)),
        (205, "Relief Arm", "BOS", "P", 1, (25, 2)),  # reliever, not a starter
        # game 103 starters (doubleheader game 2)
        (206, "David Peterson", "CHC", "P", 1, (91, 6)),
        (207, "Brayan Bello", "BOS", "P", 1, (58, 2)),
    ]
    for mlb_id, name, team, pos, is_p, _stat in players:
        dbmod.upsert_player(con, {"mlb_id": mlb_id, "name": name, "team": team,
                                  "position": pos, "is_pitcher": bool(is_p)})
    games = {
        101: {"game_pk": 101, "season": season, "week": week,
              "friday_date": friday_date, "game_date_utc": g1_time,
              "status": "Final", "home_team": "BOS", "away_team": "CHC",
              "home_starter_mlb_id": 201, "away_starter_mlb_id": 202},
        102: {"game_pk": 102, "season": season, "week": week,
              "friday_date": friday_date, "game_date_utc": g2_time,
              "status": "Final", "home_team": "NYY", "away_team": "BAL",
              "home_starter_mlb_id": 203, "away_starter_mlb_id": 204},
        103: {"game_pk": 103, "season": season, "week": week,
              "friday_date": friday_date, "game_date_utc": g3_time,
              "status": "Final", "home_team": "BOS", "away_team": "CHC",
              "home_starter_mlb_id": 207, "away_starter_mlb_id": 206},
    }
    for g in games.values():
        dbmod.upsert_game(con, g)
    # Rosters + lineups + stats.
    roster = {101: [11, 12, 13, 14, 15, 16, 201, 202, 205],
              102: [21, 22, 23, 24, 25, 26, 203, 204],
              103: [14, 206, 207]}
    for gpk, ids in roster.items():
        for mlb_id in ids:
            dbmod.add_roster(con, gpk, mlb_id)
    orders = {101: [(11, 1), (12, 2), (14, 1), (15, 2), (16, 3)],
              102: [(21, 1), (22, 2), (24, 1), (25, 2), (26, 3)],
              103: [(14, 1)]}
    for gpk, rows in orders.items():
        for mlb_id, order in rows:
            dbmod.set_lineup(con, gpk, "batter", mlb_id, order)
    for gpk, starter in ((101, 201), (101, 202), (102, 203), (102, 204),
                         (103, 206), (103, 207)):
        dbmod.set_lineup(con, gpk, "pitcher", starter, None)
    if with_stats:
        stats = [
            (101, 11, 3, 0), (101, 12, 2, 0), (101, 14, 2, 0),
            (101, 15, 2, 0), (101, 16, 2, 0),
            (102, 21, 3, 0), (102, 22, 3, 0),
            (102, 24, 2, 0), (102, 25, 2, 0), (102, 26, 2, 0),
            (103, 14, 2, 0),
        ]
        for gpk, mlb_id, hits, _hr in stats:
            dbmod.upsert_stats(con, gpk, {"mlb_id": mlb_id, "at_bats": 4,
                                           "hits": hits, "hr": 0, "rbi": 0,
                                           "pitches": 0, "strikeouts": 0})
        for gpk, mlb_id, pitches, k in ((101, 201, 40, 1), (101, 202, 78, 3),
                                        (102, 203, 97, 8), (102, 204, 55, 4),
                                        (101, 205, 25, 2),
                                        (103, 206, 91, 6), (103, 207, 58, 2)):
            dbmod.upsert_stats(con, gpk, {"mlb_id": mlb_id, "at_bats": 0,
                                           "hits": 0, "hr": 0, "rbi": 0,
                                           "pitches": pitches, "strikeouts": k})
    batters = {name: mlb_id for mlb_id, name, _t, _p, is_p, _s in players
               if not is_p}
    pitchers = {name: mlb_id for mlb_id, name, _t, _p, is_p, _s in players
                if is_p}
    # Game each batter belongs to (Busch plays both games of the
    # doubleheader; his default here is game 101).
    batter_game = {
        "Roman Anthony": 101, "Adley Rutschman": 101,
        "Willson Contreras": 101, "Michael Busch": 101,
        "Matt Shaw": 101, "Seiya Suzuki": 101,
        "Pete Alonso": 102, "Coby Mayo": 102,
        "Heston Kjerstad": 102, "Heliot Ramos": 102,
        "Amed Rosario": 102, "Anthony Volpe": 102,
    }
    return {"games": games, "batters": batters, "pitchers": pitchers,
            "batter_game": batter_game}


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)
