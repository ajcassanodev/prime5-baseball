"""Pick-help form (Nov 25, 2026): last-7-days form, Friday
probabilities, and the pick-page "Form guide".

Formula v1 — simple and explainable, shown to members in the guide:
- Batter: blended per-AB hit rate h = 0.6 * season AVG + 0.4 *
  recent AVG (last 7 days; with fewer than 10 AB in the window the
  season AVG is used on its own). Adjusted for Friday's opposing
  starter: factor = clamp(1 + (4.20 - opp_ERA) * 0.02, 0.95, 1.05).
  P(hit Friday) = 1 - (1 - h*factor)^4, rounded to a whole %,
  clamped to 5–95%.
- Pitcher: lambda = (season K/9) * (season IP per start) / 9;
  P(5+ K) = 1 - Poisson_CDF(4; lambda), rounded to a whole %.
  IP per start is capped at 7.0: a spot starter with 1–2 starts
  (say 60 relief innings over 2 starts) would otherwise get a
  30-inning "per start" and a near-100% probability from a lambda
  in the 20s. No regular starter averages more than 7.
- Hit streak: consecutive most-recent appearances with >= 1 hit. A
  game with no at-bat (defensive sub, pinch-run) neither extends nor
  breaks it. v1 streaks come from the fetched 7-day window and can
  be truncated if the streak started before the window.

Everything here is pure: it takes parsed boxscores / season stats
and returns plain dicts, so tests run on fixtures with no network.
"""
import math

# The league-average ERA the opposing-starter adjustment pivots on.
LEAGUE_ERA = 4.20
# A batter's recent window counts only when they have this many AB;
# below it the sample is too small and the season AVG stands alone.
MIN_RECENT_AB = 10
# The "4" in the hit probability: a batter's ~4 at-bats in a game.
AT_BATS_PER_GAME = 4


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def batter_hit_prob(season_avg: float, recent_ab: int, recent_hits: int,
                    opp_era: float | None) -> int:
    """Whole-% chance the batter gets a hit on Friday, or None when
    the inputs can't support a number (no season average at all).
    A 0.000 season average is real data — it yields the 5% floor,
    not a blank."""
    if season_avg is None:
        return None
    if recent_ab >= MIN_RECENT_AB:
        recent_avg = recent_hits / recent_ab
        h = 0.6 * season_avg + 0.4 * recent_avg
    else:
        h = season_avg
    factor = 1.0
    if opp_era is not None:
        factor = clamp(1 + (LEAGUE_ERA - opp_era) * 0.02, 0.95, 1.05)
    p = 1 - (1 - h * factor) ** AT_BATS_PER_GAME
    return int(round(clamp(p * 100, 5, 95)))


def poisson_cdf(k: int, lam: float) -> float:
    """P(X <= k) for X ~ Poisson(lam)."""
    total = 0.0
    for i in range(k + 1):
        total += math.exp(-lam) * lam ** i / math.factorial(i)
    return total


def pitcher_five_k_prob(k_per_9: float | None,
                        ip_per_start: float | None) -> int:
    """Whole-% chance the pitcher strikes out 5 or more on Friday,
    or None when the inputs can't support a number."""
    if not k_per_9 or not ip_per_start:
        return None
    lam = k_per_9 * ip_per_start / 9.0
    p = 1 - poisson_cdf(4, lam)
    return int(round(p * 100))


def k_per_9(strikeouts: int, ip_outs: int) -> float | None:
    """Season strikeouts per 9 innings, or None with no innings."""
    if not ip_outs:
        return None
    return strikeouts * 27.0 / ip_outs


def ip_per_start(ip_outs: int, games_started: int) -> float | None:
    """Season innings per start, capped at 7.0 (see the module
    docstring — uncapped, a spot starter's relief innings inflate
    it past 30), or None with no starts."""
    if not games_started or not ip_outs:
        return None
    return min(7.0, ip_outs / 3.0 / games_started)


def aggregate_window(games: list) -> dict:
    """Per-player window totals from parsed boxscores.

    games: [{date, boxscore}] where boxscore is the parse_boxscore
    dict ({players, stats} plus a 'starters' list of mlb_ids).
    'datetime' (first-pitch ISO) is used as a tiebreak within a date
    so a doubleheader's games order correctly for streaks.
    Returns {mlb_id: {"hitting": [{date, at_bats, hits}] (date order),
    "pitching": [{date, pitches, strikeouts, started}] (date order)}}.
    Only games the player appeared in are listed (a stats row).
    """
    out: dict = {}
    for game in sorted(games,
                       key=lambda g: (g["date"], g.get("datetime", ""))):
        box = game["boxscore"]
        starters = set(box.get("starters", []))
        by_id = {s["mlb_id"]: s for s in box.get("stats", [])}
        for mlb_id, stat in by_id.items():
            entry = out.setdefault(mlb_id, {"hitting": [], "pitching": []})
            if stat.get("at_bats") or stat.get("hits"):
                entry["hitting"].append({
                    "date": game["date"], "at_bats": stat.get("at_bats", 0),
                    "hits": stat.get("hits", 0)})
            if stat.get("pitches") or stat.get("strikeouts"):
                entry["pitching"].append({
                    "date": game["date"],
                    "pitches": stat.get("pitches", 0),
                    "strikeouts": stat.get("strikeouts", 0),
                    "started": mlb_id in starters})
    return out


