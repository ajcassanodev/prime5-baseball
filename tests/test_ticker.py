"""Live Friday-night ticker (Nov 25, 2026, mock v5): headline
generation, streak listing, bonuses, scoreboard play selection,
ticker assembly, and dashboard rendering (HTML + /dashboard.json).

The real-data tests load the actual Sep 25, 2026 playground seed,
so the numbers asserted there are the real Friday's numbers.
"""
import json
import os

import pytest

from app import db as dbmod
from app import matchups as matchups_mod
from app import ticker as ticker_mod
from conftest import build_week, make_member
from test_dashboard import (DEMO_PICKS, PAGS_PICKS, load_real_seed,
                            make_demo, make_pags)


def _pick(con, member_id, slot, kind, mlb_id, game_pk, week=1, season=2026):
    dbmod.save_pick(con, member_id, season, week, slot, kind, mlb_id,
                    game_pk)


def _white_member(con, name="Whitaker White"):
    return dbmod.create_member(con, name, email="w@x.com",
                               screen_name="Whit", division="white")


def _set_status(con, status, game_pk=None):
    if game_pk is None:
        con.execute("UPDATE games SET status = ?", (status,))
    else:
        con.execute("UPDATE games SET status = ? WHERE game_pk = ?",
                    (status, game_pk))
    con.commit()


def _dashboard(client, member):
    client.get(f"/submit/{member['pick_token']}")
    r = client.get("/dashboard")
    assert r.status_code == 200
    return r.text


# ---------------------------------------------------------------- milestone

def test_milestone_state_stages():
    assert ticker_mod.milestone_state(0) == {
        "banked": 0, "stage": 1,
        "stage_label": "MILESTONE — 5 HITS IN A ROW",
        "target": 5, "progress": 0, "pct": 0,
        "reward": "→ EXTRA BATTER NEXT WEEK"}
    s = ticker_mod.milestone_state(3)
    assert (s["stage"], s["target"], s["progress"], s["pct"],
            s["banked"]) == (1, 5, 3, 60, 0)
    s = ticker_mod.milestone_state(5)
    assert (s["stage"], s["target"], s["progress"], s["pct"],
            s["banked"]) == (2, 10, 5, 50, 1)
    assert "banked at 5" in s["reward"]
    s = ticker_mod.milestone_state(7)
    assert (s["stage"], s["target"], s["progress"], s["pct"],
            s["banked"]) == (2, 10, 7, 70, 1)
    s = ticker_mod.milestone_state(10)
    assert (s["banked"], s["pct"], s["progress"]) == (2, 100, 10)
    s = ticker_mod.milestone_state(12)
    assert (s["banked"], s["pct"]) == (2, 100)
    assert "2 extra batters banked" in s["reward"]


# ---------------------------------------------------------------- member streaks

def test_member_streaks_cutoff_and_order(con):
    w = build_week(con)
    b = w["batters"]
    # Streak 5: five hits in slot order.
    a = make_member(con, "Ada")
    for slot, name in (("b1", "Roman Anthony"), ("b2", "Adley Rutschman"),
                       ("b3", "Michael Busch"), ("b4", "Matt Shaw"),
                       ("b5", "Seiya Suzuki")):
        _pick(con, a["id"], slot, "batter", b[name], 101)
    # Streak 4: a hitless leadoff resets, then four hits.
    c = make_member(con, "Cal")
    _pick(con, c["id"], "b1", "batter", b["Seiya Suzuki"], 101)  # 2 H
    # Suzuki hits — use Contreras (DNP, no stat row in a Final game)
    # as the hitless leadoff instead.
    con.execute("DELETE FROM picks WHERE member_id = ?", (c["id"],))
    _pick(con, c["id"], "b1", "batter", b["Willson Contreras"], 101)
    for slot, name in (("b2", "Roman Anthony"), ("b3", "Adley Rutschman"),
                       ("b4", "Michael Busch"), ("b5", "Matt Shaw")):
        _pick(con, c["id"], slot, "batter", b[name], 101)
    # Streak 0: no picks.
    make_member(con, "Dee")
    rows = ticker_mod.member_streaks(con, 2026, 1)
    assert [(r["name"], r["streak"]) for r in rows] == [("Ada", 5)]
    assert dbmod.hit_streak(con, c["id"], 2026, 1) == 4


