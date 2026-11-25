"""Live Friday-night ticker for the dashboard (Nov 25, 2026, mock v5).

Sits at the top of the dashboard only, under the header: scrolling
strip, status strip, milestone bar, "How Friday went" block, and the
drop-down scoreboard. Everything comes from the DB (the poller
ingests Friday boxscores) — never live APIs per request.

HEADLINE GENERATION RULE v1 (documented here and in the guide note):
per division, the top story by points impact — a picked batter's HR
first (most HR, then most hits), then the most hits in a game, then
the top pitcher K game. Each story names the member and the play and
gives their current division rank ("Alonso's 3-run HR lifts Demo T.
to 2nd in Red Hots"). The two division stories join with ", while "
into ONE headline for the whole league. If only one division has a
story, the headline is just that one. If no division has a story:
the day's top lines (best batter + best pitcher, league-wide) when
games have started, or a pre-game line with the first-pitch time
when they haven't. No games at all: no headline.

Two honest limits of v1, both by design:
- The captain mechanic is not implemented yet (picks carry no
  captain flag; scoring.py says "coming preseason"), so a "captain
  HR" can't be detected — any picked batter's HR leads instead, and
  the scoreboard flashes gold on it. Captain-specific wording waits
  for the captain mechanic.
- The scoreboard has no line score: per-inning data isn't stored,
  so the board shows the big play, the play line and the points.
  Boxscores also can't tell a single from a double, so a 1-hit game
  is a SINGLE and 2+ hits without a HR is a MULTI-HIT.

Milestone stages (Nov 13, 2026 decision): every 5 hits in a row
earns 1 extra batter for the next week. Stage 1 is 5 in a row, Stage
2 is 10 (another). A third stage is undecided — the bar caps at 2
banked extra batters.
"""
from __future__ import annotations

import html

from . import db as dbmod
from . import matchups as matchups_mod
from . import mlb as mlb_mod
from .scoring import pick_points, week_points

MILESTONE_STAGES = (5, 10)
STREAK_CUTOFF = 5
STREAK_LIMIT = 3


def _ordinal(n: int) -> str:
    """1 -> '1st', 2 -> '2nd', 3 -> '3rd', 4 -> '4th', 11 -> '11th'."""
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _division_rank(con, member_id: int, season: int, week: int):
    """(rank, division) of a member in their division's standings for
    the week, or (None, division) when they don't appear."""
    member = dbmod.member_by_id(con, member_id)
    division = (member or {}).get("division") or "red"
    rows = [r for r in dbmod.standings(con, season, week)
            if r.get("division") == division]
    rank = next((i + 1 for i, r in enumerate(rows)
                 if r["member_id"] == member_id), None)
    return rank, division


def _picks_with_stats(con, member_id: int, season: int, week: int) -> list:
    """The member's picks with their player, stat row and game,
    in slot order. Picks with no stat row yet carry None stats."""
    stats = dbmod.stats_for_week(con, season, week)
    games = {g["game_pk"]: g for g in dbmod.games_for_week(con, season, week)}
    out = []
    for p in dbmod.picks_for_member_week(con, member_id, season, week):
        player = dbmod.player_by_id(con, p["mlb_id"]) or {}
        stat = stats.get((p["game_pk"], p["mlb_id"]))
        game = games.get(p["game_pk"], {})
        out.append({
            "slot": p["slot"], "kind": p["kind"],
            "mlb_id": p["mlb_id"], "game_pk": p["game_pk"],
            "name": player.get("name", "?"),
            "team": player.get("team", "?"),
            "stat": stat, "game": game,
            "points": pick_points(p["kind"], stat),
        })
    return out


def _hr_desc(hr: int, rbi: int) -> str:
    """'solo HR' / '2-run HR' / '3-run HR'."""
    if rbi == 1:
        return "solo HR"
    if rbi >= 2:
        return f"{rbi}-run HR"
    return "HR"


def _play_rank(pick: dict):
    """Sort key for a pick's story: HR first (most HR, then most
    hits), then most hits, then most strikeouts. Returns None when
    the pick has no stat row or no scoring line at all."""
    stat = pick["stat"]
    if not stat:
        return None
    if pick["kind"] == "batter":
        if stat.get("hr", 0) > 0:
            return (0, -stat["hr"], -stat.get("hits", 0))
        if stat.get("hits", 0) > 0:
            return (1, -stat.get("hits", 0))
        return None
    if stat.get("strikeouts", 0) > 0:
        return (2, -stat.get("strikeouts", 0))
    return None


