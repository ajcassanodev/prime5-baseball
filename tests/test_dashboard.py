"""Baseball dashboard: rank card, donuts, my picks + streak, top
performers, division standings, how scoring works.

The real-data tests load the actual Sep 25, 2026 playground seed
(data/seed_friday_2026-09-25.json) and Demo Tester's real picks, so
the numbers asserted here are the real Friday's numbers, not
synthetic ones.
"""
import json
import os

import pytest

from app import db as dbmod
from app import mlb as mlb_mod
from conftest import build_week, make_member

SEED_PATH = os.path.join(os.path.dirname(__file__), "..", "data",
                         "seed_friday_2026-09-25.json")

# Demo Tester's real picks (seeded playground week, 2026 week 1).
DEMO_PICKS = [
    ("b1", "batter", 701350, 824703),  # Roman Anthony — 3 H
    ("b2", "batter", 624413, 823491),  # Pete Alonso — 3 H
    ("b3", "batter", 668939, 824703),  # Adley Rutschman — 2 H
    ("b4", "batter", 673548, 824703),  # Seiya Suzuki — 0 H (resets streak)
    ("b5", "batter", 657136, 824703),  # Connor Wong — 1 H
    ("p1", "pitcher", 605280, 824703),  # Clay Holmes — 78 pitches, 3 K
]

# A 22.00-point set (Sánchez 11.00 + 3 + 3 + 2 + 2 + 1) for a
# division leader.
PAGS_PICKS = [
    ("b1", "batter", 701350, 824703),
    ("b2", "batter", 624413, 823491),
    ("b3", "batter", 668939, 824703),
    ("b4", "batter", 683737, 824703),  # Michael Busch — 2 H
    ("b5", "batter", 657136, 824703),
    ("p1", "pitcher", 650911, 823409),  # Cristopher Sánchez — 100 pitches, 10 K
]


def load_real_seed(con):
    """Load the real Sep 25, 2026 playground seed into the test DB."""
    with open(SEED_PATH) as f:
        seed = json.load(f)
    dbmod.ensure_week(con, seed["season"], seed["week"], seed["friday_date"],
                      label=seed.get("label"),
                      playground=seed.get("playground", False))
    for game in seed["games"]:
        dbmod.upsert_game(con, {**game, "season": seed["season"],
                                 "week": seed["week"],
                                 "friday_date": seed["friday_date"]})
    for player in seed["players"]:
        dbmod.upsert_player(con, player)
    for row in seed.get("rosters", []):
        dbmod.add_roster(con, row["game_pk"], row["mlb_id"])
    for row in seed["lineups"]:
        dbmod.set_lineup(con, row["game_pk"], row["role"], row["mlb_id"],
                         row.get("batting_order"))
    for stat in seed["stats"]:
        dbmod.upsert_stats(con, stat["game_pk"], stat)
    return seed


def _save_picks(con, member_id, picks):
    for slot, kind, mlb_id, game_pk in picks:
        dbmod.save_pick(con, member_id, 2026, 1, slot, kind, mlb_id, game_pk)


def make_demo(con):
    m = dbmod.create_member(con, "Demo Tester", email="demo@example.com",
                            is_test=True)
    _save_picks(con, m["id"], DEMO_PICKS)
    return m


def make_pags(con):
    m = dbmod.create_member(con, "Joe Pagliaro", email="joe@example.com",
                            screen_name="Pags")
    _save_picks(con, m["id"], PAGS_PICKS)
    return m


@pytest.fixture(autouse=True)
def no_mlb_network(monkeypatch):
    """Dashboard loads must not touch the real MLB API in tests —
    the next-lock lookup falls back to plain 'Next Friday'."""
    def _boom(date):
        raise mlb_mod.MlbError("no network in tests")
    monkeypatch.setattr(mlb_mod, "fetch_schedule", _boom)


def _dashboard(client, member):
    client.get(f"/submit/{member['pick_token']}")
    r = client.get("/dashboard")
    assert r.status_code == 200
    return r.text


# ---------------------------------------------------------------- divisions

def test_division_defaults_to_red_hots(con):
    m = make_member(con, "Ada")
    assert m["division"] == "red"
    assert dbmod.division_display(m["division"]) == "Red Hots"
    assert dbmod.division_display(None) == "Red Hots"
    assert dbmod.division_display("white") == "White Hots"
    assert dbmod.division_display("bogus") == "Red Hots"


def test_create_member_division(con):
    m = dbmod.create_member(con, "Whitaker White", email="w@x.com",
                            division="white")
    assert dbmod.member_by_id(con, m["id"])["division"] == "white"
    with pytest.raises(ValueError, match="unknown division"):
        dbmod.create_member(con, "Bad Div", division="purple")


def test_standings_rows_carry_division(con):
    w = build_week(con)
    m = dbmod.create_member(con, "Whitaker White", email="w@x.com",
                            division="white")
    rows = dbmod.standings(con, 2026, 1)
    by_id = {r["member_id"]: r for r in rows}
    assert by_id[m["id"]]["division"] == "white"