def test_member_streaks_limit_and_sort(con):
    w = build_week(con)
    b = w["batters"]
    names = [("b1", "Roman Anthony"), ("b2", "Adley Rutschman"),
             ("b3", "Michael Busch"), ("b4", "Matt Shaw"),
             ("b5", "Seiya Suzuki")]
    members = []
    for name in ("Zed", "Yan", "Xiu", "Wes"):
        m = make_member(con, name)
        for slot, pname in names:
            _pick(con, m["id"], slot, "batter", b[pname], 101)
        members.append(m)
    # Zed reaches 6: one more hit in week 2.
    dbmod.ensure_week(con, 2026, 2, "2026-10-02",
                      label="Week 2 — 2026-10-02", playground=True)
    dbmod.upsert_game(con, {"game_pk": 201, "season": 2026, "week": 2,
                            "friday_date": "2026-10-02",
                            "game_date_utc": "2026-10-02T17:05:00Z",
                            "status": "Final", "home_team": "BOS",
                            "away_team": "CHC"})
    dbmod.upsert_stats(con, 201, {"mlb_id": b["Roman Anthony"],
                                  "at_bats": 4, "hits": 2, "hr": 0,
                                  "rbi": 0, "pitches": 0, "strikeouts": 0})
    _pick(con, members[0]["id"], "b1", "batter",
          b["Roman Anthony"], 201, week=2)
    rows = ticker_mod.member_streaks(con, 2026, 2)
    assert [(r["name"], r["streak"]) for r in rows] == [
        ("Zed", 6), ("Wes", 5), ("Xiu", 5)]
    # Only the top 3 — Yan's 5 is cut, ties break by name.


# ---------------------------------------------------------------- headline

def test_headline_both_divisions(con):
    w = build_week(con)
    b = w["batters"]
    ada = make_member(con, "Ada")
    _pick(con, ada["id"], "b1", "batter", b["Roman Anthony"], 101)
    whit = _white_member(con)
    # Alonso's line gets a HR so the white story leads with one.
    dbmod.upsert_stats(con, 102, {"mlb_id": b["Pete Alonso"],
                                  "at_bats": 5, "hits": 3, "hr": 1,
                                  "rbi": 2, "pitches": 0, "strikeouts": 0})
    _pick(con, whit["id"], "b1", "batter", b["Pete Alonso"], 102)
    head = ticker_mod.headline_for_week(con, 2026, 1)
    assert head is not None
    assert "Roman Anthony's 3-for-4 lifts Ada to 1st in Red Hots" in head["text"]
    assert "Pete Alonso's 2-run HR lifts Whit to 1st in White Hots" in head["text"]
    assert ", while " in head["text"]
    assert 'class="tk-div-red">Red Hots</span>' in head["html"]
    assert 'class="tk-div-white">White Hots</span>' in head["html"]


def test_headline_one_division_only(con):
    w = build_week(con)
    b = w["batters"]
    ada = make_member(con, "Ada")
    _pick(con, ada["id"], "b1", "batter", b["Roman Anthony"], 101)
    head = ticker_mod.headline_for_week(con, 2026, 1)
    assert "Red Hots" in head["text"]
    assert "White Hots" not in head["text"]
    assert "while" not in head["text"]


def test_headline_pitcher_story(con):
    w = build_week(con)
    p = w["pitchers"]
    ada = make_member(con, "Ada")
    _pick(con, ada["id"], "p1", "pitcher", p["Brendan Beck"], 102)
    head = ticker_mod.headline_for_week(con, 2026, 1)
    assert "Brendan Beck's 8 K lifts Ada to 1st in Red Hots" in head["text"]


def test_headline_pregame(con):
    w = build_week(con, with_stats=False)
    _set_status(con, "Scheduled")
    head = ticker_mod.headline_for_week(con, 2026, 1)
    assert head is not None
    assert head["text"] == (
        "Friday's first pitch at 1:05 PM — picks lock at first pitch")


