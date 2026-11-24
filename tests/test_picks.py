"""Pick-lock rules: 5 distinct batters + 1 starting pitcher; picks for
games already started are frozen (except on playground weeks)."""
from datetime import datetime, timezone

import pytest

from app import db as dbmod
from app.picks import PickError, submit_picks, validate_pick_set
from conftest import build_week, make_member

FUTURE = "2030-03-29T17:05:00Z"  # far-future Friday, games not started
PAST = "2026-09-25T17:05:00Z"


def _five_batters(w):
    """Five batter picks as (mlb_id, game_pk) pairs — one slot each."""
    b = w["batters"]
    g = w["batter_game"]
    names = ["Roman Anthony", "Adley Rutschman", "Michael Busch",
             "Pete Alonso", "Heliot Ramos"]
    return [(b[n], g[n]) for n in names]


def _busch_both_games(w):
    """Busch picked for both games of the doubleheader (2 slots)."""
    return [(w["batters"]["Michael Busch"], 101),
            (w["batters"]["Michael Busch"], 103)]


def test_validate_happy_path(con):
    w = build_week(con)
    checked = validate_pick_set(con, 2026, 1, _five_batters(w),
                                w["pitchers"]["Clay Holmes"])
    assert len(checked["batters"]) == 5
    assert checked["pitcher"]["name"] == "Clay Holmes"


def test_validate_wrong_batter_count(con):
    w = build_week(con)
    with pytest.raises(PickError, match="exactly 5 batters"):
        validate_pick_set(con, 2026, 1, _five_batters(w)[:4],
                          w["pitchers"]["Clay Holmes"])


def test_validate_duplicate_batter(con):
    w = build_week(con)
    bats = _five_batters(w)
    bats[1] = bats[0]
    with pytest.raises(PickError, match="only be picked once"):
        validate_pick_set(con, 2026, 1, bats, w["pitchers"]["Clay Holmes"])


def test_validate_pitcher_in_batter_slots(con):
    w = build_week(con)
    bats = _five_batters(w)
    bats[0] = (w["pitchers"]["Clay Holmes"], 101)
    with pytest.raises(PickError, match="cannot also be picked"):
        validate_pick_set(con, 2026, 1, bats, w["pitchers"]["Clay Holmes"])


def test_validate_batter_slot_holds_pitcher(con):
    w = build_week(con)
    bats = _five_batters(w)
    bats[0] = (w["pitchers"]["Alec Gamboa"], 101)
    with pytest.raises(PickError, match="is a pitcher, not a batter"):
        validate_pick_set(con, 2026, 1, bats, w["pitchers"]["Clay Holmes"])


def test_validate_pitcher_slot_holds_batter(con):
    w = build_week(con)
    # Seiya Suzuki is a batter not in the five — put him in the pitcher slot.
    with pytest.raises(PickError, match="is not a pitcher"):
        validate_pick_set(con, 2026, 1, _five_batters(w),
                          w["batters"]["Seiya Suzuki"])


def test_validate_pitcher_must_be_a_starter(con):
    w = build_week(con)
    # Relief Arm pitched in game 101 but did not start it.
    with pytest.raises(PickError, match="not a starting pitcher"):
        validate_pick_set(con, 2026, 1, _five_batters(w),
                          w["pitchers"]["Relief Arm"])


def test_validate_pitcher_probable_starter_pre_game(con):
    # Pre-game: no starter lineup rows announced yet, but the game row
    # carries the probable starters — picking one must still validate.
    w = build_week(con, with_stats=False)
    con.execute("DELETE FROM lineups WHERE role = 'pitcher'")
    con.commit()
    checked = validate_pick_set(con, 2026, 1, _five_batters(w),
                                w["pitchers"]["Clay Holmes"])
    assert checked["pitcher"]["name"] == "Clay Holmes"


def test_validate_player_with_no_game_that_week(con):
    w = build_week(con)
    dbmod.upsert_player(con, {"mlb_id": 999, "name": "No Game Ned",
                              "team": "FA", "position": "LF",
                              "is_pitcher": False})
    bats = _five_batters(w)
    bats[0] = (999, 101)
    with pytest.raises(PickError, match="has no game this week"):
        validate_pick_set(con, 2026, 1, bats, w["pitchers"]["Clay Holmes"])


