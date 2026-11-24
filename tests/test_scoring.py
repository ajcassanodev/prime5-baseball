"""Scoring rules: 1 pt per hit; pitcher 0.01/pitch + 1/K."""
import pytest

from app.scoring import batter_points, pick_points, pitcher_points, week_points


def test_batter_points():
    assert batter_points(3) == 3.0
    assert batter_points(0) == 0.0
    assert batter_points(None) == 0.0


def test_pitcher_points_verified_examples():
    # Real Sep 25, 2026 lines, verified against the MLB Stats API.
    assert pitcher_points(78, 3) == 3.78   # Clay Holmes
    assert pitcher_points(97, 8) == 8.97   # Brendan Beck
    assert pitcher_points(40, 1) == 1.40   # Alec Gamboa
    assert pitcher_points(55, 4) == 4.55   # Trevor Rogers


def test_pitcher_points_rounding():
    assert pitcher_points(33, 0) == 0.33
    assert pitcher_points(0, 0) == 0.0


def test_pick_points_missing_stats_scores_zero():
    assert pick_points("batter", None) == 0.0
    assert pick_points("pitcher", None) == 0.0
    assert pick_points("batter", {"hits": 2}) == 2.0
    assert pick_points("pitcher", {"pitches": 91, "strikeouts": 6}) == 6.91


def test_pick_points_unknown_kind():
    with pytest.raises(ValueError):
        pick_points("catcher", {"hits": 1})


def test_week_points_mixed_picks():
    picks = [
        {"kind": "batter", "mlb_id": 11, "game_pk": 101},
        {"kind": "batter", "mlb_id": 12, "game_pk": 101},
        {"kind": "batter", "mlb_id": 13, "game_pk": 101},  # DNP, no stats
        {"kind": "pitcher", "mlb_id": 202, "game_pk": 101},
    ]
    stats = {(101, 11): {"hits": 3}, (101, 12): {"hits": 2},
             (101, 202): {"pitches": 78, "strikeouts": 3}}
    assert week_points(picks, stats) == 3.0 + 2.0 + 0.0 + 3.78


def test_week_points_empty():
    assert week_points([], {}) == 0.0