def _story_text(play: dict, member_name: str, rank, division: str) -> str:
    """'Alonso's 3-run HR lifts Demo T. to 2nd in Red Hots'."""
    stat = play["stat"]
    if play["kind"] == "batter" and stat.get("hr", 0) > 0:
        desc = _hr_desc(stat["hr"], stat.get("rbi", 0))
    elif play["kind"] == "batter":
        ab = stat.get("at_bats", 0)
        desc = (f"{stat['hits']}-for-{ab}" if ab
                else f"{stat['hits']} hit{'s' if stat['hits'] != 1 else ''}")
    else:
        desc = f"{stat['strikeouts']} K"
    div_name = dbmod.division_display(division)
    if rank:
        return (f"{play['name']}'s {desc} lifts {member_name} to "
                f"{_ordinal(rank)} in {div_name}")
    return f"{play['name']}'s {desc} for {member_name}"


def _story_html(play: dict, member_name: str, rank, division: str) -> str:
    """Same story, with the division name in its own colour (gold
    Red Hots, white White Hots). Names are escaped; the division
    span is the only markup."""
    div_name = dbmod.division_display(division)
    cls = "tk-div-red" if division == "red" else "tk-div-white"
    div_span = f'<span class="{cls}">{html.escape(div_name)}</span>'
    stat = play["stat"]
    if play["kind"] == "batter" and stat.get("hr", 0) > 0:
        desc = _hr_desc(stat["hr"], stat.get("rbi", 0))
    elif play["kind"] == "batter":
        ab = stat.get("at_bats", 0)
        desc = (f"{stat['hits']}-for-{ab}" if ab
                else f"{stat['hits']} hit{'s' if stat['hits'] != 1 else ''}")
    else:
        desc = f"{stat['strikeouts']} K"
    name = html.escape(play["name"])
    member = html.escape(member_name)
    if rank:
        return (f"{name}'s {desc} lifts {member} to {_ordinal(rank)} "
                f"in {div_span}")
    return f"{name}'s {desc} for {member}"


def _division_story(con, division: str, season: int, week: int):
    """The division's top story, or None when no member of the
    division has a picked play with a scoring line yet."""
    members = [m for m in matchups_mod.active_members(con)
               if (m.get("division") or "red") == division]
    best = None
    for m in members:
        for pick in _picks_with_stats(con, m["id"], season, week):
            rank_key = _play_rank(pick)
            if rank_key is None:
                continue
            if best is None or rank_key < best[0]:
                best = (rank_key, m, pick)
    if best is None:
        return None
    _key, member, pick = best
    rank, _div = _division_rank(con, member["id"], season, week)
    name = dbmod.display_name(member)
    return {"text": _story_text(pick, name, rank, division),
            "html": _story_html(pick, name, rank, division)}


def _day_top_line(con, season: int, week: int):
    """Fallback headline when no division has a story but games have
    started: the day's best batter + best pitcher, league-wide.
    'Rutschman 3-for-4 · Beck 8 K — Friday's top lines'."""
    stats = dbmod.stats_for_week(con, season, week)
    if not stats:
        return None
    best_batter, best_pitcher = None, None
    for (game_pk, mlb_id), stat in stats.items():
        player = dbmod.player_by_id(con, mlb_id)
        if not player:
            continue
        if stat.get("hits", 0) > 0:
            key = (-stat["hits"], player["name"])
            if best_batter is None or key < best_batter[0]:
                best_batter = (key, player["name"], stat)
        if stat.get("strikeouts", 0) > 0:
            key = (-stat["strikeouts"], player["name"])
            if best_pitcher is None or key < best_pitcher[0]:
                best_pitcher = (key, player["name"], stat)
    parts = []
    if best_batter is not None:
        _k, name, stat = best_batter
        ab = stat.get("at_bats", 0)
        parts.append(f"{name} {stat['hits']}-for-{ab}" if ab
                     else f"{name} {stat['hits']} hits")
    if best_pitcher is not None:
        _k, name, stat = best_pitcher
        parts.append(f"{name} {stat['strikeouts']} K")
    if not parts:
        return None
    noun = "line" if len(parts) == 1 else "lines"
    return " · ".join(parts) + f" — Friday's top {noun}"


def _games_started(con, season: int, week: int) -> bool:
    """True once any game of the week has started (Final or live)."""
    return any(g.get("status") not in ("Scheduled", "Preview")
               for g in dbmod.games_for_week(con, season, week))


