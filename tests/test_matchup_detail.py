"""Matchup detail as an animated drop-down on /matchups (Nov 24, 2026).

Clicking a pairing row expands its detail in place (?open=<a>-<b>);
clicking again collapses it. The reveal rule is enforced server-side:
a pick is visible only when it belongs to the viewer or its game has
started — a hidden pick's name never appears in the HTML at all.
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


def _submit(con, member_id, w, batter_picks, pitcher_name, extra=False):
    return submit_picks(con, member_id, 2026, 1, batter_picks,
                        w["pitchers"][pitcher_name], extra_batter=extra)


def _two_members_final_week(con):
    """Ada beats Bob in the (final) week-1 matchup; returns (w, ada, bob)."""
    w = build_week(con)
    ada = make_member(con, "Ada")
    bob = make_member(con, "Bob")
    _submit(con, ada["id"], w, _five_batters(w), "Clay Holmes")
    _submit(con, bob["id"], w, _weak_five(w), "Alec Gamboa")
    return w, ada, bob


def _open_url(ada, bob):
    return f"/matchups?open={ada['id']}-{bob['id']}"


def _make_game_unstarted(con, game_pk=102):
    """Push a game's first pitch into the future so its picks are
    still hidden; the week stops being final."""
    con.execute(
        "UPDATE games SET game_date_utc = '2030-09-25T20:05:00Z',"
        " status = 'Scheduled' WHERE game_pk = ?", (game_pk,))
    con.commit()


def _make_game_started(con, game_pk=102):
    con.execute(
        "UPDATE games SET game_date_utc = '2026-09-25T20:05:00Z',"
        " status = 'Final' WHERE game_pk = ?", (game_pk,))
    con.commit()


# ---------------------------------------------------------------- pages

def test_detail_renders_and_redirects(con, client):
    w, ada, bob = _two_members_final_week(con)
    url = _open_url(ada, bob)
    assert client.get(url, follow_redirects=False).status_code == 303
    _sign_in(client, ada)
    page = client.get(url)
    assert page.status_code == 200
    assert "Ada" in page.text
    assert "Bob" in page.text
    assert "15.78" in page.text  # Ada's revealed total
    assert "11.40" in page.text  # Bob's revealed total
    assert "Ada wins" in page.text  # final verdict
    assert page.text.count("Pete Alonso") == 1  # Ada's pick only
    assert page.text.count("Heliot Ramos") == 2  # both picked him
    assert "Hidden until first pitch" not in page.text  # all started
    assert "Close ↑" in page.text  # collapse link in the detail


def test_list_rows_link_to_open_urls(con, client):
    w, ada, bob = _two_members_final_week(con)
    _sign_in(client, ada)
    page = client.get("/matchups")
    assert page.status_code == 200
    assert f'href="/matchups?open={ada["id"]}-{bob["id"]}"' in page.text
    assert "h2h-detail-wrap" not in page.text  # nothing open


def test_own_picks_always_visible(con, client):
    w, ada, bob = _two_members_final_week(con)
    _make_game_unstarted(con, 102)
    _sign_in(client, ada)
    page = client.get(_open_url(ada, bob))
    assert page.status_code == 200
    # Ada's own picks in the unstarted 4:05 PM game are visible to her.
    assert "Pete Alonso" in page.text
    assert page.text.count("Heliot Ramos") == 1  # Ada's only; Bob's hidden
    assert "15.78" in page.text  # her total counts all her picks
    assert "Ada leads by 6.38" in page.text  # live verdict vs 9.40


def test_opponent_unstarted_hidden_then_revealed(con, client):
    w, ada, bob = _two_members_final_week(con)
    _make_game_unstarted(con, 102)
    _sign_in(client, ada)
    page = client.get(_open_url(ada, bob))
    assert page.text.count("Heliot Ramos") == 1  # Bob's is hidden
    assert page.text.count("Hidden until first pitch") == 1
    assert "4:05 PM game" in page.text  # the hidden row names its game
    assert "9.40" in page.text  # Bob's revealed-only total
    # Once the game has started, the pick reveals with its points.
    _make_game_started(con, 102)
    page = client.get(_open_url(ada, bob))
    assert page.text.count("Heliot Ramos") == 2
    assert "Hidden until first pitch" not in page.text
    assert "11.40" in page.text
    assert "Ada wins" in page.text  # final again


def test_spectator_hides_both_sides(con, client):
    w, ada, bob = _two_members_final_week(con)
    cyd = make_member(con, "Cyd")
    make_member(con, "Dan")
    # 4 members, week 1 pairs Ada vs Dan and Bob vs Cyd; Ada spectates
    # the Bob vs Cyd matchup (she is in neither side).
    _submit(con, cyd["id"], w, _five_batters(w), "Clay Holmes")
    _make_game_unstarted(con, 102)
    _sign_in(client, ada)
    page = client.get(f"/matchups?open={bob['id']}-{cyd['id']}")
    assert page.status_code == 200
    # Both sides' 4:05 PM picks stay hidden from a spectator.
    assert "Pete Alonso" not in page.text
    assert "Heliot Ramos" not in page.text
    assert page.text.count("Hidden until first pitch") == 3
    assert "10.78" in page.text  # Cyd's revealed total (7 + Holmes 3.78)
    assert "9.40" in page.text  # Bob's revealed total


def test_invalid_open_param_renders_list(con, client):
    w, ada, bob = _two_members_final_week(con)
    _sign_in(client, ada)
    for bad in (f"/matchups?open=99-100", "/matchups?open=abc",
                f"/matchups?open={ada['id']}-{ada['id']}",
                "/matchups?open=1-2-3", "/matchups?open="):
        page = client.get(bad)
        assert page.status_code == 200
        assert "Head-to-Head" in page.text
        assert "h2h-detail-wrap" not in page.text  # nothing open
    # A real pair of members who aren't matched this week: also ignored.
    cyd = make_member(con, "Cyd")
    page = client.get(f"/matchups?open={ada['id']}-{cyd['id']}")
    assert page.status_code == 200
    assert "h2h-detail-wrap" not in page.text


# ---------------------------------------------------------------- helper units

def test_matchup_detail_totals_and_invalid_pair(con):
    w, ada, bob = _two_members_final_week(con)
    _make_game_unstarted(con, 102)
    d = matchups_mod.matchup_detail(con, ada["id"], 2026, 1,
                                    ada["id"], bob["id"])
    assert d is not None
    assert d["a_total"] == 15.78  # viewer's own picks all count
    assert d["b_total"] == 9.40  # opponent's revealed picks only
    assert d["final"] is False
    assert d["verdict"] == "Ada leads by 6.38"
    # Hidden rows carry no name at all.
    hidden = [p for p in d["b_picks"] if not p["visible"]]
    assert len(hidden) == 1
    assert hidden[0]["name"] is None
    assert hidden[0]["slot"] == "b5"
    # A pair that isn't matched that week -> None.
    cyd = make_member(con, "Cyd")
    assert matchups_mod.matchup_detail(con, ada["id"], 2026, 1,
                                       ada["id"], cyd["id"]) is None
    assert matchups_mod.matchup_detail(con, ada["id"], 2026, 1,
                                       99, 100) is None


def test_visible_picks_reveal_rule(con):
    w, ada, bob = _two_members_final_week(con)
    _make_game_unstarted(con, 102)
    # Viewer sees her own unstarted picks...
    mine = matchups_mod.visible_picks(con, ada["id"], ada["id"], 2026, 1)
    assert all(p["visible"] for p in mine)
    assert [p["name"] for p in mine if p["slot"] == "b4"] == ["Pete Alonso"]
    # ...but not her opponent's.
    theirs = matchups_mod.visible_picks(con, ada["id"], bob["id"], 2026, 1)
    hidden = [p for p in theirs if not p["visible"]]
    assert len(hidden) == 1
    assert hidden[0]["name"] is None
    assert hidden[0]["game_label"] == "4:05 PM"
