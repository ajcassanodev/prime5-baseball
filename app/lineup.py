"""Lineup status — pure helpers.

A player is "in the lineup" when he is in his game's starting nine
(batters: the 9-man batting order; pitcher: the game's starting
pitcher). The pick page shows a green dot for in-lineup players and a
gray dot for everyone else on the game roster. Lineups are only
published a few hours before first pitch, so at pick time during the
week a player may have no lineup info yet — that is "unknown", shown
as gray until the lineup lands (same treatment as out, but the pick
page labels it "lineup not announced" when the game has no lineup
rows at all).

The opposing starting pitcher for a batter is the other team's
starter in the same game.
"""
from __future__ import annotations

IN = "in"
OUT = "out"


def batter_lineup_status(mlb_id: int, batting_order: list) -> str:
    """'in' if the batter is in the 9-man batting order, else 'out'."""
    return IN if mlb_id in set(batting_order) else OUT


def pitcher_lineup_status(mlb_id: int, starter_ids: list) -> str:
    """'in' if the pitcher is a starting pitcher of the game."""
    return IN if mlb_id in set(starter_ids) else OUT


def lineup_status(kind: str, mlb_id: int, game_lineup: dict) -> str:
    """kind 'batter'|'pitcher'; game_lineup has batting_order + starters."""
    if kind == "batter":
        return batter_lineup_status(mlb_id, game_lineup.get("batting_order", []))
    if kind == "pitcher":
        return pitcher_lineup_status(mlb_id, game_lineup.get("starters", []))
    raise ValueError(f"unknown kind: {kind!r}")


def opposing_starter(game: dict, player_team: str):
    """The opposing team's starting pitcher for a game.

    game: {home_team, away_team, home_starter_mlb_id,
    away_starter_mlb_id, home_starter_name, away_starter_name}.
    Returns {mlb_id, name} or None if not announced yet.
    """
    if player_team == game["home_team"]:
        mlb_id = game.get("away_starter_mlb_id")
        name = game.get("away_starter_name")
    elif player_team == game["away_team"]:
        mlb_id = game.get("home_starter_mlb_id")
        name = game.get("home_starter_name")
    else:
        return None
    if mlb_id is None:
        return None
    return {"mlb_id": mlb_id, "name": name}


def game_has_lineup(game_lineup: dict) -> bool:
    """True once the game has any announced lineup (batting order or starter)."""
    return bool(game_lineup.get("batting_order") or game_lineup.get("starters"))
