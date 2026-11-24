"""Head-to-head matchups + per-member schedule (Nov 24, 2026).

Weekly: every active member faces one opponent (round-robin); the
winner earns +1 batter pick for the following week (use-it-or-lose-it).
A matchup is scored on the base lineup only — 5 batters + 1 pitcher;
a bonus 6th batter never counts toward it.
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
    """2 hits each = 10 pts of batting."""
    b = w["batters"]
    g = w["batter_game"]
    names = ["Adley Rutschman", "Michael Busch", "Matt Shaw",
             "Seiya Suzuki", "Heliot Ramos"]
    return [(b[n], g[n]) for n in names]


def _submit(con, member_id, w, batter_picks, pitcher_name, extra=False):
    return submit_picks(con, member_id, 2026, 1, batter_picks,
                        w["pitchers"][pitcher_name], extra_batter=extra)


def _grant_count(con, member_id=None):
    sql = "SELECT COUNT(*) FROM head_to_head_grants"
    params = []
    if member_id is not None:
        sql += " WHERE member_id = ?"
        params.append(member_id)
    return con.execute(sql, params).fetchone()[0]


def _build_week2(con, season=2026):
    """A second Friday (Oct 2, 2026) reusing week-1 players, with
    fresh game_pks so week 1's games stay intact."""
    dbmod.ensure_week(con, season, 2, "2026-10-02",
                      label="Week 2 — 2026-10-02", playground=True)
    games = {
        301: {"game_pk": 301, "season": season, "week": 2,
              "friday_date": "2026-10-02",
              "game_date_utc": "2026-10-02T17:05:00Z",
              "status": "Final", "home_team": "BOS", "away_team": "CHC",
              "home_starter_mlb_id": 201, "away_starter_mlb_id": 202},
        302: {"game_pk": 302, "season": season, "week": 2,
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
    # The roster/lineup/stats helpers do not commit; close the
    # transaction so later HTTP calls (which award grants) are not
    # blocked on a database lock.
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


def _week2_five(w2):
    b = w2["batters"]
    g = w2["batter_game"]
    names = ["Roman Anthony", "Adley Rutschman", "Michael Busch",
             "Pete Alonso", "Heliot Ramos"]
    return [(b[n], g[n]) for n in names]


# ---------------------------------------------------------------- pairings

def test_round_robin_deterministic():
    ids = list(range(1, 15))
    assert (matchups_mod.pairings_for_week(2026, 3, ids)
            == matchups_mod.pairings_for_week(2026, 3, ids))


def test_round_robin_covers_every_pair_once():
    ids = list(range(1, 15))
    seen = set()
    appearances = {i: 0 for i in ids}
    for w in range(1, 14):
        pairs, bye = matchups_mod.pairings_for_week(2026, w, ids)
        assert bye is None
        assert len(pairs) == 7
        for a, b in pairs:
            seen.add(frozenset((a, b)))
            appearances[a] += 1
            appearances[b] += 1
    # 14 members -> 91 unique pairs, each member plays 13 rounds.
    assert len(seen) == 91
    assert all(v == 13 for v in appearances.values())
    # Weeks 14-25 repeat rounds 1-12.
    for w in range(14, 26):
        assert (matchups_mod.pairings_for_week(2026, w, ids)
                == matchups_mod.pairings_for_week(2026, w - 13, ids))


def test_round_robin_odd_count_bye():
    ids = [1, 2, 3]
    seen, byes = set(), []
    for w in range(1, 4):
        pairs, bye = matchups_mod.pairings_for_week(2026, w, ids)
        assert len(pairs) == 1
        assert bye is not None
        seen.add(frozenset(pairs[0]))
        byes.append(bye)
    assert seen == {frozenset((1, 2)), frozenset((1, 3)),
                    frozenset((2, 3))}
    assert sorted(byes) == [1, 2, 3]  # everyone sits out exactly once


def test_pairings_fewer_than_two_members():
    assert matchups_mod.pairings_for_week(2026, 1, []) == ([], None)
    assert matchups_mod.pairings_for_week(2026, 1, [7]) == ([], 7)


# ---------------------------------------------------------------- scoring

def test_matchup_points_excludes_sixth_batter(con):
    w = build_week(con)
    m = make_member(con, "Ada")
    six = _five_batters(w) + [(w["batters"]["Matt Shaw"],
                               w["batter_game"]["Matt Shaw"])]
    _submit(con, m["id"], w, six, "Clay Holmes", extra=True)
    # 5 batters (12) + Holmes 3.78 = 15.78; Shaw's 2 hits as the 6th
    # batter must not count toward the matchup...
    assert matchups_mod.matchup_points(con, m["id"], 2026, 1) == 15.78
    # ...but they do count toward overall standings.
    from app.scoring import week_points
    picks = dbmod.picks_for_member_week(con, m["id"], 2026, 1)
    stats = dbmod.stats_for_week(con, 2026, 1)
    assert week_points(
        [{"kind": p["kind"], "mlb_id": p["mlb_id"], "game_pk": p["game_pk"]}
         for p in picks], stats) == 17.78


def test_week_not_final_until_games_done(con):
    w = build_week(con)
    assert matchups_mod.week_final(con, 2026, 1) is True
    con.execute("UPDATE games SET status = 'Scheduled'")
    con.commit()
    assert matchups_mod.week_final(con, 2026, 1) is False
    # No games at all -> not final either (delete dependents first).
    for t in ("picks", "player_game_stats", "lineups", "rosters",
              "games"):
        con.execute(f"DELETE FROM {t}")
    con.commit()
    assert matchups_mod.week_final(con, 2026, 1) is False


# ---------------------------------------------------------------- grants

def _two_members_final_week(con):
    """Ada beats Bob in the (final) week-1 matchup; returns (w, ada, bob)."""
    w = build_week(con)
    ada = make_member(con, "Ada")
    bob = make_member(con, "Bob")
    _submit(con, ada["id"], w, _five_batters(w), "Clay Holmes")
    _submit(con, bob["id"], w, _weak_five(w), "Alec Gamboa")
    return w, ada, bob


def test_grant_award_idempotent(con):
    w, ada, bob = _two_members_final_week(con)
    matchups_mod.award_head_to_head_grants(con, 2026, 1)
    matchups_mod.award_head_to_head_grants(con, 2026, 1)
    assert _grant_count(con) == 1
    assert _grant_count(con, ada["id"]) == 1
    assert _grant_count(con, bob["id"]) == 0
    grant = dbmod.available_grant(con, ada["id"], 2026, 2)
    assert grant is not None
    assert grant["week_earned"] == 1
    assert grant["use_week"] == 2


def test_grant_no_grant_on_tie(con):
    w = build_week(con)
    ada = make_member(con, "Ada")
    bob = make_member(con, "Bob")
    _submit(con, ada["id"], w, _five_batters(w), "Clay Holmes")
    _submit(con, bob["id"], w, _five_batters(w), "Clay Holmes")
    matchups_mod.award_head_to_head_grants(con, 2026, 1)
    assert _grant_count(con) == 0


def test_grant_no_grant_on_bye(con):
    w = build_week(con)
    ada = make_member(con, "Ada")
    bob = make_member(con, "Bob")
    cyd = make_member(con, "Cyd")
    # Week 1 pairs Bob vs Cyd; Ada sits out (odd count, circle method).
    _submit(con, bob["id"], w, _five_batters(w), "Clay Holmes")
    _submit(con, cyd["id"], w, _weak_five(w), "Alec Gamboa")
    matchups_mod.award_head_to_head_grants(con, 2026, 1)
    assert _grant_count(con) == 1
    assert _grant_count(con, bob["id"]) == 1
    assert _grant_count(con, ada["id"]) == 0  # bye week earns nothing
    assert _grant_count(con, cyd["id"]) == 0


def test_grant_not_awarded_before_final(con):
    w = build_week(con)
    ada = make_member(con, "Ada")
    bob = make_member(con, "Bob")
    _submit(con, ada["id"], w, _five_batters(w), "Clay Holmes")
    _submit(con, bob["id"], w, _weak_five(w), "Alec Gamboa")
    con.execute("UPDATE games SET status = 'Scheduled'")
    con.commit()
    matchups_mod.award_head_to_head_grants(con, 2026, 1)
    assert _grant_count(con) == 0


# ---------------------------------------------------------------- 6th batter

def _six_batter_post(w2, pitcher_id):
    b = _week2_five(w2) + [(w2["batters"]["Matt Shaw"],
                            w2["batter_game"]["Matt Shaw"])]
    data = {"pitcher": pitcher_id}
    for i, (mlb_id, game_pk) in enumerate(b, start=1):
        data[f"batter_{i}"] = mlb_id
        data[f"batter_{i}_game"] = game_pk
    return data


def test_pick_page_rejects_sixth_without_grant(con, client):
    w = build_week(con)
    m = make_member(con, "Ada")
    _sign_in(client, m)
    r = client.post("/picks", data=_six_batter_post(
        {"batters": w["batters"], "batter_game": w["batter_game"]},
        w["pitchers"]["Clay Holmes"]))
    assert r.status_code == 200
    assert "exactly 5 batters" in r.text


def test_grant_flow_end_to_end(con, client):
    w, ada, bob = _two_members_final_week(con)
    w2 = _build_week2(con)
    _sign_in(client, ada)
    # Opening the week-2 pick page awards last week's grant lazily.
    page = client.get("/picks")
    assert page.status_code == 200
    assert "Bonus pick" in page.text
    assert "Week 1 matchup" in page.text
    r = client.post("/picks", data=_six_batter_post(
        w2, w2["pitchers"]["Clay Holmes"]), follow_redirects=False)
    assert r.status_code == 303  # -> /dashboard
    picks = dbmod.picks_for_member_week(con, ada["id"], 2026, 2)
    assert {p["slot"] for p in picks} == {"b1", "b2", "b3", "b4", "b5",
                                          "b6", "p1"}
    # The grant is spent.
    assert dbmod.available_grant(con, ada["id"], 2026, 2) is None


def test_expired_grant_not_usable(con, client):
    w = build_week(con)
    _build_week2(con)  # current week is now 2
    ada = make_member(con, "Ada")
    # A grant usable only in week 1 — last week, so expired.
    con.execute(
        "INSERT INTO head_to_head_grants (season, week_earned, member_id,"
        " use_week, used) VALUES (2026, 1, ?, 1, 0)", (ada["id"],))
    con.commit()
    _sign_in(client, ada)
    page = client.get("/picks")
    assert "Bonus pick" not in page.text
    w2 = {"batters": {"Roman Anthony": 11, "Adley Rutschman": 12,
                      "Michael Busch": 14, "Pete Alonso": 21,
                      "Heliot Ramos": 24, "Matt Shaw": 15},
          "batter_game": {"Roman Anthony": 301, "Adley Rutschman": 301,
                          "Michael Busch": 301, "Pete Alonso": 302,
                          "Heliot Ramos": 302, "Matt Shaw": 301}}
    r = client.post("/picks", data=_six_batter_post(w2, 202))
    assert r.status_code == 200
    assert "exactly 5 batters" in r.text


def test_drop_sixth_batter_on_five_submit(con, client):
    w, ada, bob = _two_members_final_week(con)
    w2 = _build_week2(con)
    _sign_in(client, ada)
    client.get("/picks")  # awards the week-1 grant lazily
    r = client.post("/picks", data=_six_batter_post(
        w2, w2["pitchers"]["Clay Holmes"]), follow_redirects=False)
    assert r.status_code == 303
    assert any(p["slot"] == "b6" for p in
               dbmod.picks_for_member_week(con, ada["id"], 2026, 2))
    # Resubmit with only 5 batters: the leftover 6th goes away.
    five = _week2_five(w2)
    data = {"pitcher": w2["pitchers"]["Clay Holmes"]}
    for i, (mlb_id, game_pk) in enumerate(five, start=1):
        data[f"batter_{i}"] = mlb_id
        data[f"batter_{i}_game"] = game_pk
    r = client.post("/picks", data=data, follow_redirects=False)
    assert r.status_code == 303
    slots = {p["slot"] for p in
             dbmod.picks_for_member_week(con, ada["id"], 2026, 2)}
    assert slots == {"b1", "b2", "b3", "b4", "b5", "p1"}


# ---------------------------------------------------------------- pages

def test_matchups_page_renders_and_redirects(con, client):
    w, ada, bob = _two_members_final_week(con)
    assert client.get("/matchups", follow_redirects=False).status_code == 303
    _sign_in(client, ada)
    page = client.get("/matchups")
    assert page.status_code == 200
    assert "Head-to-Head" in page.text
    assert "Ada" in page.text
    assert "Bob" in page.text
    assert "+1 pick" in page.text  # Ada won the final week-1 matchup
    assert "1-0" in page.text  # records table
    assert 'href="/schedule"' in page.text


def test_matchups_page_live_before_final(con, client):
    w = build_week(con)
    ada = make_member(con, "Ada")
    bob = make_member(con, "Bob")
    _submit(con, ada["id"], w, _five_batters(w), "Clay Holmes")
    _submit(con, bob["id"], w, _weak_five(w), "Alec Gamboa")
    con.execute("UPDATE games SET status = 'Scheduled'")
    con.commit()
    _sign_in(client, ada)
    page = client.get("/matchups")
    assert page.status_code == 200
    assert "Live" in page.text
    assert "+1 pick" not in page.text
    assert _grant_count(con) == 0


def test_schedule_page_renders_and_redirects(con, client):
    w, ada, bob = _two_members_final_week(con)
    assert client.get("/schedule", follow_redirects=False).status_code == 303
    _sign_in(client, ada)
    page = client.get("/schedule")
    assert page.status_code == 200
    assert "My Schedule" in page.text
    assert page.text.count("Wk ") == 25
    assert "Fri Sep 25" in page.text
    assert "W</b>" in page.text  # week-1 result vs Bob
    assert "15.78" in page.text
    assert "+1 pick" in page.text
    assert "Playoffs:" in page.text
    assert "All 25 weeks" in page.text


def test_dashboard_card_and_nav(con, client):
    w, ada, bob = _two_members_final_week(con)
    _sign_in(client, ada)
    dash = client.get("/dashboard")
    assert dash.status_code == 200
    assert "HEAD-TO-HEAD" in dash.text
    assert "View all matchups" in dash.text
    assert "Bob" in dash.text  # current opponent
    assert 'href="/matchups"' in dash.text
    assert 'href="/schedule"' in dash.text


def test_empty_state_fewer_than_two_members(con, client):
    build_week(con)
    ada = make_member(con, "Ada")
    _sign_in(client, ada)
    for path in ("/matchups", "/schedule"):
        page = client.get(path)
        assert page.status_code == 200
        assert "starts once more members join" in page.text
    dash = client.get("/dashboard")
    assert "starts once more members join" in dash.text
