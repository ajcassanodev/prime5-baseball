"""Pick submission rules (core only).

A valid week: exactly 5 batter slots + 1 starting pitcher — or 6
batters when the member earned a head-to-head bonus pick (extra_batter).
- Batter slots hold non-pitchers; the pitcher slot holds a pitcher who
  is a starting pitcher of a game that week.
- Doubleheader rule (decided Nov 23, 2026): a batter appears as one
  pick row per game. A member may pick either game or both; picking
  both games of the same player uses 2 of the 5 batter slots. Each
  row scores only its own game's hits and locks at its own game's
  first pitch. Pitchers are unaffected (a starter only starts one game).
- No player twice in the same game (a pitcher may not also be picked
  as a batter).
- A pick whose game has already started is rejected — except on a
  playground week (past data, sandbox), where picks are always allowed.
- Editing: a member may resubmit any time; picks for games that have
  already started are frozen and kept as-is. Resubmitting with only
  5 batters drops a leftover 6th batter — unless its game has already
  started, in which case the pick is frozen and stays.

Batter picks are (mlb_id, game_pk) pairs: the same player in two
different games is two different picks; the same player twice in the
same game is a duplicate.
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import db as dbmod

BATTER_COUNT = 5


class PickError(Exception):
    """A pick-set problem, reported back to the member."""


def _parse_utc(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _starters_of_game(con, game: dict) -> set:
    """Starter mlb_ids for a game: announced lineup starters, plus the
    game's probable starters (pre-game, the lineup arrays may not list
    the pitcher yet)."""
    starters = set(dbmod.lineup_for_game(con, game["game_pk"])["starters"])
    starters |= {game.get("home_starter_mlb_id"),
                 game.get("away_starter_mlb_id")} - {None}
    return starters


def validate_pick_set(con, season: int, week: int,
                      batter_picks: list, pitcher_id: int,
                      extra_batter: bool = False) -> dict:
    """Validate a full pick set. batter_picks is a list of
    (mlb_id, game_pk) pairs. Returns {batters: [player dicts],
    pitcher: player dict, games: {game_pk: game dict}}.

    extra_batter allows a 6th batter (a head-to-head bonus pick): the
    set may then hold 5 or 6 batters. Without it, exactly 5.

    Raises PickError with a plain-language message on any problem.
    """
    if extra_batter:
        if len(batter_picks) not in (BATTER_COUNT, BATTER_COUNT + 1):
            raise PickError(f"Pick {BATTER_COUNT} batters"
                            f" (or {BATTER_COUNT + 1} with a bonus pick).")
    elif len(batter_picks) != BATTER_COUNT:
        raise PickError(f"Pick exactly {BATTER_COUNT} batters.")
    if len(set(batter_picks)) != len(batter_picks):
        raise PickError("Each player can only be picked once per game.")
    batter_ids = [mlb_id for mlb_id, _ in batter_picks]
    if pitcher_id in batter_ids:
        raise PickError("The pitcher cannot also be picked as a batter.")
    if pitcher_id is None:
        raise PickError("Pick 1 starting pitcher.")

    games = {g["game_pk"]: g for g in dbmod.games_for_week(con, season, week)}
    if not games:
        raise PickError("No games scheduled for this week yet.")
    rosters = {gpk: {p["mlb_id"] for p in dbmod.players_for_game(con, gpk)}
               for gpk in games}

    batters = []
    for mlb_id, game_pk in batter_picks:
        player = dbmod.player_by_id(con, mlb_id)
        if player is None:
            raise PickError(f"Unknown player: {mlb_id}.")
        if player["is_pitcher"]:
            raise PickError(f"{player['name']} is a pitcher, not a batter.")
        if game_pk not in games:
            raise PickError(f"Unknown game: {game_pk}.")
        if mlb_id not in rosters[game_pk]:
            if any(mlb_id in rosters[gpk] for gpk in games):
                raise PickError(
                    f"{player['name']} is not playing in that game.")
            raise PickError(f"{player['name']} has no game this week.")
        batters.append({**player, "game_pk": game_pk})

    pitcher = dbmod.player_by_id(con, pitcher_id)
    if pitcher is None:
        raise PickError(f"Unknown player: {pitcher_id}.")
    if not pitcher["is_pitcher"]:
        raise PickError(f"{pitcher['name']} is not a pitcher.")
    pgame_pk = None
    for g in sorted(games.values(), key=lambda g: g["game_date_utc"]):
        if pitcher_id in _starters_of_game(con, g):
            pgame_pk = g["game_pk"]
            break
    if pgame_pk is None:
        raise PickError(f"{pitcher['name']} is not a starting pitcher this week.")

    return {"batters": batters,
            "pitcher": {**pitcher, "game_pk": pgame_pk},
            "games": games}


def submit_picks(con, member_id: int, season: int, week: int,
                 batter_picks: list, pitcher_id: int,
                 now: datetime | None = None,
                 extra_batter: bool = False) -> dict:
    """Validate and store a member's week. Returns the stored pick set.

    Picks for games already started are frozen: they are kept as-is and
    the new submission only replaces picks for games that have not
    started yet (playground weeks skip the started check entirely).
    On a doubleheader day each game is frozen independently — a pick
    for a started game 1 stays put while the same player's game 2
    pick can still change.
    """
    now = now or datetime.now(timezone.utc)
    checked = validate_pick_set(con, season, week, batter_picks, pitcher_id,
                                extra_batter=extra_batter)
    week_row = dbmod.current_week(con, season)
    playground = bool(week_row and week_row.get("playground"))

    desired = (
        [(f"b{i+1}", "batter", b) for i, b in enumerate(checked["batters"])]
        + [("p1", "pitcher", checked["pitcher"])]
    )
    existing = {p["slot"]: p for p in
                dbmod.picks_for_member_week(con, member_id, season, week)}
    # An auto-filled pick the member keeps stays tagged AUTO (they
    # didn't choose it); a pick they chose themselves stays manual.
    auto_by_pair = {(p["mlb_id"], p["game_pk"]): bool(p.get("auto"))
                    for p in existing.values()}
    frozen, saved = [], []
    for slot, kind, player in desired:
        game = checked["games"][player["game_pk"]]
        started = _parse_utc(game["game_date_utc"]) <= now
        if started and not playground:
            # Frozen: keep whatever is already stored for this slot.
            if slot in existing:
                frozen.append(slot)
            else:
                raise PickError(
                    f"{player['name']}'s game has already started.")
            continue
        dbmod.save_pick(con, member_id, season, week, slot, kind,
                        player["mlb_id"], player["game_pk"],
                        auto=auto_by_pair.get(
                            (player["mlb_id"], player["game_pk"]), False))
        saved.append(slot)
    # A 5-batter submission drops a leftover 6th batter from an earlier
    # bonus submission — unless that game's already started, in which
    # case the pick is frozen and stays.
    if len(checked["batters"]) == BATTER_COUNT:
        old6 = existing.get("b6")
        if old6 is not None:
            game6 = checked["games"].get(old6["game_pk"])
            started6 = (game6 is not None
                        and _parse_utc(game6["game_date_utc"]) <= now)
            if playground or not started6:
                dbmod.delete_pick(con, member_id, season, week, "b6")
    # Auto-pick declines: any previously auto-filled pick that is no
    # longer in the stored set was cleared by the member — record its
    # slot so the poller never fills it again this week. Frozen picks
    # are still stored, so they are never declined.
    stored_pairs = {(p["mlb_id"], p["game_pk"]) for p in
                    dbmod.picks_for_member_week(con, member_id, season, week)}
    for p in existing.values():
        if p.get("auto") and (p["mlb_id"], p["game_pk"]) not in stored_pairs:
            dbmod.record_auto_decline(con, member_id, season, week, p["slot"])
    return {"saved": saved, "frozen": frozen,
            "batters": checked["batters"], "pitcher": checked["pitcher"]}
