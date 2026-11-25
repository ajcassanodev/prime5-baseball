"""Pick-help form (Nov 25, 2026): probability formula v1, window
aggregation, streaks, and the Form guide selection."""
import pytest

from app import form_guide as fg


# ---------------------------------------------------------------- batter probability

def test_batter_hit_prob_blends_season_and_recent():
    # h = 0.6*0.280 + 0.4*0.300 = 0.288; factor = 1 + 0.70*0.02 = 1.014;
    # P = 1 - (1 - 0.288*1.014)^4 = 74.9% -> 75.
    assert fg.batter_hit_prob(0.280, 20, 6, 3.50) == 75


def test_batter_hit_prob_small_recent_window_uses_season_only():
    # 4 AB is below the 10-AB minimum, so the 2-for-4 week is ignored:
    # h = 0.250, factor 1.0 -> 1 - 0.75^4 = 68.4% -> 68.
    assert fg.batter_hit_prob(0.250, 4, 2, 4.20) == 68


def test_batter_hit_prob_bad_opposing_starter_helps():
    # h = 0.6*0.300 + 0.4*0.200 = 0.260; ERA 5.50 -> factor 0.974.
    assert fg.batter_hit_prob(0.300, 25, 5, 5.50) == 69


def test_batter_hit_prob_ace_opposing_starter_hurts():
    # h = 0.6*0.200 + 0.4*0.100 = 0.160; ERA 2.00 -> factor 1.044.
    assert fg.batter_hit_prob(0.200, 30, 3, 2.00) == 52


def test_batter_hit_prob_no_opp_era_is_neutral():
    assert fg.batter_hit_prob(0.280, 20, 6, None) == 74


def test_batter_hit_prob_factor_is_clamped():
    # ERA 1.00 would give factor 1.064 without the 1.05 cap;
    # ERA 7.00 would give 0.944 without the 0.95 floor.
    assert fg.batter_hit_prob(0.280, 20, 6, 1.00) == fg.batter_hit_prob(
        0.280, 20, 6, 0.00)
    assert fg.batter_hit_prob(0.280, 20, 6, 7.00) == fg.batter_hit_prob(
        0.280, 20, 6, 9.00)


def test_batter_hit_prob_clamps_to_5_and_95():
    assert fg.batter_hit_prob(0.010, 20, 0, 4.20) == 5
    assert fg.batter_hit_prob(0.550, 20, 11, 4.20) == 95


def test_batter_hit_prob_needs_a_season_average():
    assert fg.batter_hit_prob(None, 20, 6, 3.50) is None
    # A 0.000 season average is real data, not missing data: with a
    # hitless recent window the blended rate is 0, so the
    # probability is the 5% floor — but a hot recent week still
    # lifts it (0.6*0 + 0.4*0.300 = 0.120 -> 40%).
    assert fg.batter_hit_prob(0.0, 20, 0, 4.20) == 5
    assert fg.batter_hit_prob(0.0, 20, 6, 3.50) == 40


# ---------------------------------------------------------------- pitcher probability

def test_pitcher_five_k_prob_known_lambdas():
    # lambda = K/9 * IP-per-start / 9.
    assert fg.pitcher_five_k_prob(9.0, 6.0) == 71   # lambda 6.0
    assert fg.pitcher_five_k_prob(10.5, 5.2) == 72  # lambda 6.07
    assert fg.pitcher_five_k_prob(6.0, 7.0) == 50   # lambda 4.67
    assert fg.pitcher_five_k_prob(12.0, 6.5) == 93  # lambda 8.67
    assert fg.pitcher_five_k_prob(7.5, 5.0) == 40   # lambda 4.17


def test_pitcher_five_k_prob_needs_inputs():
    assert fg.pitcher_five_k_prob(None, 6.0) is None
    assert fg.pitcher_five_k_prob(9.0, None) is None
    assert fg.pitcher_five_k_prob(0.0, 6.0) is None


