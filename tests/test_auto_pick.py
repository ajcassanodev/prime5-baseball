"""Auto-pick (Nov 24, 2026): the "Auto-pick my lineup" settings
toggle and the poller's random fill from Friday's lineup.

- Toggle off by default; flips via POST /settings (same endpoint as
  "Show my full name"), rejects non-bools.
- The poller fills empty batter slots (b1..b5), the pitcher slot, and
  the 6th batter when the member has a head-to-head grant — uniform
  random draw from players in a published lineup for games that
  haven't started. Auto picks are tagged AUTO and stay editable.
- A member-cleared auto pick is never refilled (decline per slot).
"""
import random
from datetime import datetime, timezone

import pytest

from app import auto_pick as auto_pick_mod
from app import db as dbmod
from app.picks import submit_picks
from conftest import build_week, make_member

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
FUTURE = {"g1_time": "2026-09-25T17:05:00Z",
          "g2_time": "2026-09-25T20:05:00Z",
          "g3_time": "2026-09-25T21:35:00Z"}


def _sign_in(client, member):
    return client.get(f"/submit/{member['pick_token']}")


def _opt_in(con, member):
    dbmod.set_auto_pick(con, member["id"], True)


def _fill(con, season=2026, week=1, seed=7, now=NOW):
    return auto_pick_mod.auto_fill_week(con, season, week, now=now,
                                        rng=random.Random(seed))


def _picks(con, member_id, season=2026, week=1):
    return {p["slot"]: p
            for p in dbmod.picks_for_member_week(con, member_id, season,
                                                 week)}


def _pick_sig(picks):
    """slot -> (mlb_id, game_pk, auto): the meaningful pick content,
    ignoring ids/timestamps."""
    return {s: (p["mlb_id"], p["game_pk"], p["auto"])
            for s, p in picks.items()}


def _auto_rows(page_text):
    """Row buttons (batter/pitcher) carrying an AUTO tag."""
    import re
    rows = re.findall(
        r'<button type="button" class="row (?:batter|pitcher)-row".*?'
        r'</button>', page_text, re.S)
    return [r for r in rows if "tag auto" in r]


# ---------------------------------------------------------------- settings

def test_auto_pick_toggle_defaults_off(con):
    m = make_member(con, "Adam Cassano", email="adam@example.com")
    assert dbmod.member_by_id(con, m["id"])["auto_pick"] == 0


def test_settings_auto_pick_flips_and_persists(con, client):
    m = make_member(con, "Adam Cassano", email="adam@example.com")
    _sign_in(client, m)
    r = client.post("/settings", json={"auto_pick": True})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "auto_pick": True}
    assert dbmod.member_by_id(con, m["id"])["auto_pick"] == 1
    page = client.get("/dashboard")
    assert 'class="switch on" id="autoPickSwitch"' in page.text
    r = client.post("/settings", json={"auto_pick": False})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "auto_pick": False}
    assert dbmod.member_by_id(con, m["id"])["auto_pick"] == 0


def test_settings_accepts_both_keys_at_once(con, client):
    m = make_member(con, "Adam Cassano", email="adam@example.com")
    _sign_in(client, m)
    r = client.post("/settings",
                    json={"show_full_name": True, "auto_pick": True})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "show_full_name": True,
                        "auto_pick": True}
    row = dbmod.member_by_id(con, m["id"])
    assert row["show_full_name"] == 1 and row["auto_pick"] == 1


def test_settings_auto_pick_rejects_non_bool(con, client):
    m = make_member(con, "Adam Cassano", email="adam@example.com")
    _sign_in(client, m)
    for bad in ({"auto_pick": "yes"}, {"auto_pick": 1},
                {"show_full_name": True, "auto_pick": "yes"}):
        r = client.post("/settings", json=bad)
        assert r.status_code == 400
    assert dbmod.member_by_id(con, m["id"])["auto_pick"] == 0


