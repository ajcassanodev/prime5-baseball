"""Auto-pick (Nov 24, 2026): fill a member's empty slots from Friday's
starting lineup, by a uniform random draw.

- Runs from the poller after ingest (poller/poll.py), for every active
  member with the "Auto-pick my lineup" toggle on.
- Fills batter slots b1..b5, the pitcher slot p1, and the extra 6th
  batter slot when the member has an available head-to-head grant for
  the week (a future extra slot — e.g. the milestone extra batter —
  reuses the same path: add its slot to the fill list).
- A fill draws only from games that have NOT started and whose lineup
  is published (green-dot players only); a batter fill is a batter in
  a game's batting order, a pitcher fill is a starting/probable
  pitcher of an unstarted game. Players already in the member's
  lineup are excluded; the same player in two doubleheader games is
  two different picks and may be drawn for either.
- Auto-filled picks are stored with auto=1 (shown with an AUTO tag)
  and are editable like any pick until their game's first pitch.
- Never overwrites a pick the member made (manual or auto) and never
  fills a slot the member declined after an auto-fill — declines are
  recorded when a member's submit drops an auto-filled pick
  (see picks.submit_picks). Idempotent: a later run only fills slots
  that are still empty.
- Toggle off = no fills at all. Turning it on mid-week fills on the
  next poller run; turning it off removes nothing already filled.

Randomness is injectable: pass a random.Random (or a seeded one) as
`rng` — tests do; production uses an unseeded Random.
"""
from __future__ import annotations

import random
from datetime import datetime, timezone

from . import db as dbmod
from . import lineup as lineup_mod
from . import matchups as matchups_mod
from .picks import _parse_utc

BATTER_SLOTS = ["b1", "b2", "b3", "b4", "b5"]
BONUS_BATTER_SLOT = "b6"
PITCHER_SLOT = "p1"


def _games_with_lineups(con, season: int, week: int, now: datetime) -> list:
    """(game, lineup) pairs for games that haven't started and whose
    lineup is published. A game with no lineup rows yet contributes
    nothing — the poller fills from it on a later run, once the
    lineup lands."""
    out = []
    for game in dbmod.games_for_week(con, season, week):
        if _parse_utc(game["game_date_utc"]) <= now:
            continue
        lineup = dbmod.lineup_for_game(con, game["game_pk"])
        if not lineup_mod.game_has_lineup(lineup):
            continue
        out.append((game, lineup))
    return out


def _batter_candidates(games_with_lineups, taken: set) -> list:
    """(mlb_id, game_pk) pairs: every batter in a published lineup,
    except pairs already in the member's lineup."""
    cands = []
    for game, lineup in games_with_lineups:
        for mlb_id in lineup["batting_order"]:
            pair = (mlb_id, game["game_pk"])
            if pair not in taken:
                cands.append(pair)
    return cands


def _pitcher_candidates(con, games_with_lineups, taken: set,
                        batter_ids: set) -> list:
    """(mlb_id, game_pk) pairs: a starting or probable pitcher of an
    unstarted game — the same union the pick page lists — except any
    player the member already has as a batter (a pitcher may not also
    be picked as a batter)."""
    cands = []
    for game, lineup in games_with_lineups:
        starters = set(lineup["starters"]) | {
            game.get("home_starter_mlb_id"),
            game.get("away_starter_mlb_id")} - {None}
        for mlb_id in starters:
            if mlb_id in batter_ids:
                continue
            pair = (mlb_id, game["game_pk"])
            if pair in taken:
                continue
            player = dbmod.player_by_id(con, mlb_id)
            if player is None or not player["is_pitcher"]:
                continue
            cands.append(pair)
    return cands


def fill_member_week(con, member_id: int, season: int, week: int,
                     now: datetime | None = None,
                     rng: random.Random | None = None) -> list:
    """Fill one member's empty slots for the week. Returns the slots
    filled this run (possibly empty). Never touches an existing pick
    or a declined slot."""
    now = now or datetime.now(timezone.utc)
    rng = rng if rng is not None else random.Random()
    picks = dbmod.picks_for_member_week(con, member_id, season, week)
    existing_slots = {p["slot"] for p in picks}
    taken = {(p["mlb_id"], p["game_pk"]) for p in picks}
    batter_ids = {p["mlb_id"] for p in picks if p["kind"] == "batter"}
    declined = dbmod.auto_declined_slots(con, member_id, season, week)
    games = _games_with_lineups(con, season, week, now)
    filled = []

    batter_slots = list(BATTER_SLOTS)
    if (dbmod.available_grant(con, member_id, season, week) is not None
            or BONUS_BATTER_SLOT in existing_slots):
        batter_slots.append(BONUS_BATTER_SLOT)
    for slot in batter_slots:
        if slot in existing_slots or slot in declined:
            continue
        cands = _batter_candidates(games, taken)
        if not cands:
            continue
        mlb_id, game_pk = rng.choice(cands)
        dbmod.save_pick(con, member_id, season, week, slot, "batter",
                        mlb_id, game_pk, auto=True)
        taken.add((mlb_id, game_pk))
        batter_ids.add(mlb_id)
        filled.append(slot)

    if PITCHER_SLOT not in existing_slots and PITCHER_SLOT not in declined:
        cands = _pitcher_candidates(con, games, taken, batter_ids)
        if cands:
            mlb_id, game_pk = rng.choice(cands)
            dbmod.save_pick(con, member_id, season, week, PITCHER_SLOT,
                            "pitcher", mlb_id, game_pk, auto=True)
            filled.append(PITCHER_SLOT)
    return filled


def auto_fill_week(con, season: int, week: int,
                   now: datetime | None = None,
                   rng: random.Random | None = None) -> dict:
    """Fill every opted-in member's empty slots for the week.
    Returns {"filled": {member_id: [slots...]}}. Grants are awarded
    lazily first, so a win last week opens the 6th slot in time."""
    now = now or datetime.now(timezone.utc)
    rng = rng if rng is not None else random.Random()
    matchups_mod.award_grants_through(con, season, week)
    filled = {}
    for member in matchups_mod.active_members(con):
        if not member.get("auto_pick"):
            continue
        slots = fill_member_week(con, member["id"], season, week, now, rng)
        if slots:
            filled[member["id"]] = slots
    return {"filled": filled}