def test_k_per_9_and_ip_per_start():
    # 78 K in 101.1 innings (304 outs): K/9 = 78*27/304 = 6.93.
    assert fg.k_per_9(78, 304) == pytest.approx(6.928, abs=0.01)
    assert fg.k_per_9(78, 0) is None
    # 101.1 innings is 101 innings + 1 out = 101.33 innings; over
    # 18 starts that is 5.63 IP per start.
    assert fg.ip_per_start(304, 18) == pytest.approx(5.630, abs=0.01)
    assert fg.ip_per_start(304, 0) is None
    # A spot starter's relief innings must not inflate the rate:
    # 63.1 innings over 2 starts is 31.7 raw, capped at 7.0.
    assert fg.ip_per_start(190, 2) == 7.0


# ---------------------------------------------------------------- window aggregation

def _box(mlb_id, ab, hits, pitches=0, k=0, started=False):
    return {"mlb_id": mlb_id, "at_bats": ab, "hits": hits, "hr": 0,
            "rbi": 0, "pitches": pitches, "strikeouts": k}


def test_aggregate_window_and_streak():
    games = [
        {"date": "2026-09-18", "datetime": "2026-09-18T23:10:00Z",
         "boxscore": {"starters": [], "stats": [
             _box(11, 4, 2), _box(12, 4, 0)]}},
        {"date": "2026-09-21", "datetime": "2026-09-21T23:10:00Z",
         "boxscore": {"starters": [], "stats": [
             _box(11, 4, 1), _box(12, 3, 1)]}},
        {"date": "2026-09-22", "datetime": "2026-09-22T23:10:00Z",
         "boxscore": {"starters": [], "stats": [
             _box(11, 4, 0), _box(12, 4, 2)]}},
        {"date": "2026-09-23", "datetime": "2026-09-23T23:10:00Z",
         "boxscore": {"starters": [], "stats": [
             _box(11, 4, 3), _box(12, 4, 1)]}},
    ]
    agg = fg.aggregate_window(games)
    totals = fg.hitting_totals(agg[11]["hitting"])
    assert totals == {"hits": 6, "at_bats": 16, "streak": 1}
    # 12 hit in each of the last three games (2, 1) after an 0-for-4.
    totals12 = fg.hitting_totals(agg[12]["hitting"])
    assert totals12 == {"hits": 4, "at_bats": 15, "streak": 3}


def test_streak_skips_no_at_bat_appearances():
    log = [
        {"date": "2026-09-21", "at_bats": 4, "hits": 1},
        {"date": "2026-09-22", "at_bats": 0, "hits": 0},  # pinch-run
        {"date": "2026-09-23", "at_bats": 4, "hits": 2},
    ]
    assert fg.hitting_totals(log)["streak"] == 2


def test_last_start_picks_most_recent_start():
    log = [
        {"date": "2026-09-19", "pitches": 88, "strikeouts": 5,
         "started": True},
        {"date": "2026-09-24", "pitches": 12, "strikeouts": 1,
         "started": False},  # relief outing after the start
        {"date": "2026-09-25", "pitches": 95, "strikeouts": 7,
         "started": True},
    ]
    assert fg.last_start(log) == {"pitches": 95, "strikeouts": 7}
    assert fg.last_start([log[1]]) is None


def test_batter_form_row_and_line():
    row = fg.batter_form_row(
        11, {"hitting": [{"date": "2026-09-23", "at_bats": 4, "hits": 2},
                         {"date": "2026-09-24", "at_bats": 4, "hits": 1}],
               "pitching": []},
        {"avg": 0.280, "at_bats": 400, "hits": 112}, 3.50)
    assert row["hits_last_7d"] == 3
    assert row["streak"] == 2
    # 8 AB is below the 10-AB minimum: h = 0.280 season only,
    # factor 1.014 (ERA 3.50) -> 1 - (1 - 0.280*1.014)^4 = 73.7% -> 74.
    assert row["hit_prob_pct"] == 74
    assert fg.batter_form_line(row) == "3 hits last 7 days · 2-game hit streak"
    assert fg.batter_form_line({**row, "streak": 0}) == "3 hits last 7 days"
    assert fg.batter_form_line(None) == ""