def test_settings_auto_pick_requires_sign_in(client):
    r = client.post("/settings", json={"auto_pick": True},
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/locked"


# ---------------------------------------------------------------- fill basics

def test_fills_empty_batter_and_pitcher_slots(con):
    w = build_week(con, **FUTURE)
    m = make_member(con, "Ada")
    _opt_in(con, m)
    filled = _fill(con)
    assert filled == {"filled": {m["id"]: ["b1", "b2", "b3", "b4", "b5",
                                           "p1"]}}
    picks = _picks(con, m["id"])
    assert set(picks) == {"b1", "b2", "b3", "b4", "b5", "p1"}
    assert all(p["auto"] == 1 for p in picks.values())
    # Every batter is in a published lineup; the pitcher is a starter.
    lineup_batters = {11, 12, 14, 15, 16, 21, 22, 24, 25, 26}
    starters = {201, 202, 203, 204, 206, 207}
    for slot in ("b1", "b2", "b3", "b4", "b5"):
        assert picks[slot]["mlb_id"] in lineup_batters
    assert picks["p1"]["mlb_id"] in starters


def test_toggle_off_fills_nothing(con):
    build_week(con, **FUTURE)
    m = make_member(con, "Ada")  # auto_pick defaults to 0
    assert _fill(con) == {"filled": {}}
    assert _picks(con, m["id"]) == {}


def test_fill_is_idempotent(con):
    build_week(con, **FUTURE)
    m = make_member(con, "Ada")
    _opt_in(con, m)
    first = _fill(con)
    before = _picks(con, m["id"])
    second = _fill(con)
    assert second == {"filled": {}}
    assert _picks(con, m["id"]) == before
    assert first["filled"][m["id"]] == ["b1", "b2", "b3", "b4", "b5", "p1"]


def test_fill_is_deterministic_for_a_seed(con):
    build_week(con, **FUTURE)
    m = make_member(con, "Ada")
    _opt_in(con, m)
    _fill(con, seed=3)
    first = _pick_sig(_picks(con, m["id"]))
    con.execute("DELETE FROM picks")
    con.commit()
    _fill(con, seed=3)
    assert _pick_sig(_picks(con, m["id"])) == first


def test_never_draws_benched_or_relief_players(con):
    """Across many seeds: no DNP (Contreras, Kjerstad — on the roster
    but not in a lineup) and never the reliever as pitcher."""
    build_week(con, **FUTURE)
    m = make_member(con, "Ada")
    _opt_in(con, m)
    for seed in range(10):
        _fill(con, seed=seed)
        picks = _picks(con, m["id"])
        assert all(p["mlb_id"] not in (13, 23) for p in picks.values())
        assert picks["p1"]["mlb_id"] != 205
        con.execute("DELETE FROM picks")
        con.commit()


def test_no_fill_before_lineups_are_published(con):
    build_week(con, **FUTURE)
    m = make_member(con, "Ada")
    _opt_in(con, m)
    con.execute("DELETE FROM lineups")
    con.commit()
    assert _fill(con) == {"filled": {}}
    assert _picks(con, m["id"]) == {}


def test_started_games_are_never_filled_from(con):
    """now is after game 101's first pitch: nothing from game 101,
    fills still happen from 102/103."""
    build_week(con, **FUTURE)
    m = make_member(con, "Ada")
    _opt_in(con, m)
    late = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)
    _fill(con, now=late)
    picks = _picks(con, m["id"])
    assert all(p["game_pk"] in (102, 103) for p in picks.values())
    assert picks  # still filled from the later games


def test_never_overwrites_a_manual_pick(con):
    w = build_week(con, **FUTURE)
    m = make_member(con, "Ada")
    _opt_in(con, m)
    # Two manual batters + a manual pitcher, saved before the fill.
    dbmod.save_pick(con, m["id"], 2026, 1, "b1", "batter",
                    w["batters"]["Roman Anthony"], 101)
    dbmod.save_pick(con, m["id"], 2026, 1, "b2", "batter",
                    w["batters"]["Pete Alonso"], 102)
    dbmod.save_pick(con, m["id"], 2026, 1, "p1", "pitcher",
                    w["pitchers"]["Brendan Beck"], 102)
    filled = _fill(con)
    assert filled == {"filled": {m["id"]: ["b3", "b4", "b5"]}}
    picks = _picks(con, m["id"])
    assert picks["b1"]["mlb_id"] == w["batters"]["Roman Anthony"]
    assert picks["b1"]["auto"] == 0
    assert picks["b2"]["mlb_id"] == w["batters"]["Pete Alonso"]
    assert picks["b2"]["auto"] == 0
    assert picks["p1"]["mlb_id"] == w["pitchers"]["Brendan Beck"]
    assert picks["p1"]["auto"] == 0
    assert all(picks[s]["auto"] == 1 for s in ("b3", "b4", "b5"))


# ---------------------------------------------------------------- sixth batter / grants

def _grant(con, member_id, season=2026, week_earned=1, use_week=2):
    con.execute(
        "INSERT INTO head_to_head_grants"
        " (season, week_earned, member_id, use_week, used)"
        " VALUES (?, ?, ?, ?, 0)",
        (season, week_earned, member_id, use_week))
    con.commit()