def headline_for_week(con, season: int, week: int):
    """{'text': ..., 'html': ...} or None when there are no games at
    all. See the module docstring for the v1 rule."""
    stories = [s for s in (_division_story(con, "red", season, week),
                           _division_story(con, "white", season, week))
               if s is not None]
    if stories:
        text = stories[0]["text"]
        html_text = stories[0]["html"]
        for s in stories[1:]:
            text += ", while " + s["text"]
            html_text += ", while " + s["html"]
        return {"text": text, "html": html_text}
    games = dbmod.games_for_week(con, season, week)
    if not games:
        return None
    if _games_started(con, season, week):
        line = _day_top_line(con, season, week)
        if line:
            return {"text": line, "html": html.escape(line)}
    earliest = min(g["game_date_utc"] for g in games
                   if g.get("game_date_utc"))
    first = mlb_mod.first_pitch_et(earliest)
    line = f"Friday's first pitch at {first} — picks lock at first pitch"
    return {"text": line, "html": html.escape(line)}


# ---------------------------------------------------------------- milestone

def milestone_state(streak: int) -> dict:
    """Progress toward the next extra batter. Stage 1 is 5 in a row,
    Stage 2 is 10 (another). A third stage is undecided, so the bar
    caps at 2 banked extra batters."""
    streak = max(0, int(streak or 0))
    if streak < MILESTONE_STAGES[0]:
        return {"banked": 0, "stage": 1, "stage_label": "MILESTONE — 5 HITS IN A ROW",
                "target": 5, "progress": streak,
                "pct": round(streak / 5 * 100),
                "reward": "→ EXTRA BATTER NEXT WEEK"}
    if streak < MILESTONE_STAGES[1]:
        return {"banked": 1, "stage": 2, "stage_label": "STAGE 2 — 10 HITS IN A ROW",
                "target": 10, "progress": streak,
                "pct": round(streak / 10 * 100),
                "reward": "★ Extra batter banked at 5 — 10 in a row earns another"}
    return {"banked": 2, "stage": 2, "stage_label": "STAGE 2 — 10 HITS IN A ROW",
            "target": 10, "progress": 10, "pct": 100,
            "reward": f"★ 2 extra batters banked — {streak} in a row"}


# ---------------------------------------------------------------- streaks & bonuses

def member_streaks(con, season: int, week: int, limit: int = STREAK_LIMIT,
                   min_streak: int = STREAK_CUTOFF) -> list:
    """Active members with a pick streak of min_streak+, best first.
    The race for the $50 best-streak prize — members' own streaks,
    never MLB batters' streaks."""
    rows = []
    for m in matchups_mod.active_members(con):
        streak = dbmod.hit_streak(con, m["id"], season, week)
        if streak >= min_streak:
            rows.append({"name": dbmod.display_name(m), "streak": streak})
    rows.sort(key=lambda r: (-r["streak"], r["name"]))
    return rows[:limit]


def member_bonuses(con, member: dict, season: int, week: int,
                   streak: int) -> list:
    """Bonuses actually earned: milestone extra batter(s) from the
    member's streak, and head-to-head grants — one earned this week
    (week final, matchup won) or one available to use this week."""
    out = []
    if streak >= MILESTONE_STAGES[1]:
        out.append(f"Another extra batter unlocked — {streak} hits in a row")
    elif streak >= MILESTONE_STAGES[0]:
        out.append(f"Extra batter unlocked — {streak} hits in a row")
    rows = con.execute(
        "SELECT * FROM head_to_head_grants WHERE member_id = ?"
        " AND season = ? AND (week_earned = ? OR use_week = ?)",
        (member["id"], season, week, week)).fetchall()
    if rows:
        pairs, _bye = matchups_mod.pairings_for_week(
            season, week, matchups_mod.active_member_ids(con))
        opp_id = None
        for a, b in pairs:
            if member["id"] in (a, b):
                opp_id = b if a == member["id"] else a
        opp = None
        if opp_id is not None:
            opp = dbmod.member_by_id(con, opp_id)
        opp_name = dbmod.display_name(opp) if opp else "your opponent"
        for r in rows:
            if r["week_earned"] == week:
                out.append("+1 batter pick earned — "
                           f"H2H win vs {opp_name}")
            elif r["use_week"] == week and not r["used"]:
                out.append("+1 batter pick available this week — H2H win")
    return out