def test_submit_future_week_saves(con):
    w = build_week(con, playground=False, g1_time=FUTURE, g2_time=FUTURE)
    m = make_member(con, "Ada")
    res = submit_picks(con, m["id"], 2026, 1, _five_batters(w),
                       w["pitchers"]["Clay Holmes"],
                       now=datetime(2030, 3, 1, tzinfo=timezone.utc))
    assert res["saved"] == ["b1", "b2", "b3", "b4", "b5", "p1"]
    assert res["frozen"] == []
    assert len(dbmod.picks_for_member_week(con, m["id"], 2026, 1)) == 6


def test_submit_started_game_rejected_when_slot_empty(con):
    # Game 101 is in the past; game 102 is in the future.
    w = build_week(con, playground=False, g1_time=PAST, g2_time=FUTURE)
    m = make_member(con, "Ada")
    # Anthony's game (101) has started and the slot is empty.
    with pytest.raises(PickError, match="has already started"):
        submit_picks(con, m["id"], 2026, 1, _five_batters(w),
                     w["pitchers"]["Clay Holmes"],
                     now=datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc))


def test_submit_freezes_started_slots_keeps_future_edits(con):
    w = build_week(con, playground=False, g1_time=FUTURE, g2_time=FUTURE)
    m = make_member(con, "Ada")
    before = datetime(2030, 3, 1, tzinfo=timezone.utc)
    submit_picks(con, m["id"], 2026, 1, _five_batters(w),
                 w["pitchers"]["Clay Holmes"], now=before)
    # Game 101 now "starts" (move its first pitch into the past).
    con.execute("UPDATE games SET game_date_utc = ? WHERE game_pk = 101",
                (PAST,))
    con.commit()
    # Swap a game-101 batter (Anthony -> Shaw) and the pitcher; game-102
    # batters stay. Only the unstarted slots may change.
    bats2 = [(w["batters"][n], w["batter_game"][n]) for n in
             ["Matt Shaw", "Adley Rutschman", "Michael Busch",
              "Pete Alonso", "Heliot Ramos"]]
    res = submit_picks(con, m["id"], 2026, 1, bats2,
                       w["pitchers"]["Brendan Beck"],
                       now=datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc))
    assert "b1" in res["frozen"]  # Anthony's slot was a started game
    assert "p1" in res["saved"]   # Beck's game (102) had not started
    saved = {p["slot"]: p["mlb_id"] for p in
             dbmod.picks_for_member_week(con, m["id"], 2026, 1)}
    assert saved["b1"] == w["batters"]["Roman Anthony"]  # frozen, unchanged
    assert saved["p1"] == w["pitchers"]["Brendan Beck"]  # edited


def test_submit_playground_week_allows_past_games(con):
    w = build_week(con, playground=True)  # games Final, in the past
    m = make_member(con, "Ada", is_test=True)
    res = submit_picks(con, m["id"], 2026, 1, _five_batters(w),
                       w["pitchers"]["Clay Holmes"],
                       now=datetime(2026, 9, 26, tzinfo=timezone.utc))
    assert res["frozen"] == []
    assert len(res["saved"]) == 6


# ---------------------------------------------------------------- doubleheaders

def test_validate_doubleheader_both_games_uses_two_slots(con):
    w = build_week(con)
    bats = _busch_both_games(w) + [
        (w["batters"]["Roman Anthony"], 101),
        (w["batters"]["Pete Alonso"], 102),
        (w["batters"]["Heliot Ramos"], 102),
    ]
    checked = validate_pick_set(con, 2026, 1, bats,
                                w["pitchers"]["Clay Holmes"])
    assert len(checked["batters"]) == 5
    busch = [b for b in checked["batters"] if b["name"] == "Michael Busch"]
    assert sorted(b["game_pk"] for b in busch) == [101, 103]


def test_validate_doubleheader_same_game_twice_rejected(con):
    w = build_week(con)
    bats = [(w["batters"]["Michael Busch"], 101),
            (w["batters"]["Michael Busch"], 101),
            (w["batters"]["Roman Anthony"], 101),
            (w["batters"]["Pete Alonso"], 102),
            (w["batters"]["Heliot Ramos"], 102)]
    with pytest.raises(PickError, match="once per game"):
        validate_pick_set(con, 2026, 1, bats, w["pitchers"]["Clay Holmes"])


def test_validate_batter_not_playing_in_that_game(con):
    w = build_week(con)
    # Alonso plays game 102 only; submitting him for game 101 is invalid.
    bats = [(w["batters"]["Roman Anthony"], 101),
            (w["batters"]["Adley Rutschman"], 101),
            (w["batters"]["Michael Busch"], 101),
            (w["batters"]["Pete Alonso"], 101),
            (w["batters"]["Heliot Ramos"], 102)]
    with pytest.raises(PickError, match="not playing in that game"):
        validate_pick_set(con, 2026, 1, bats, w["pitchers"]["Clay Holmes"])


