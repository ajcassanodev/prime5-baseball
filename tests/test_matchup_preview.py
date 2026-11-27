"""Matchup preview in the drop-down on /matchups (Nov 27, 2026).

Before any game starts, tapping a pairing opens a preview in place —
previous meeting, all-time record, season records, and form — instead
of an empty scoreboard. Once the week is underway the same tap shows
the live detail (unchanged). Everything is derived from stored picks
and stats; a first meeting shows "First meeting." and a member with no
completed week has no form line.
"""
from app import db as dbmod
from app import matchups as matchups_mod
from app.picks import submit_picks
from conftest import build_week, make_member


def _sign_in(client, member):
    return client.get(f"/submit/{member['pick_token']}")


def _five_batters(w):
    b = w["batters"]
    g = w["batter_game"]
    names = ["Roman Anthony", "Adley Rutschman", "Michael Busch",
             "Pete Alonso", "Heliot Ramos"]
    return [(b[n], g[n]) for n in names]


def _weak_five(w):
    b = w["batters"]
    g = w["batter_game"]
    names = ["Adley Rutschman", "Michael Busch", "Matt Shaw",
             "Seiya Suzuki", "Heliot Ramos"]
    return [(b[n], g[n]) for n in names]


def _submit(con, member_id, w, batter_picks, pitcher_name, week=1):
    return submit_picks(con, member_id, 2026, week, batter_picks,
                        w["pitchers"][pitcher_name])


def _unstarted_week(con, week, friday_date, base_pk):
    """A Friday whose games are still Scheduled with first pitch in
    2030 — nothing has started, so the drop-down shows a preview.
    Fresh game_pks so earlier weeks' games stay intact."""
    dbmod.ensure_week(con, 2026, week, friday_date,
                      label=f"Week {week} — {friday_date}", playground=True)
    for i, (gpk, t) in enumerate((
            (base_pk, "2030-10-02T17:05:00Z"),
            (base_pk + 1, "2030-10-02T20:05:00Z"))):
        dbmod.upsert_game(con, {"game_pk": gpk, "season": 2026,
                                "week": week, "friday_date": friday_date,
                                "game_date_utc": t, "status": "Scheduled",
                                "home_team": "BOS", "away_team": "CHC"})
    con.commit()


def _final_week2(con):
    """Week 2 (Oct 2, 2026), Final, reusing week-1 players with fresh
    game_pks — every batter has 2 hits, so Ada's strong five scores
    13.78 (10 + Holmes 3.78) and Bob's weak five 11.40 (10 + Gamboa
    1.40). Mirrors test_head_to_head._build_week2."""
    dbmod.ensure_week(con, 2026, 2, "2026-10-02",
                      label="Week 2 — 2026-10-02", playground=True)
    games = {
        301: {"game_pk": 301, "season": 2026, "week": 2,
              "friday_date": "2026-10-02",
              "game_date_utc": "2026-10-02T17:05:00Z",
              "status": "Final", "home_team": "BOS", "away_team": "CHC",
              "home_starter_mlb_id": 201, "away_starter_mlb_id": 202},
        302: {"game_pk": 302, "season": 2026, "week": 2,
              "friday_date": "2026-10-02",
              "game_date_utc": "2026-10-02T20:05:00Z",
              "status": "Final", "home_team": "NYY", "away_team": "BAL",
              "home_starter_mlb_id": 203, "away_starter_mlb_id": 204},
    }
    for g in games.values():
        dbmod.upsert_game(con, g)
    for gpk, ids in ((301, [11, 12, 14, 15, 16, 201, 202]),
                     (302, [21, 22, 24, 25, 26, 203, 204])):
        for mlb_id in ids:
            dbmod.add_roster(con, gpk, mlb_id)
    for gpk, rows in ((301, [(11, 1), (12, 2), (14, 1), (15, 2), (16, 3)]),
                      (302, [(21, 1), (22, 2), (24, 1), (25, 2), (26, 3)])):
        for mlb_id, order in rows:
            dbmod.set_lineup(con, gpk, "batter", mlb_id, order)
    for gpk, starter in ((301, 201), (301, 202), (302, 203), (302, 204)):
        dbmod.set_lineup(con, gpk, "pitcher", starter, None)
    for gpk, mlb_id, hits in ((301, 11, 2), (301, 12, 2), (301, 14, 2),
                               (301, 15, 2), (301, 16, 2),
                               (302, 21, 2), (302, 22, 2), (302, 24, 2),
                               (302, 25, 2), (302, 26, 2)):
        dbmod.upsert_stats(con, gpk, {"mlb_id": mlb_id, "at_bats": 4,
                                       "hits": hits, "hr": 0, "rbi": 0,
                                       "pitches": 0, "strikeouts": 0})
    for gpk, mlb_id, pitches, k in ((301, 201, 40, 1), (301, 202, 78, 3),
                                    (302, 203, 97, 8), (302, 204, 55, 4)):
        dbmod.upsert_stats(con, gpk, {"mlb_id": mlb_id, "at_bats": 0,
                                       "hits": 0, "hr": 0, "rbi": 0,
                                       "pitches": pitches, "strikeouts": k})
    con.commit()
    b = {"Roman Anthony": 11, "Adley Rutschman": 12, "Michael Busch": 14,
         "Matt Shaw": 15, "Seiya Suzuki": 16, "Pete Alonso": 21,
         "Coby Mayo": 22, "Heliot Ramos": 24, "Amed Rosario": 25,
         "Anthony Volpe": 26}
    return {"batters": b,
            "pitchers": {"Alec Gamboa": 201, "Clay Holmes": 202,
                         "Brendan Beck": 203, "Trevor Rogers": 204},
            "batter_game": {"Roman Anthony": 301, "Adley Rutschman": 301,
                            "Michael Busch": 301, "Matt Shaw": 301,
                            "Seiya Suzuki": 301, "Pete Alonso": 302,
                            "Coby Mayo": 302, "Heliot Ramos": 302,
                            "Amed Rosario": 302, "Anthony Volpe": 302}}