def test_pitcher_form_row_and_line():
    row = fg.pitcher_form_row(
        201, {"hitting": [], "pitching": [
            {"date": "2026-09-20", "pitches": 91, "strikeouts": 6,
             "started": True}]},
        {"era": 3.29, "strikeouts": 78, "ip_outs": 304,
         "games_started": 18})
    assert row["last_start_pitches"] == 91
    assert row["last_start_k"] == 6
    assert row["five_k_prob_pct"] == fg.pitcher_five_k_prob(
        fg.k_per_9(78, 304), fg.ip_per_start(304, 18))
    assert fg.pitcher_form_line(row) == "Last start: 91 pitches · 6 K"
    assert fg.pitcher_form_line({**row, "last_start_pitches": None}) == ""


# ---------------------------------------------------------------- guide selection

def _row(mlb_id, name, team, is_pitcher, hits=0, streak=0, prob=None,
         five_k=None, ls_p=None, ls_k=None):
    return {"mlb_id": mlb_id, "name": name, "team": team,
            "is_pitcher": is_pitcher, "hits_last_7d": hits,
            "streak": streak, "hit_prob_pct": prob,
            "five_k_prob_pct": five_k, "last_start_pitches": ls_p,
            "last_start_k": ls_k}


def test_build_guide_picks_top_five_two_and_three():
    rows = [
        _row(11, "Roman Anthony", "BOS", 0, hits=9, streak=5, prob=74),
        _row(12, "Adley Rutschman", "BOS", 0, hits=9, streak=5, prob=74),
        _row(14, "Michael Busch", "CHC", 0, hits=7, streak=3, prob=68),
        _row(24, "Heliot Ramos", "NYY", 0, hits=6, streak=6, prob=71),
        _row(15, "Matt Shaw", "CHC", 0, hits=6, streak=4, prob=66),
        _row(21, "Pete Alonso", "BAL", 0, hits=5, streak=0, prob=61),
        _row(22, "Coby Mayo", "BAL", 0, hits=2, streak=0, prob=55),
        _row(201, "Alec Gamboa", "BOS", 1, five_k=41, ls_p=40, ls_k=1),
        _row(202, "Clay Holmes", "CHC", 1, five_k=62, ls_p=78, ls_k=3),
        _row(203, "Brendan Beck", "NYY", 1, five_k=71, ls_p=97, ls_k=8),
    ]
    guide = fg.build_guide(rows, {11, 12, 14, 24, 15, 21, 22},
                           {201, 202, 203})
    # Top 5 by hits, ties by name (Rutschman before Anthony? No —
    # both 9 hits: 'Adley Rutschman' < 'Roman Anthony', so Rutschman
    # first).
    assert [r["name"] for r in guide["hottest"]] == [
        "Adley Rutschman", "Roman Anthony", "Michael Busch",
        "Heliot Ramos", "Matt Shaw"]
    # Top 2 starters by 5+ K probability.
    assert [r["name"] for r in guide["starters"]] == [
        "Brendan Beck", "Clay Holmes"]
    # Top 3 streaks, streak > 0 only.
    assert [r["name"] for r in guide["streaks"]] == [
        "Heliot Ramos", "Adley Rutschman", "Roman Anthony"]
    assert guide["streaks"][0]["short"] == "Ramos"
    assert "Hit %" in guide["note"]


def test_build_guide_skips_players_not_on_friday():
    rows = [_row(99, "Not Playing", "XXX", 0, hits=12, streak=9, prob=90)]
    guide = fg.build_guide(rows, set(), set())
    assert guide["hottest"] == []
    assert guide["streaks"] == []