def pick_results(con, member_id: int, season: int, week: int) -> list:
    """The member's own picks' Friday lines, in slot order.
    'Rutschman 3-for-4 · Final', 'Peterson 6 K · Final'. Only picks
    with a stat row — an unstarted game has no line yet."""
    out = []
    for pick in _picks_with_stats(con, member_id, season, week):
        stat = pick["stat"]
        if not stat:
            continue
        status = pick["game"].get("status") or ""
        label = "Final" if status == "Final" else (
            "Live" if _games_started(con, season, week) and status
            not in ("Scheduled", "Preview") else status)
        if pick["kind"] == "batter":
            ab = stat.get("at_bats", 0)
            line = (f"{pick['name']} {stat['hits']}-for-{ab}" if ab
                    else f"{pick['name']} {stat['hits']} hits")
        else:
            line = f"{pick['name']} {stat.get('strikeouts', 0)} K"
        out.append(f"{line} · {label}" if label else line)
    return out


# ---------------------------------------------------------------- scoreboard

def _pts_text(points: float, hits: int | None = None,
              hr: int = 0) -> str:
    if hits is not None:
        noun = "HIT" if hits == 1 else "HITS"
        text = f"+{points:.1f} PTS — {hits} {noun}"
        if hr:
            text += f" · {hr} HR"
        return text
    return f"+{points:.2f} PTS"


def big_play(con, member: dict, season: int, week: int):
    """The scoreboard's current big play for the signed-in member, or
    None when none of their picks has had a big moment yet. Only the
    member's OWN picks trigger it. Priority: week final (summary) >
    milestone crossed this week > HR > most hits > 5+ K game."""
    picks = _picks_with_stats(con, member["id"], season, week)
    with_stats = [p for p in picks if p["stat"]]
    if not with_stats:
        return None
    streak = dbmod.hit_streak(con, member["id"], season, week)
    final = matchups_mod.week_final(con, season, week)
    rank, division = _division_rank(con, member["id"], season, week)
    rank_label = (f"{_ordinal(rank)} in {dbmod.division_display(division)}"
                  if rank else "")
    if final:
        total = round(sum(p["points"] for p in picks), 2)
        play = f"Week {week} — {rank_label} · Streak {streak}"
        if streak >= MILESTONE_STAGES[0]:
            play += " · Extra batter unlocked"
        return {"key": "final", "kind": "final", "big": "WEEK FINAL",
                "clock": "FINAL", "play": play, "lines": "",
                "pts": f"+{total:.2f} PTS — WEEK TOTAL", "flash": False}
    prev = (dbmod.hit_streak(con, member["id"], season, week - 1)
            if week > 1 else 0)
    for stage in (MILESTONE_STAGES[1], MILESTONE_STAGES[0]):
        if streak >= stage and prev < stage:
            # Base lineup is 5 batters; each banked stage adds one.
            n = 5 + (1 if stage == 5 else 2)
            return {"key": f"milestone:{stage}", "kind": "milestone",
                    "big": "MILESTONE!", "clock": f"STREAK {streak}",
                    "play": (f"{streak} hits in a row — EXTRA BATTER "
                             f"UNLOCKED · {n} batters next week"),
                    "lines": "",
                    "pts": "★ EXTRA BATTER UNLOCKED",
                    "flash": True}
    hr_picks = [p for p in with_stats
                if p["kind"] == "batter" and p["stat"].get("hr", 0) > 0]
    if hr_picks:
        pick = max(hr_picks, key=lambda p: (p["stat"]["hr"],
                                            p["stat"].get("hits", 0)))
        stat = pick["stat"]
        status = pick["game"].get("status") or ""
        return {"key": f"hr:{pick['mlb_id']}:{pick['game_pk']}",
                "kind": "homer", "big": "HOME RUN",
                "clock": "Final" if status == "Final" else "Live",
                "play": f"{pick['name']} — {_hr_desc(stat['hr'], stat.get('rbi', 0))}",
                "lines": "",
                "pts": _pts_text(pick["points"], stat.get("hits", 0),
                                 stat["hr"]),
                "flash": True}
    hit_picks = [p for p in with_stats
                 if p["kind"] == "batter" and p["stat"].get("hits", 0) > 0]
    if hit_picks:
        pick = max(hit_picks, key=lambda p: p["stat"].get("hits", 0))
        stat = pick["stat"]
        status = pick["game"].get("status") or ""
        hits = stat.get("hits", 0)
        ab = stat.get("at_bats", 0)
        return {"key": f"hit:{pick['mlb_id']}:{pick['game_pk']}",
                "kind": "hit",
                "big": "MULTI-HIT" if hits >= 2 else "SINGLE",
                "clock": "Final" if status == "Final" else "Live",
                "play": (f"{pick['name']} — {hits}-for-{ab}" if ab
                         else f"{pick['name']} — {hits} hits"),
                "lines": "",
                "pts": _pts_text(pick["points"], hits),
                "flash": False}
    k_picks = [p for p in with_stats
               if p["kind"] == "pitcher"
               and p["stat"].get("strikeouts", 0) >= 5]
    if k_picks:
        pick = max(k_picks, key=lambda p: p["stat"].get("strikeouts", 0))
        stat = pick["stat"]
        status = pick["game"].get("status") or ""
        return {"key": f"k:{pick['mlb_id']}:{pick['game_pk']}",
                "kind": "k", "big": "STRIKEOUT",
                "clock": "Final" if status == "Final" else "Live",
                "play": (f"{pick['name']} — strikeout "
                         f"#{stat['strikeouts']}"),
                "lines": "",
                "pts": f"+{pick['points']:.2f} PTS — {stat['strikeouts']} K",
                "flash": False}
    return None