def _week1_ada_beats_bob(con):
    """Week 1 final, Ada 15.78 beats Bob 11.40; returns (w, ada, bob)."""
    w = build_week(con)
    ada = make_member(con, "Ada")
    bob = make_member(con, "Bob")
    _submit(con, ada["id"], w, _five_batters(w), "Clay Holmes")
    _submit(con, bob["id"], w, _weak_five(w), "Alec Gamboa")
    return w, ada, bob


# ---------------------------------------------------------------- units

def test_preview_repeat_round_with_previous_meeting(con):
    w1, ada, bob = _week1_ada_beats_bob(con)
    _unstarted_week(con, 2, "2026-10-02", 301)
    p = matchups_mod.matchup_preview(con, 2026, 2, ada["id"], bob["id"])
    assert p is not None
    assert p["kind"] == "preview"
    assert p["first_meeting"] is False
    assert p["previous"]["week"] == 1
    assert p["previous"]["winner"] == "a"
    assert p["previous"]["a_points"] == 15.78
    assert p["previous"]["b_points"] == 11.40
    assert p["previous"]["line"] == "Week 1 — Ada won 15.78 – 11.40"
    # Winner's top pick was Holmes (3.78) — the decided line names him.
    assert p["previous"]["decided"] == \
        "Clay Holmes's 3 strikeouts led the way"
    assert p["h2h_line"] == "Ada leads 1 – 0"
    assert p["record_line"] == "Ada 1–0 · Bob 0–1"
    assert p["a_form_line"] == "Last week 15.78 pts · Won last week"
    assert p["b_form_line"] == "Last week 11.40 pts · Lost last week"


def test_preview_first_meeting(con):
    _unstarted_week(con, 1, "2026-09-25", 301)
    ada = make_member(con, "Ada")
    bob = make_member(con, "Bob")
    p = matchups_mod.matchup_preview(con, 2026, 1, ada["id"], bob["id"])
    assert p is not None
    assert p["first_meeting"] is True
    assert p["previous"] is None
    assert p["h2h_line"] == "No meetings yet"
    assert p["record_line"] == "Ada 0–0 · Bob 0–0"
    assert p["a_form_line"] is None
    assert p["b_form_line"] is None


def test_preview_previous_meeting_tie(con):
    w1 = build_week(con)
    ada = make_member(con, "Ada")
    bob = make_member(con, "Bob")
    _submit(con, ada["id"], w1, _five_batters(w1), "Clay Holmes")
    _submit(con, bob["id"], w1, _five_batters(w1), "Clay Holmes")
    _unstarted_week(con, 2, "2026-10-02", 301)
    p = matchups_mod.matchup_preview(con, 2026, 2, ada["id"], bob["id"])
    assert p["previous"]["winner"] == "tie"
    assert p["previous"]["line"] == "Week 1 — Tie 15.78 – 15.78"
    assert p["previous"]["decided"] is None  # no winner, no decided line
    assert p["h2h_line"] == "Tied 0 – 0 · 1 tie"
    assert p["record_line"] == "Ada 0–0–1 · Bob 0–0–1"
    assert p["a_form_line"] == "Last week 15.78 pts · Tied last week"


def test_preview_win_streak_and_all_time(con):
    w1, ada, bob = _week1_ada_beats_bob(con)
    w2 = _final_week2(con)
    _submit(con, ada["id"], w2, _five_batters(w2), "Clay Holmes", week=2)
    _submit(con, bob["id"], w2, _weak_five(w2), "Alec Gamboa", week=2)
    _unstarted_week(con, 3, "2026-10-09", 401)
    p = matchups_mod.matchup_preview(con, 2026, 3, ada["id"], bob["id"])
    assert p["previous"]["week"] == 2  # most recent meeting, not week 1
    assert p["previous"]["line"] == "Week 2 — Ada won 13.78 – 11.40"
    assert p["h2h_line"] == "Ada leads 2 – 0"
    assert p["record_line"] == "Ada 2–0 · Bob 0–2"
    assert p["a_form_line"] == "Last week 13.78 pts · Won 2 straight"
    assert p["b_form_line"] == "Last week 11.40 pts · Lost last week"