def test_headline_day_top_fallback(con):
    w = build_week(con)  # stats, no member picks at all
    head = ticker_mod.headline_for_week(con, 2026, 1)
    assert head is not None
    # Three 3-hit batters; the tie breaks by name.
    assert head["text"] == (
        "Coby Mayo 3-for-4 · Brendan Beck 8 K — Friday's top lines")


def test_headline_no_games(con):
    dbmod.ensure_week(con, 2026, 1, "2026-09-25",
                      label="Week 1", playground=True)
    assert ticker_mod.headline_for_week(con, 2026, 1) is None


# ---------------------------------------------------------------- bonuses

def test_member_bonuses_milestone(con):
    w = build_week(con)
    m = make_member(con, "Ada")
    assert ticker_mod.member_bonuses(con, m, 2026, 1, 4) == []
    assert ticker_mod.member_bonuses(con, m, 2026, 1, 5) == [
        "Extra batter unlocked — 5 hits in a row"]
    assert ticker_mod.member_bonuses(con, m, 2026, 1, 10) == [
        "Another extra batter unlocked — 10 hits in a row"]


def test_member_bonuses_h2h_grant(con):
    w = build_week(con)
    b = w["batters"]
    ada = make_member(con, "Ada")
    bob = make_member(con, "Bob")
    for slot, name in (("b1", "Roman Anthony"), ("b2", "Adley Rutschman"),
                       ("b3", "Michael Busch")):
        _pick(con, ada["id"], slot, "batter", b[name], 101)
    _pick(con, bob["id"], "b1", "batter", b["Seiya Suzuki"], 101)
    # Week 1 is Final; Ada wins, so the grant is awarded lazily.
    matchups_mod.award_grants_through(con, 2026, 1)
    assert ticker_mod.member_bonuses(con, ada, 2026, 1, 3) == [
        "+1 batter pick earned — H2H win vs Bob"]
    # The grant is usable in week 2.
    assert ticker_mod.member_bonuses(con, ada, 2026, 2, 3) == [
        "+1 batter pick available this week — H2H win"]
    # The loser earns nothing.
    assert ticker_mod.member_bonuses(con, bob, 2026, 1, 0) == []


# ---------------------------------------------------------------- pick results

def test_pick_results(con):
    w = build_week(con)
    b, p = w["batters"], w["pitchers"]
    m = make_member(con, "Ada")
    _pick(con, m["id"], "b1", "batter", b["Roman Anthony"], 101)
    _pick(con, m["id"], "p1", "pitcher", p["Brendan Beck"], 102)
    assert ticker_mod.pick_results(con, m["id"], 2026, 1) == [
        "Roman Anthony 3-for-4 · Final", "Brendan Beck 8 K · Final"]
    # A pick with no stat row (DNP) has no line.
    _pick(con, m["id"], "b2", "batter", b["Willson Contreras"], 101)
    assert len(ticker_mod.pick_results(con, m["id"], 2026, 1)) == 2


# ---------------------------------------------------------------- scoreboard

def _live_week(con):
    w = build_week(con)
    _set_status(con, "Live")
    return w


def test_big_play_homer_beats_hits(con):
    w = _live_week(con)
    b = w["batters"]
    m = make_member(con, "Ada")
    dbmod.upsert_stats(con, 102, {"mlb_id": b["Pete Alonso"],
                                  "at_bats": 5, "hits": 3, "hr": 1,
                                  "rbi": 2, "pitches": 0, "strikeouts": 0})
    _pick(con, m["id"], "b1", "batter", b["Roman Anthony"], 101)
    _pick(con, m["id"], "b2", "batter", b["Pete Alonso"], 102)
    play = ticker_mod.big_play(con, m, 2026, 1)
    assert play["kind"] == "homer"
    assert play["big"] == "HOME RUN"
    assert play["flash"] is True
    assert play["key"] == "hr:21:102"
    assert play["play"] == "Pete Alonso — 2-run HR"
    assert play["pts"] == "+3.0 PTS — 3 HITS · 1 HR"