# ---------------------------------------------------------------- assembly

def ticker_data(con, member: dict, season: int, week: int) -> dict:
    """Everything the dashboard ticker needs, JSON-serializable.
    Callers must have run matchups_mod.award_grants_through first so
    H2H grants are fresh (the dashboard route does)."""
    picks = dbmod.picks_for_member_week(con, member["id"], season, week)
    stats = dbmod.stats_for_week(con, season, week)
    total = week_points(
        [{"kind": p["kind"], "mlb_id": p["mlb_id"], "game_pk": p["game_pk"]}
         for p in picks], stats)
    streak = dbmod.hit_streak(con, member["id"], season, week)
    rank, division = _division_rank(con, member["id"], season, week)
    headline = headline_for_week(con, season, week)
    bonuses = member_bonuses(con, member, season, week, streak)
    streaks = member_streaks(con, season, week)
    results = pick_results(con, member["id"], season, week)
    h2h = matchups_mod.dashboard_matchup(con, member, season, week)
    h2h_text = None
    h2h_row = None
    if h2h and not h2h.get("bye"):
        games = dbmod.games_for_week(con, season, week)
        finals = sum(1 for g in games if g.get("status") == "Final")
        tail = (f"{finals} game{'s' if finals != 1 else ''} final"
                if h2h.get("final") else "live")
        opp_name = h2h["opponent"]["display_name"]
        h2h_text = (f"You {h2h['my_points']:.2f} – {opp_name} "
                    f"{h2h['opp_points']:.2f} · {tail}")
        h2h_row = {"opp_name": opp_name, "my": h2h["my_points"],
                   "opp": h2h["opp_points"],
                   "final": bool(h2h.get("final"))}
    strip_items = []
    if headline is not None:
        strip_items.append({"label": "HEADLINE", "text": headline["text"]})
    if h2h_text is not None:
        strip_items.append({"label": "H2H", "text": h2h_text})
    for bonus in bonuses:
        strip_items.append({"label": "BONUS", "text": bonus})
    if streaks:
        # Mock v5: "Adam 6 in a row · Tim Farrell 5 · Joe Pagliaro 5".
        parts = [f"{streaks[0]['name']} {streaks[0]['streak']} in a row"]
        parts += [f"{s['name']} {s['streak']}" for s in streaks[1:]]
        strip_items.append({"label": "STREAK", "text": " · ".join(parts)})
    for line in results:
        strip_items.append({"label": None, "text": line})
    if not strip_items:
        strip_items.append({"label": None,
                            "text": "No Friday games scheduled yet."})
    strip_key = "|".join(
        f"{(i['label'] or '')}:{i['text']}" for i in strip_items)
    board = big_play(con, member, season, week)
    return {
        "headline": headline,
        "strip_items": strip_items,
        "strip_key": strip_key,
        "status": {"total": total, "streak": streak,
                   "rank": rank,
                   "rank_ordinal": _ordinal(rank) if rank else "–",
                   "division_name": dbmod.division_display(division)},
        "milestone": milestone_state(streak),
        "h2h": ({"text": h2h_text, **h2h_row} if h2h_text is not None
                else None),
        "bonuses": bonuses,
        "streaks": streaks,
        "board": board,
        "board_key": board["key"] if board else None,
        "final": matchups_mod.week_final(con, season, week),
    }
