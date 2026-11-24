"""Scoring — pure functions. Points are computed, never stored.

Decided rules (Nov 13, 2026 deep dive):
- Batters: 1 point per hit. (HR/RBI weeks removed; the captain mechanic,
  which would make a captain's HR/RBI count, is a later sprinkle-in and
  is NOT implemented here.)
- Pitcher: 1 Friday starting pitcher, 0.01 points per pitch + 1 per
  strikeout. Friday games only.
- A pick on a player with no stats for that game scores 0.
"""
from __future__ import annotations

BATTER_HIT_POINTS = 1.0
PITCHER_PER_PITCH = 0.01
PITCHER_PER_STRIKEOUT = 1.0


def batter_points(hits: int) -> float:
    """1 point per hit."""
    return float(hits or 0) * BATTER_HIT_POINTS


def pitcher_points(pitches: int, strikeouts: int) -> float:
    """0.01 per pitch + 1 per strikeout, rounded to 2 decimals."""
    return round((pitches or 0) * PITCHER_PER_PITCH
                 + (strikeouts or 0) * PITCHER_PER_STRIKEOUT, 2)


def pick_points(kind: str, stats: dict | None) -> float:
    """Points for one pick given its game's stat row (or None -> 0.0)."""
    if not stats:
        return 0.0
    if kind == "batter":
        return batter_points(stats.get("hits", 0))
    if kind == "pitcher":
        return pitcher_points(stats.get("pitches", 0), stats.get("strikeouts", 0))
    raise ValueError(f"unknown pick kind: {kind!r}")


def week_points(picks: list, stats_by_pick: dict) -> float:
    """Total for a member's week.

    picks: list of dicts with keys kind, mlb_id, game_pk.
    stats_by_pick: {(game_pk, mlb_id): stat dict} — missing entry = 0.
    """
    total = 0.0
    for pick in picks:
        total += pick_points(
            pick["kind"],
            stats_by_pick.get((pick["game_pk"], pick["mlb_id"])),
        )
    return round(total, 2)