def test_validate_six_batter_rows_exceeds_five_slots(con):
    w = build_week(con)
    bats = _busch_both_games(w) + [
        (w["batters"]["Roman Anthony"], 101),
        (w["batters"]["Adley Rutschman"], 101),
        (w["batters"]["Pete Alonso"], 102),
        (w["batters"]["Heliot Ramos"], 102),
    ]
    with pytest.raises(PickError, match="exactly 5 batters"):
        validate_pick_set(con, 2026, 1, bats, w["pitchers"]["Clay Holmes"])


def test_submit_doubleheader_scores_each_game_independently(con):
    w = build_week(con, playground=True)
    m = make_member(con, "Ada", is_test=True)
    bats = _busch_both_games(w) + [
        (w["batters"]["Roman Anthony"], 101),
        (w["batters"]["Pete Alonso"], 102),
        (w["batters"]["Heliot Ramos"], 102),
    ]
    res = submit_picks(con, m["id"], 2026, 1, bats,
                       w["pitchers"]["Brendan Beck"],
                       now=datetime(2026, 9, 26, tzinfo=timezone.utc))
    assert len(res["saved"]) == 6
    picks = dbmod.picks_for_member_week(con, m["id"], 2026, 1)
    stats = dbmod.stats_for_week(con, 2026, 1)
    from app.scoring import week_points
    total = week_points(
        [{"kind": p["kind"], "mlb_id": p["mlb_id"], "game_pk": p["game_pk"]}
         for p in picks], stats)
    # Busch 2 + 2, Anthony 3, Alonso 3, Ramos 2 = 12; Beck 8.97.
    assert total == 20.97


def test_submit_doubleheader_freezes_only_started_game(con):
    # Both games future; submit Busch for both. Then game 101 starts:
    # its Busch pick freezes, the game-103 Busch pick stays editable.
    w = build_week(con, playground=False, g1_time=FUTURE, g2_time=FUTURE,
                   g3_time=FUTURE)
    m = make_member(con, "Ada")
    before = datetime(2030, 3, 1, tzinfo=timezone.utc)
    bats = _busch_both_games(w) + [
        (w["batters"]["Roman Anthony"], 101),
        (w["batters"]["Pete Alonso"], 102),
        (w["batters"]["Heliot Ramos"], 102),
    ]
    submit_picks(con, m["id"], 2026, 1, bats,
                 w["pitchers"]["Brendan Beck"], now=before)
    # Game 101 now "starts" (move its first pitch into the past).
    con.execute("UPDATE games SET game_date_utc = ? WHERE game_pk = 101",
                (PAST,))
    con.commit()
    # Keep the game-101 Busch pick, swap the game-103 Busch pick for a
    # game-102 batter (Coby Mayo). Only the unstarted slot may change.
    bats2 = [(w["batters"]["Michael Busch"], 101),
             (w["batters"]["Coby Mayo"], 102),
             (w["batters"]["Roman Anthony"], 101),
             (w["batters"]["Pete Alonso"], 102),
             (w["batters"]["Heliot Ramos"], 102)]
    res = submit_picks(con, m["id"], 2026, 1, bats2,
                       w["pitchers"]["Brendan Beck"],
                       now=datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc))
    assert "b1" in res["frozen"]   # Busch game-101 slot frozen
    assert "b2" in res["saved"]    # Busch game-103 slot swapped to Mayo
    saved = {p["slot"]: (p["mlb_id"], p["game_pk"]) for p in
             dbmod.picks_for_member_week(con, m["id"], 2026, 1)}
    assert saved["b1"] == (w["batters"]["Michael Busch"], 101)
    assert saved["b2"] == (w["batters"]["Coby Mayo"], 102)


# ---------------------------------------------------------------- HTTP flow

def test_pick_link_gate_flow(con, client):
    w = build_week(con)
    m = make_member(con, "Ada")
    # No cookie -> redirect to /locked.
    assert client.get("/picks", follow_redirects=False).status_code == 303
    locked = client.get("/locked")
    assert locked.status_code == 200
    assert "pick link" in locked.text.lower()
    # Bad token -> /locked.
    assert client.get("/submit/bad-token", follow_redirects=False).status_code == 303
    # Good token -> cookie set, lands on the pick page.
    r = client.get(f"/submit/{m['pick_token']}", follow_redirects=False)
    assert r.status_code == 303
    page = client.get("/picks")
    assert page.status_code == 200
    assert "Roman Anthony" in page.text
    assert "Clay Holmes" in page.text
    # Lineup dots render for in/out players.
    assert 'dot in' in page.text
    assert 'dot out' in page.text  # Contreras / Kjerstad on the bench
    # Game times + opposing pitchers render.
    assert "1:05 PM" in page.text
    assert "vs Clay Holmes" in page.text