def test_preview_no_last_week_data_after_bye(con):
    # 3 members: week 1 pairs Bob vs Cyd, Ada has the bye; week 2
    # pairs Ada vs Cyd. Ada has no completed week, so no form line.
    w1 = build_week(con)
    ada = make_member(con, "Ada")
    bob = make_member(con, "Bob")
    cyd = make_member(con, "Cyd")
    _submit(con, bob["id"], w1, _five_batters(w1), "Clay Holmes")
    _submit(con, cyd["id"], w1, _weak_five(w1), "Alec Gamboa")
    _unstarted_week(con, 2, "2026-10-02", 301)
    p = matchups_mod.matchup_preview(con, 2026, 2, ada["id"], cyd["id"])
    assert p is not None
    assert p["first_meeting"] is True  # never paired before
    assert p["a_form_line"] is None  # bye in week 1 — no form yet
    assert p["b_form_line"] == "Last week 11.40 pts · Lost last week"
    assert p["record_line"] == "Ada 0–0 · Cyd 0–1"


def test_preview_cross_division_pair(con):
    w1 = build_week(con)
    ada = make_member(con, "Ada")  # red, the default
    bob = dbmod.create_member(con, "Bob", division="white")
    _submit(con, ada["id"], w1, _five_batters(w1), "Clay Holmes")
    _submit(con, bob["id"], w1, _weak_five(w1), "Alec Gamboa")
    _unstarted_week(con, 2, "2026-10-02", 301)
    p = matchups_mod.matchup_preview(con, 2026, 2, ada["id"], bob["id"])
    assert p is not None
    assert p["a"]["division"] == "red"
    assert p["b"]["division"] == "white"
    assert p["previous"]["line"] == "Week 1 — Ada won 15.78 – 11.40"


def test_preview_decided_line_batter_led(con):
    # Winner's top pick is a batter (Gamboa the pitcher scores least).
    w1 = build_week(con)
    ada = make_member(con, "Ada")
    bob = make_member(con, "Bob")
    _submit(con, ada["id"], w1, _five_batters(w1), "Alec Gamboa")
    _submit(con, bob["id"], w1, _weak_five(w1), "Alec Gamboa")
    _unstarted_week(con, 2, "2026-10-02", 301)
    p = matchups_mod.matchup_preview(con, 2026, 2, ada["id"], bob["id"])
    assert p["previous"]["a_points"] == 13.40
    assert p["previous"]["decided"] == \
        "Roman Anthony's 3 hits led the way"


def test_preview_invalid_pair_and_week_started(con):
    w1, ada, bob = _week1_ada_beats_bob(con)
    cyd = make_member(con, "Cyd")
    assert matchups_mod.matchup_preview(con, 2026, 1,
                                        ada["id"], cyd["id"]) is None
    assert matchups_mod.week_started(con, 2026, 1) is True
    _unstarted_week(con, 2, "2026-10-02", 301)
    assert matchups_mod.week_started(con, 2026, 2) is False


# ---------------------------------------------------------------- pages

def test_preview_page_renders_history_not_live_detail(con, client):
    w1, ada, bob = _week1_ada_beats_bob(con)
    _unstarted_week(con, 2, "2026-10-02", 301)
    _sign_in(client, ada)
    page = client.get(f"/matchups?open={ada['id']}-{bob['id']}")
    assert page.status_code == 200
    assert "Previous meeting" in page.text
    assert "Week 1 — Ada won 15.78 – 11.40" in page.text
    assert "Ada leads 1 – 0" in page.text
    assert "Ada 1–0 · Bob 0–1" in page.text
    assert "Last week 15.78 pts · Won last week" in page.text
    assert "Winner earns +1 batter pick next week." in page.text
    assert "Close ↑" in page.text
    assert "total points" not in page.text  # live detail absent
    assert "Hidden until first pitch" not in page.text


def test_preview_page_first_meeting(con, client):
    _unstarted_week(con, 1, "2026-09-25", 301)
    ada = make_member(con, "Ada")
    bob = make_member(con, "Bob")
    _sign_in(client, ada)
    page = client.get(f"/matchups?open={ada['id']}-{bob['id']}")
    assert page.status_code == 200
    assert "First meeting." in page.text
    assert "No meetings yet" in page.text
    assert "No completed weeks yet." in page.text
    assert "total points" not in page.text


def test_live_detail_once_week_started(con, client):
    # Same week-1 setup as the detail tests: games started, so the
    # drop-down is the live detail, never the preview.
    w1, ada, bob = _week1_ada_beats_bob(con)
    _sign_in(client, ada)
    page = client.get(f"/matchups?open={ada['id']}-{bob['id']}")
    assert page.status_code == 200
    assert "total points" in page.text
    assert "Ada wins" in page.text
    assert "Previous meeting" not in page.text