def test_big_play_multi_hit_and_single(con):
    w = _live_week(con)
    b = w["batters"]
    m = make_member(con, "Ada")
    _pick(con, m["id"], "b1", "batter", b["Roman Anthony"], 101)
    play = ticker_mod.big_play(con, m, 2026, 1)
    assert play["kind"] == "hit" and play["big"] == "MULTI-HIT"
    assert play["play"] == "Roman Anthony — 3-for-4"
    assert play["pts"] == "+3.0 PTS — 3 HITS"
    # A 1-hit game is a SINGLE.
    dbmod.upsert_stats(con, 102, {"mlb_id": b["Heston Kjerstad"],
                                  "at_bats": 4, "hits": 1, "hr": 0,
                                  "rbi": 0, "pitches": 0, "strikeouts": 0})
    m2 = make_member(con, "Bo")
    _pick(con, m2["id"], "b1", "batter", b["Heston Kjerstad"], 102)
    play = ticker_mod.big_play(con, m2, 2026, 1)
    assert play["big"] == "SINGLE"
    assert play["play"] == "Heston Kjerstad — 1-for-4"


def test_big_play_strikeout(con):
    w = _live_week(con)
    p = w["pitchers"]
    m = make_member(con, "Ada")
    _pick(con, m["id"], "p1", "pitcher", p["Brendan Beck"], 102)
    play = ticker_mod.big_play(con, m, 2026, 1)
    assert play["kind"] == "k" and play["big"] == "STRIKEOUT"
    assert play["play"] == "Brendan Beck — strikeout #8"
    assert play["pts"] == "+8.97 PTS — 8 K"
    # Under 5 K is not a big moment.
    m2 = make_member(con, "Bo")
    _pick(con, m2["id"], "p1", "pitcher", p["Alec Gamboa"], 101)
    assert ticker_mod.big_play(con, m2, 2026, 1) is None


def test_big_play_milestone_crossing(con):
    w = build_week(con)  # week 1 Final, member has no picks -> streak 0
    b = w["batters"]
    m = make_member(con, "Ada")
    dbmod.ensure_week(con, 2026, 2, "2026-10-02",
                      label="Week 2 — 2026-10-02", playground=True)
    dbmod.upsert_game(con, {"game_pk": 201, "season": 2026, "week": 2,
                            "friday_date": "2026-10-02",
                            "game_date_utc": "2026-10-02T17:05:00Z",
                            "status": "Live", "home_team": "BOS",
                            "away_team": "CHC"})
    for slot, name in (("b1", "Roman Anthony"), ("b2", "Adley Rutschman"),
                       ("b3", "Michael Busch"), ("b4", "Matt Shaw"),
                       ("b5", "Seiya Suzuki")):
        dbmod.upsert_stats(con, 201, {"mlb_id": b[name],
                                      "at_bats": 4, "hits": 2, "hr": 0,
                                      "rbi": 0, "pitches": 0, "strikeouts": 0})
        _pick(con, m["id"], slot, "batter", b[name], 201, week=2)
    play = ticker_mod.big_play(con, m, 2026, 2)
    assert play["kind"] == "milestone"
    assert play["big"] == "MILESTONE!"
    assert play["key"] == "milestone:5"
    assert play["flash"] is True
    assert "6 batters next week" in play["play"]


def test_big_play_week_final(con):
    load_real_seed(con)
    demo = make_demo(con)
    play = ticker_mod.big_play(con, demo, 2026, 1)
    assert play["kind"] == "final"
    assert play["big"] == "WEEK FINAL"
    assert play["key"] == "final"
    assert play["pts"] == "+12.78 PTS — WEEK TOTAL"
    assert "Week 1 — 1st in Red Hots · Streak 1" in play["play"]


def test_big_play_none_without_picks(con):
    w = build_week(con)
    m = make_member(con, "Ada")
    assert ticker_mod.big_play(con, m, 2026, 1) is None


# ---------------------------------------------------------------- assembly

def test_ticker_data_no_matchup_no_bonuses(con):
    w = build_week(con)
    m = make_member(con, "Ada")
    data = ticker_mod.ticker_data(con, m, 2026, 1)
    assert data["h2h"] is None
    assert [i["label"] for i in data["strip_items"]] == ["HEADLINE"]
    assert data["bonuses"] == []
    assert data["streaks"] == []
    assert data["board"] is None and data["board_key"] is None
    assert data["milestone"]["stage"] == 1
    assert data["status"] == {"total": 0.0, "streak": 0, "rank": 1,
                              "rank_ordinal": "1st",
                              "division_name": "Red Hots"}
    assert data["final"] is True


