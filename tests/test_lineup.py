"""Lineup status: green dot (in the starting nine / starting pitcher)
vs gray dot (bench / reliever / not announced)."""
import pytest

from app import lineup as lineup_mod
from conftest import build_week


def test_batter_in_batting_order():
    assert lineup_mod.batter_lineup_status(11, [11, 12, 14]) == "in"
    assert lineup_mod.batter_lineup_status(13, [11, 12, 14]) == "out"


def test_pitcher_starter_vs_reliever():
    assert lineup_mod.pitcher_lineup_status(202, [201, 202]) == "in"
    assert lineup_mod.pitcher_lineup_status(205, [201, 202]) == "out"


def test_lineup_status_dispatch():
    game = {"batting_order": [11, 12], "starters": [201, 202]}
    assert lineup_mod.lineup_status("batter", 11, game) == "in"
    assert lineup_mod.lineup_status("batter", 13, game) == "out"
    assert lineup_mod.lineup_status("pitcher", 202, game) == "in"
    assert lineup_mod.lineup_status("pitcher", 205, game) == "out"
    with pytest.raises(ValueError):
        lineup_mod.lineup_status("coach", 11, game)


def test_opposing_starter():
    game = {"home_team": "BOS", "away_team": "CHC",
            "home_starter_mlb_id": 201, "home_starter_name": "Alec Gamboa",
            "away_starter_mlb_id": 202, "away_starter_name": "Clay Holmes"}
    assert lineup_mod.opposing_starter(game, "BOS") == {
        "mlb_id": 202, "name": "Clay Holmes"}
    assert lineup_mod.opposing_starter(game, "CHC") == {
        "mlb_id": 201, "name": "Alec Gamboa"}
    assert lineup_mod.opposing_starter(game, "NYY") is None


def test_opposing_starter_not_announced_yet():
    game = {"home_team": "BOS", "away_team": "CHC",
            "home_starter_mlb_id": None, "home_starter_name": None,
            "away_starter_mlb_id": None, "away_starter_name": None}
    assert lineup_mod.opposing_starter(game, "BOS") is None


def test_game_has_lineup():
    assert lineup_mod.game_has_lineup({"batting_order": [11], "starters": []})
    assert lineup_mod.game_has_lineup({"batting_order": [], "starters": [201]})
    assert not lineup_mod.game_has_lineup({"batting_order": [], "starters": []})


def test_db_lineup_for_game(con):
    build_week(con)
    from app import db as dbmod
    lineup = dbmod.lineup_for_game(con, 101)
    # Both teams' orders share numbers 1-9, so only membership is asserted.
    assert set(lineup["batting_order"]) == {11, 12, 14, 15, 16}
    assert set(lineup["starters"]) == {201, 202}
    # DNP bench players are on the roster but not in the lineup.
    roster_ids = {p["mlb_id"] for p in dbmod.players_for_game(con, 101)}
    assert 13 in roster_ids  # Willson Contreras, DNP
    assert lineup_mod.batter_lineup_status(13, lineup["batting_order"]) == "out"


def test_schedule_announced_lineup_counts_as_in(con):
    # Pre-game: the schedule announces the nine but with no batting
    # order numbers (order None). They still count as in the lineup.
    build_week(con)
    from app import db as dbmod
    dbmod.set_lineup(con, 101, "batter", 13, None)  # Contreras announced
    lineup = dbmod.lineup_for_game(con, 101)
    assert 13 in lineup["batting_order"]
    assert lineup_mod.batter_lineup_status(13, lineup["batting_order"]) == "in"