def test_fills_sixth_batter_with_grant(con):
    build_week(con, season=2026, week=2, friday_date="2026-10-02",
               g1_time="2026-10-02T17:05:00Z",
               g2_time="2026-10-02T20:05:00Z",
               g3_time="2026-10-02T21:35:00Z")
    m = make_member(con, "Ada")
    _opt_in(con, m)
    _grant(con, m["id"])
    now = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
    filled = _fill(con, week=2, now=now)
    assert filled == {"filled": {m["id"]: ["b1", "b2", "b3", "b4", "b5",
                                           "b6", "p1"]}}
    picks = _picks(con, m["id"], week=2)
    assert picks["b6"]["auto"] == 1


def test_no_sixth_batter_without_grant(con):
    build_week(con, **FUTURE)
    m = make_member(con, "Ada")
    _opt_in(con, m)
    _fill(con)
    assert "b6" not in _picks(con, m["id"])


# ---------------------------------------------------------------- declines

def test_cleared_auto_pick_is_never_refilled(con):
    """The 6th batter is the slot a submit can actually empty: fill
    it, drop it with a 5-batter submit, and the poller must not fill
    it again even though the grant is still available."""
    build_week(con, season=2026, week=2, friday_date="2026-10-02",
               g1_time="2026-10-02T17:05:00Z",
               g2_time="2026-10-02T20:05:00Z",
               g3_time="2026-10-02T21:35:00Z")
    m = make_member(con, "Ada")
    _opt_in(con, m)
    _grant(con, m["id"])
    now = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
    _fill(con, week=2, now=now)
    before = _picks(con, m["id"], week=2)
    assert "b6" in before
    # Resubmit the 5 auto batters + pitcher, dropping the 6th.
    batter_pairs = [(before[s]["mlb_id"], before[s]["game_pk"])
                    for s in ("b1", "b2", "b3", "b4", "b5")]
    submit_picks(con, m["id"], 2026, 2, batter_pairs,
                 before["p1"]["mlb_id"], now=now)
    assert "b6" not in _picks(con, m["id"], week=2)
    assert dbmod.auto_declined_slots(con, m["id"], 2026, 2) == {"b6"}
    _fill(con, week=2, now=now)
    assert "b6" not in _picks(con, m["id"], week=2)


def test_dropping_an_auto_batter_records_its_slot(con):
    build_week(con, **FUTURE)
    m = make_member(con, "Ada")
    _opt_in(con, m)
    _fill(con)
    before = _picks(con, m["id"])
    dropped = before["b2"]
    # Replace the auto b2 with a manual batter from another game —
    # any lineup batter not already in the kept set.
    kept_pairs = [(before[s]["mlb_id"], before[s]["game_pk"])
                  for s in ("b1", "b3", "b4", "b5")]
    pool = [(11, 101), (12, 101), (14, 101), (15, 101), (16, 101),
            (21, 102), (22, 102), (24, 102), (25, 102), (26, 102),
            (14, 103)]
    manual = next(p for p in pool if p not in kept_pairs
                  and p != (dropped["mlb_id"], dropped["game_pk"]))
    batter_pairs = kept_pairs[:1] + [manual] + kept_pairs[1:]
    submit_picks(con, m["id"], 2026, 1, batter_pairs,
                 before["p1"]["mlb_id"], now=NOW)
    assert dbmod.auto_declined_slots(con, m["id"], 2026, 1) == {"b2"}
    # The kept auto picks stay tagged AUTO; the manual one does not.
    after = _picks(con, m["id"])
    assert after["b1"]["auto"] == 1
    assert after["b2"]["auto"] == 0
    assert after["b2"]["mlb_id"] == manual[0]


# ---------------------------------------------------------------- pick-page tag

def test_auto_tag_renders_on_pick_page(con, client):
    w = build_week(con, **FUTURE)
    m = make_member(con, "Ada")
    _opt_in(con, m)
    _fill(con)
    _sign_in(client, m)
    page = client.get("/picks")
    assert page.status_code == 200
    # All six filled picks carry the tag on their rows (the page's
    # inline JS also mentions the tag, so count rows, not substrings).
    assert len(_auto_rows(page.text)) == 6


def test_manual_picks_carry_no_auto_tag(con, client):
    w = build_week(con, **FUTURE)
    m = make_member(con, "Ada")
    dbmod.save_pick(con, m["id"], 2026, 1, "b1", "batter",
                    w["batters"]["Roman Anthony"], 101)
    _sign_in(client, m)
    page = client.get("/picks")
    assert page.status_code == 200
    assert _auto_rows(page.text) == []