# ---------------------------------------------------------------- rank card

def test_rank_card_gap_and_division(con, client):
    load_real_seed(con)
    demo = make_demo(con)
    make_pags(con)
    out = _dashboard(client, demo)
    assert '<div class="dash-place">#2</div>' in out
    assert "12.78" in out  # week total
    assert "9.22 behind Pags" in out
    assert "2nd in Red Hots" in out
    assert "Next Friday" in out  # schedule lookup failed in tests


def test_rank_card_leading(con, client):
    w = build_week(con)
    m = make_member(con, "Ada")
    out = _dashboard(client, m)
    assert '<div class="dash-place">#1</div>' in out
    assert "Leading the league" in out
    assert "1st in Red Hots" in out


def test_next_lock_from_schedule(monkeypatch, con, client):
    w = build_week(con)
    m = make_member(con, "Ada")

    def _fake(date):
        return {"dates": [{"games": [
            {"gamePk": 1, "gameDate": "2026-10-02T20:05:00Z",
             "status": {"detailedState": "Scheduled"},
             "teams": {"home": {"team": {"id": 111}},
                       "away": {"team": {"id": 112}}}},
            {"gamePk": 2, "gameDate": "2026-10-02T17:05:00Z",
             "status": {"detailedState": "Scheduled"},
             "teams": {"home": {"team": {"id": 111}},
                       "away": {"team": {"id": 112}}}},
        ]}]}
    monkeypatch.setattr(mlb_mod, "fetch_schedule", _fake)
    out = _dashboard(client, m)
    assert "Next Friday · 1:05 PM ET" in out


# ---------------------------------------------------------------- donuts

def test_week_donut_data_demo_tester(con, client):
    load_real_seed(con)
    demo = make_demo(con)
    out = _dashboard(client, demo)
    # Biggest slice first; zero-point Suzuki is omitted.
    assert ('donut(\'dash-d2\', \'dash-l2\', [{"name": "Clay Holmes",'
            ' "points": 3.78}, {"name": "Roman Anthony", "points": 3.0},'
            ' {"name": "Pete Alonso", "points": 3.0},'
            ' {"name": "Adley Rutschman", "points": 2.0},'
            ' {"name": "Connor Wong", "points": 1.0}])') in out
    assert ('donut(\'dash-d1\', \'dash-l1\', [{"name": "Playground week",'
            ' "points": 12.78}])') in out
    assert ("Season starts Mar 26, 2027 — the playground is one week."
            in out)


# ---------------------------------------------------------------- hit streak

def test_hit_streak_demo_tester(con):
    # Anthony hit, Alonso hit, Rutschman hit, Suzuki hitless (reset),
    # Wong hit -> 1.
    load_real_seed(con)
    demo = make_demo(con)
    assert dbmod.hit_streak(con, demo["id"], 2026, 1) == 1


def test_hit_streak_dnp_resets(con):
    w = build_week(con)
    m = make_member(con, "Ada")
    # Anthony 3H, Contreras DNP (no stat row in a Final game), Rutschman 2H.
    dbmod.save_pick(con, m["id"], 2026, 1, "b1", "batter",
                    w["batters"]["Roman Anthony"], 101)
    dbmod.save_pick(con, m["id"], 2026, 1, "b2", "batter",
                    w["batters"]["Willson Contreras"], 101)
    dbmod.save_pick(con, m["id"], 2026, 1, "b3", "batter",
                    w["batters"]["Adley Rutschman"], 101)
    assert dbmod.hit_streak(con, m["id"], 2026, 1) == 1


def test_hit_streak_skips_unplayed_week_and_accumulates(con):
    w = build_week(con)
    m = make_member(con, "Ada")
    dbmod.save_pick(con, m["id"], 2026, 1, "b1", "batter",
                    w["batters"]["Roman Anthony"], 101)
    assert dbmod.hit_streak(con, m["id"], 2026, 1) == 1
    # Week 2, game not played yet: the pick can't move the streak.
    dbmod.ensure_week(con, 2026, 2, "2026-10-02",
                      label="Week 2 — 2026-10-02", playground=True)
    dbmod.upsert_game(con, {"game_pk": 201, "season": 2026, "week": 2,
                            "friday_date": "2026-10-02",
                            "game_date_utc": "2026-10-02T17:05:00Z",
                            "status": "Scheduled", "home_team": "BOS",
                            "away_team": "CHC"})
    dbmod.save_pick(con, m["id"], 2026, 2, "b1", "batter",
                    w["batters"]["Roman Anthony"], 201)
    assert dbmod.hit_streak(con, m["id"], 2026, 2) == 1
    # Week 2 goes Final and Anthony hits again -> streak 2.
    con.execute("UPDATE games SET status = 'Final' WHERE game_pk = 201")
    dbmod.upsert_stats(con, 201, {"mlb_id": w["batters"]["Roman Anthony"],
                                  "at_bats": 4, "hits": 2, "hr": 0, "rbi": 0,
                                  "pitches": 0, "strikeouts": 0})
    assert dbmod.hit_streak(con, m["id"], 2026, 2) == 2