def test_ticker_data_full(con):
    w = build_week(con)
    b = w["batters"]
    ada = make_member(con, "Ada")
    bob = make_member(con, "Bob")
    for slot, name in (("b1", "Roman Anthony"), ("b2", "Adley Rutschman"),
                       ("b3", "Michael Busch"), ("b4", "Matt Shaw"),
                       ("b5", "Seiya Suzuki")):
        _pick(con, ada["id"], slot, "batter", b[name], 101)
    _pick(con, bob["id"], "b1", "batter", b["Seiya Suzuki"], 101)
    matchups_mod.award_grants_through(con, 2026, 1)
    data = ticker_mod.ticker_data(con, ada, 2026, 1)
    labels = [i["label"] for i in data["strip_items"]]
    assert labels[0] == "HEADLINE"
    assert "H2H" in labels
    assert labels.count("BONUS") == 2  # milestone + H2H grant
    assert "STREAK" in labels
    assert data["streaks"] == [{"name": "Ada", "streak": 5}]
    assert data["h2h"]["text"].startswith("You ")
    assert "Bob" in data["h2h"]["text"]
    assert data["board"]["kind"] == "final"
    assert json.dumps(data)  # JSON-serializable for /dashboard.json


def test_ticker_data_real_seed(con):
    load_real_seed(con)
    demo = make_demo(con)
    make_pags(con)
    data = ticker_mod.ticker_data(con, demo, 2026, 1)
    assert data["headline"]["text"] == (
        "Pete Alonso's 3-run HR lifts Demo T. to 2nd in Red Hots")
    assert 'class="tk-div-red">Red Hots</span>' in data["headline"]["html"]
    assert data["streaks"] == [{"name": "Pags", "streak": 5}]
    assert data["h2h"]["text"] == "You 12.78 – Pags 22.00 · 17 games final"
    assert data["board"]["kind"] == "final"
    assert data["board"]["pts"] == "+12.78 PTS — WEEK TOTAL"
    assert data["status"]["total"] == 12.78
    assert data["status"]["streak"] == 1


# ---------------------------------------------------------------- rendering

def test_dashboard_renders_ticker(con, client):
    load_real_seed(con)
    demo = make_demo(con)
    make_pags(con)
    out = _dashboard(client, demo)
    assert '<div class="tk-strip">' in out
    assert "HOW FRIDAY WENT" in out
    assert "3-run HR lifts Demo T. to 2nd in Red Hots" in out
    assert "WEEK FINAL" in out
    assert "12.78 PTS — WEEK TOTAL" in out
    assert "Member streaks 5+" in out
    assert "Pags 5" in out
    # Existing cards untouched and still in order after the ticker.
    assert out.index("tk-strip") < out.index("MY PICKS")
    for card in ("MY PICKS", "TOP PERFORMERS", "STANDINGS — TOP 5",
                 "HOW SCORING WORKS"):
        assert card in out


def test_dashboard_json(con, client):
    load_real_seed(con)
    demo = make_demo(con)
    client.get(f"/submit/{demo['pick_token']}")
    r = client.get("/dashboard.json")
    assert r.status_code == 200
    data = r.json()
    assert data["board_key"] == "final"
    assert data["headline"]["text"].startswith("Pete Alonso's 3-run HR")
    assert data["status"]["total"] == 12.78
    assert data["milestone"]["stage"] == 1


def test_dashboard_json_requires_member(client):
    r = client.get("/dashboard.json")
    assert r.status_code == 303


def test_dashboard_renders_ticker_without_picks(con, client):
    w = build_week(con)
    m = make_member(con, "Ada")
    out = _dashboard(client, m)
    assert '<div class="tk-strip">' in out
    # Day-top fallback headline: no member picks, stats exist. (The
    # apostrophe is HTML-escaped in the rendered page.)
    assert "Coby Mayo 3-for-4" in out
    assert "top lines" in out
    assert "STAGE 1" not in out  # milestone label is the full phrase
    assert "MILESTONE — 5 HITS IN A ROW" in out
    assert "HOW FRIDAY WENT" in out