def hitting_totals(log: list) -> dict:
    """{hits, at_bats, streak} for one player's hitting log (date
    order, most recent last). Streak counts consecutive most-recent
    appearances with >= 1 hit; an appearance with no at-bat is
    skipped (it neither extends nor breaks the streak)."""
    hits = sum(g["hits"] for g in log)
    ab = sum(g["at_bats"] for g in log)
    streak = 0
    for g in reversed(log):
        if not g["at_bats"]:
            continue
        if g["hits"] > 0:
            streak += 1
        else:
            break
    return {"hits": hits, "at_bats": ab, "streak": streak}


def last_start(log: list) -> dict | None:
    """The pitcher's most recent start in the window, or None when
    they didn't start one: {pitches, strikeouts}."""
    for g in reversed(log):
        if g.get("started"):
            return {"pitches": g["pitches"], "strikeouts": g["strikeouts"]}
    return None


def batter_form_row(mlb_id: int, window: dict, season: dict,
                    opp_era: float | None) -> dict:
    """One player_form row for a batter. season is the parse_people_stats
    hitting dict for the player ({avg, at_bats, hits}); window is the
    aggregate_window entry for the player (or an empty one)."""
    totals = hitting_totals(window.get("hitting", []))
    return {
        "mlb_id": mlb_id, "is_pitcher": 0,
        "hits_last_7d": totals["hits"], "ab_last_7d": totals["at_bats"],
        "streak": totals["streak"],
        "season_avg": season.get("avg"),
        "hit_prob_pct": batter_hit_prob(
            season.get("avg"), totals["at_bats"], totals["hits"], opp_era),
        "last_start_pitches": None, "last_start_k": None,
        "five_k_prob_pct": None,
    }


def pitcher_form_row(mlb_id: int, window: dict, season: dict) -> dict:
    """One player_form row for a Friday starter. season is the
    parse_people_stats pitching dict ({era, strikeouts, ip_outs,
    games_started})."""
    start = last_start(window.get("pitching", []))
    return {
        "mlb_id": mlb_id, "is_pitcher": 1,
        "hits_last_7d": 0, "ab_last_7d": 0, "streak": 0,
        "season_avg": None,
        "hit_prob_pct": None,
        "last_start_pitches": start["pitches"] if start else None,
        "last_start_k": start["strikeouts"] if start else None,
        "five_k_prob_pct": pitcher_five_k_prob(
            k_per_9(season.get("strikeouts", 0), season.get("ip_outs", 0)),
            ip_per_start(season.get("ip_outs", 0),
                         season.get("games_started", 0))),
    }


def batter_form_line(row: dict | None) -> str:
    """'9 hits last 7 days · 5-game hit streak' — the streak part is
    dropped when the streak is 0; blank when there is no form row."""
    if not row:
        return ""
    line = f"{row['hits_last_7d']} hit{'s' if row['hits_last_7d'] != 1 else ''} last 7 days"
    if row.get("streak"):
        line += f" · {row['streak']}-game hit streak"
    return line


def pitcher_form_line(row: dict | None) -> str:
    """'Last start: 91 pitches · 6 K' — blank when the pitcher has
    no start in the window."""
    if not row or row.get("last_start_pitches") is None:
        return ""
    return (f"Last start: {row['last_start_pitches']} pitches"
            f" · {row['last_start_k']} K")


# ---------------------------------------------------------------- guide

FORMULA_NOTE = (
    "Hit % blends season average with the last 7 days and adjusts for "
    "Friday's opposing starter; 5+ K % comes from the starter's season "
    "strikeout rate. Simple numbers, not a prediction service.")

GUIDE_STREAK_NOTE = "5 in a row earns +1 batter next week."


def build_guide(form_rows: list, friday_batters: set,
                friday_starters: set) -> dict:
    """The Form guide's three lists, from the week's player_form rows
    (each enriched with the player's "name" and "team" for display).

    hottest: top 5 Friday batters by hits in the last 7 days
    (ties by name). starters: top 2 Friday starters by 5+ K
    probability (ties by name). streaks: top 3 Friday batters by
    current hit streak (streak > 0 only, ties by name). Rows without
    the needed number are skipped, so a partial week still renders.
    """
    batters = [r for r in form_rows
               if not r["is_pitcher"] and r["mlb_id"] in friday_batters]
    starters = [r for r in form_rows
                if r["is_pitcher"] and r["mlb_id"] in friday_starters]
    hottest = sorted(batters,
                     key=lambda r: (-r["hits_last_7d"], r["name"]))[:5]
    top_starters = sorted(
        [r for r in starters if r.get("five_k_prob_pct") is not None],
        key=lambda r: (-r["five_k_prob_pct"], r["name"]))[:2]
    streaks = sorted([r for r in batters if r.get("streak")],
                     key=lambda r: (-r["streak"], r["name"]))[:3]

    def _with_short(rows):
        return [{**r, "short": (r["name"] or "").split()[-1] if r.get("name")
                 else "?"} for r in rows]

    return {"hottest": _with_short(hottest),
            "starters": _with_short(top_starters),
            "streaks": _with_short(streaks), "note": FORMULA_NOTE}