def test_http_submit_and_dashboard(con, client):
    w = build_week(con)
    m = make_member(con, "Ada")
    client.get(f"/submit/{m['pick_token']}")
    bats = _five_batters(w)
    r = client.post("/picks", data={
        "batter_1": bats[0][0], "batter_1_game": bats[0][1],
        "batter_2": bats[1][0], "batter_2_game": bats[1][1],
        "batter_3": bats[2][0], "batter_3_game": bats[2][1],
        "batter_4": bats[3][0], "batter_4_game": bats[3][1],
        "batter_5": bats[4][0], "batter_5_game": bats[4][1],
        "pitcher": w["pitchers"]["Clay Holmes"],
    }, follow_redirects=False)
    assert r.status_code == 303  # -> /dashboard
    dash = client.get("/dashboard")
    assert dash.status_code == 200
    # Anthony 3 + Rutschman 2 + Busch 2 + Alonso 3 + Ramos 2 = 12,
    # Holmes 3.78 -> 15.78.
    assert "15.78" in dash.text


def test_http_submit_invalid_shows_error(con, client):
    w = build_week(con)
    m = make_member(con, "Ada")
    client.get(f"/submit/{m['pick_token']}")
    bats = _five_batters(w)
    r = client.post("/picks", data={
        "batter_1": bats[0][0], "batter_1_game": bats[0][1],
        "batter_2": bats[1][0], "batter_2_game": bats[1][1],
        "batter_3": bats[2][0], "batter_3_game": bats[2][1],
        "batter_4": bats[3][0], "batter_4_game": bats[3][1],
        "batter_5": 0,
        "pitcher": w["pitchers"]["Clay Holmes"],
    })
    assert r.status_code == 200
    assert "exactly 5 batters" in r.text


def test_pick_page_renders_doubleheader_player_twice(con, client):
    w = build_week(con)
    m = make_member(con, "Ada")
    client.get(f"/submit/{m['pick_token']}")
    page = client.get("/picks")
    assert page.status_code == 200
    # Busch plays both games of the doubleheader: one row per game.
    assert page.text.count("Michael Busch") == 2
    assert 'data-game="101"' in page.text
    assert 'data-game="103"' in page.text


def test_http_submit_doubleheader_both_games(con, client):
    w = build_week(con)
    m = make_member(con, "Ada")
    client.get(f"/submit/{m['pick_token']}")
    b = w["batters"]
    r = client.post("/picks", data={
        "batter_1": b["Michael Busch"], "batter_1_game": 101,
        "batter_2": b["Michael Busch"], "batter_2_game": 103,
        "batter_3": b["Roman Anthony"], "batter_3_game": 101,
        "batter_4": b["Pete Alonso"], "batter_4_game": 102,
        "batter_5": b["Heliot Ramos"], "batter_5_game": 102,
        "pitcher": w["pitchers"]["Brendan Beck"],
    }, follow_redirects=False)
    assert r.status_code == 303  # -> /dashboard
    dash = client.get("/dashboard")
    assert dash.status_code == 200
    # Busch 2 + 2, Anthony 3, Alonso 3, Ramos 2 = 12; Beck 8.97.
    assert "20.97" in dash.text
    assert dash.text.count("Michael Busch") == 2


def test_standings_marks_test_members(con, client):
    w = build_week(con)
    m1 = make_member(con, "Ada")
    m2 = make_member(con, "Testy", is_test=True)
    submit_picks(con, m1["id"], 2026, 1, _five_batters(w),
                 w["pitchers"]["Clay Holmes"],
                 now=datetime(2026, 9, 26, tzinfo=timezone.utc))
    submit_picks(con, m2["id"], 2026, 1, _five_batters(w),
                 w["pitchers"]["Brendan Beck"],
                 now=datetime(2026, 9, 26, tzinfo=timezone.utc))
    client.get(f"/submit/{m1['pick_token']}")
    page = client.get("/standings")
    assert page.status_code == 200
    assert "Testy" in page.text
    assert "test" in page.text.lower()
    # Beck (8.97) outscores Holmes (3.78): Testy first.
    assert page.text.index("Testy") < page.text.index("Ada")