def test_hit_streak_line_on_dashboard(con, client):
    load_real_seed(con)
    demo = make_demo(con)
    out = _dashboard(client, demo)
    assert "Hit streak: <b>1</b>" in out
    assert "Best streak wins $50." in out


# ---------------------------------------------------------------- top performers

def test_top_performers_real_values(con):
    load_real_seed(con)
    top = dbmod.top_performers(con, 2026, 1)
    assert {(b["name"], b["hits"], b["points"]) for b in top["batters"]} == {
        ("Roman Anthony", 4, 4.0), ("Michael Busch", 4, 4.0),
        ("Christian Franklin", 4, 4.0), ("Pete Alonso", 4, 4.0),
        ("Luke Keaschall", 4, 4.0)}
    assert [(p["name"], p["pitches"], p["strikeouts"], p["points"])
            for p in top["pitchers"]] == [
        ("Cristopher Sánchez", 100, 10, 11.0),
        ("Tarik Skubal", 75, 10, 10.75)]


def test_top_performers_on_dashboard(con, client):
    load_real_seed(con)
    demo = make_demo(con)
    out = _dashboard(client, demo)
    assert "TOP PERFORMERS — FRI SEP 25 (FULL DAY)" in out
    assert "BATTERS" in out and "PITCHERS" in out
    for name, points in (("Roman Anthony", "4.00"), ("Michael Busch", "4.00"),
                         ("Christian Franklin", "4.00"),
                         ("Pete Alonso", "4.00"), ("Luke Keaschall", "4.00"),
                         ("Cristopher Sánchez", "11.00"),
                         ("Tarik Skubal", "10.75")):
        assert name in out and points in out
    assert "100 pitches · 10 K" in out
    assert "75 pitches · 10 K" in out


def test_top_performers_empty_when_no_stats(con):
    w = build_week(con, with_stats=False)
    assert dbmod.top_performers(con, 2026, 1) == {"batters": [],
                                                  "pitchers": []}


# ---------------------------------------------------------------- standings card

def test_standings_card_division_label_and_link(con, client):
    load_real_seed(con)
    demo = make_demo(con)
    make_pags(con)
    out = _dashboard(client, demo)
    assert "STANDINGS — TOP 5" in out
    assert 'class="dash-div div-red">Red Hots</div>' in out
    assert "Pags" in out and "Demo T." in out
    assert "See full standings →" in out
    assert 'href="/standings"' in out


def test_standings_card_white_division(con, client):
    w = build_week(con)
    m = dbmod.create_member(con, "Whitaker White", email="w@x.com",
                            screen_name="Whit", division="white")
    out = _dashboard(client, m)
    assert 'class="dash-div div-white">White Hots</div>' in out
    assert "Red Hots" not in out
    assert "1st in White Hots" in out


def test_standings_top5_filtered_to_division(con, client):
    load_real_seed(con)
    demo = make_demo(con)
    make_pags(con)
    whit = dbmod.create_member(con, "Whitaker White", email="w@x.com",
                               screen_name="Whit", division="white")
    _save_picks(con, whit["id"], PAGS_PICKS)  # 22.00, but wrong division
    out = _dashboard(client, demo)
    assert "Pags" in out
    assert "Whit" not in out


# ---------------------------------------------------------------- my picks + misc

def test_my_picks_card(con, client):
    load_real_seed(con)
    demo = make_demo(con)
    out = _dashboard(client, demo)
    assert "MY PICKS" in out
    assert "Edit picks →" in out
    assert 'href="/picks"' in out
    for name in ("Roman Anthony", "Pete Alonso", "Adley Rutschman",
                 "Seiya Suzuki", "Connor Wong", "Clay Holmes"):
        assert name in out
    assert "Week total: 12.78 pts" in out
    assert "HOW SCORING WORKS" in out
    assert "1 pt per hit" in out
    assert "Captain (coming preseason)" in out


def test_empty_picks_state(con, client):
    w = build_week(con)
    m = make_member(con, "Ada")
    out = _dashboard(client, m)
    assert "You have no picks yet this week." in out
    assert "Make your picks →" in out
    assert "Hit streak: <b>0</b>" in out
    assert "donut('dash-d1', 'dash-l1', [])" in out
    assert "donut('dash-d2', 'dash-l2', [])" in out


def test_no_stats_state(con, client):
    # A week with no stats at all: top performers has nothing to show.
    w = build_week(con, with_stats=False)
    m = make_member(con, "Ada")
    out = _dashboard(client, m)
    assert "No games scored yet." in out
