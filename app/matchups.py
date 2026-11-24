"""Weekly head-to-head matchups (Nov 24, 2026).

Every active member faces one opponent a week; the winner earns +1
batter pick for the following week (use-it-or-lose-it). A matchup is
scored on the base lineup only — batter slots b1..b5 + pitcher p1;
a bonus 6th batter never counts toward the matchup. Ties earn nothing,
and a bye week (odd member count) earns nothing.

Pairings are a deterministic circle-method round-robin over the active
members ordered by id: with n members there are n-1 rounds (n rounds
with a bye when n is odd); week w uses round (w-1) % rounds, so the
pairings repeat after the full cycle — with 14 members, weeks 14-25
repeat rounds 1-12.
"""
from __future__ import annotations

from datetime import date, timedelta

from . import db as dbmod
from .scoring import week_points

BASE_SLOTS = ("b1", "b2", "b3", "b4", "b5", "p1")
REGULAR_WEEKS = 25


def active_member_ids(con) -> list:
    rows = con.execute(
        "SELECT id FROM members WHERE is_active = 1 ORDER BY id").fetchall()
    return [r["id"] for r in rows]


def active_members(con) -> list:
    rows = con.execute(
        "SELECT * FROM members WHERE is_active = 1 ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def _with_display(member: dict) -> dict:
    member["display_name"] = dbmod.display_name(member)
    member["display_sub"] = dbmod.display_sub(member)
    return member


def pairings_for_week(season, week, member_ids):
    """Circle-method round-robin. Returns (pairs, bye_id).

    pairs is a list of (member_a_id, member_b_id); bye_id is the
    member sitting out that week (odd count only) or None. The same
    inputs always give the same pairings. With fewer than 2 members
    there are no pairs; the lone member (if any) is the bye.
    """
    ids = list(member_ids)
    if len(ids) < 2:
        return [], (ids[0] if ids else None)
    if len(ids) % 2 == 1:
        ids = ids + [None]
    n = len(ids)
    rounds = n - 1
    r = (week - 1) % rounds
    arr = ids[:]
    for _ in range(r):
        arr = [arr[0], arr[-1]] + arr[1:-1]
    pairs, bye = [], None
    for i in range(n // 2):
        a, b = arr[i], arr[n - 1 - i]
        if a is None:
            bye = b
        elif b is None:
            bye = a
        else:
            pairs.append((a, b))
    return pairs, bye


def matchup_points(con, member_id, season, week) -> float:
    """A member's matchup score: base lineup only (b1..b5 + p1).

    Slot b6+ is excluded by construction — only base slots are read,
    so a bonus batter's points never count here (they still count
    toward overall standings via week_points).
    """
    picks = [p for p in
             dbmod.picks_for_member_week(con, member_id, season, week)
             if p["slot"] in BASE_SLOTS]
    stats = dbmod.stats_for_week(con, season, week)
    return week_points(
        [{"kind": p["kind"], "mlb_id": p["mlb_id"], "game_pk": p["game_pk"]}
         for p in picks],
        stats)


def week_final(con, season, week) -> bool:
    """True once every game in the week is Final (and the week has at
    least one game). A pairing's score isn't settled until the whole
    Friday is done: a stat row in a live game still moves, and a
    member can still swap a pick into an unstarted game. A DNP in a
    final game is settled at 0 — no stat row needed."""
    games = dbmod.games_for_week(con, season, week)
    if not games:
        return False
    return all(g.get("status") == "Final" for g in games)


def award_head_to_head_grants(con, season, week):
    """Idempotent: for a final week, one grant per winner (use_week =
    week+1). No grant on ties or byes."""
    if not week_final(con, season, week):
        return
    pairs, _bye = pairings_for_week(season, week, active_member_ids(con))
    for a, b in pairs:
        pa = matchup_points(con, a, season, week)
        pb = matchup_points(con, b, season, week)
        if pa == pb:
            continue
        winner = a if pa > pb else b
        con.execute(
            "INSERT OR IGNORE INTO head_to_head_grants"
            " (season, week_earned, member_id, use_week, used)"
            " VALUES (?, ?, ?, ?, 0)",
            (season, week, winner, week + 1))
    con.commit()


def award_grants_through(con, season, week):
    """Award for every week up to `week` (inclusive). Safe to call on
    every render — INSERT OR IGNORE makes it idempotent."""
    for w in range(1, week + 1):
        award_head_to_head_grants(con, season, w)


def matchup_rows(con, season, week):
    """Pairings for a week with live scores and finality. Returns
    (rows, bye_member_or_None). Each row: {a, b (member dicts with
    display_name/display_sub), a_points, b_points, final,
    winner: 'a' | 'b' | 'tie' | None} — winner is None until final."""
    members = {m["id"]: _with_display(m) for m in active_members(con)}
    pairs, bye_id = pairings_for_week(season, week, list(members))
    final = week_final(con, season, week)
    rows = []
    for a, b in pairs:
        pa = matchup_points(con, a, season, week)
        pb = matchup_points(con, b, season, week)
        winner = None
        if final:
            winner = "a" if pa > pb else ("b" if pb > pa else "tie")
        rows.append({"a": members[a], "b": members[b],
                     "a_points": pa, "b_points": pb, "final": final,
                     "winner": winner})
    bye = members.get(bye_id) if bye_id is not None else None
    return rows, bye


def head_to_head_records(con, season, up_to_week):
    """[{member, wins, losses, ties, picks_earned, matchup_points}]
    for final pairings in weeks 1..up_to_week, best first (wins, then
    matchup points). Picks earned == wins: each win earned one grant
    for the following week."""
    members = {m["id"]: _with_display(m) for m in active_members(con)}
    rec = {mid: {"member": m, "wins": 0, "losses": 0, "ties": 0,
                 "matchup_points": 0.0} for mid, m in members.items()}
    for w in range(1, up_to_week + 1):
        if not week_final(con, season, w):
            continue
        pairs, _bye = pairings_for_week(season, w, list(members))
        for a, b in pairs:
            pa = matchup_points(con, a, season, w)
            pb = matchup_points(con, b, season, w)
            rec[a]["matchup_points"] += pa
            rec[b]["matchup_points"] += pb
            if pa > pb:
                rec[a]["wins"] += 1
                rec[b]["losses"] += 1
            elif pb > pa:
                rec[b]["wins"] += 1
                rec[a]["losses"] += 1
            else:
                rec[a]["ties"] += 1
                rec[b]["ties"] += 1
    out = []
    for r in rec.values():
        r["picks_earned"] = r["wins"]
        out.append(r)
    out.sort(key=lambda r: (-r["wins"], -r["matchup_points"],
                            r["member"]["name"]))
    return out


def dashboard_matchup(con, member, season, week):
    """The signed-in member's current matchup + season record, or None
    when fewer than 2 members are active (head-to-head hasn't started).

    {bye, opponent (member dict or None), my_points, opp_points,
    final, winner: 'me' | 'opp' | 'tie' | None, status (plain-language
    line for the card), wins, losses, ties, picks_earned}."""
    ids = active_member_ids(con)
    if len(ids) < 2:
        return None
    records = {r["member"]["id"]: r
               for r in head_to_head_records(con, season, week)}
    mine = records.get(member["id"], {"wins": 0, "losses": 0, "ties": 0,
                                      "picks_earned": 0})
    base = {"wins": mine["wins"], "losses": mine["losses"],
            "ties": mine["ties"], "picks_earned": mine["picks_earned"]}
    pairs, bye_id = pairings_for_week(season, week, ids)
    opponent_id = None
    for a, b in pairs:
        if member["id"] in (a, b):
            opponent_id = b if a == member["id"] else a
            break
    if opponent_id is None:
        # Bye week (odd member count).
        return {**base, "bye": True, "opponent": None, "my_points": 0.0,
                "opp_points": 0.0, "final": False, "winner": None,
                "status": "You have a bye this week — no matchup, no pick."}
    opp = _with_display(dbmod.member_by_id(con, opponent_id))
    my_points = matchup_points(con, member["id"], season, week)
    opp_points = matchup_points(con, opponent_id, season, week)
    final = week_final(con, season, week)
    winner = None
    if final:
        winner = ("me" if my_points > opp_points else
                  ("opp" if opp_points > my_points else "tie"))
    if final and winner == "me":
        status = "You won — +1 batter pick for next week"
    elif final and winner == "opp":
        status = f"{opp['display_name']} won — no pick this week"
    elif final:
        status = "Tie — no pick for either side"
    elif my_points > opp_points:
        status = f"You're ahead by {my_points - opp_points:.2f}"
    elif opp_points > my_points:
        status = f"You're behind by {opp_points - my_points:.2f}"
    else:
        status = "Tied"
    return {**base, "bye": False, "opponent": opp, "my_points": my_points,
            "opp_points": opp_points, "final": final, "winner": winner,
            "status": status}


def season_fridays(con, season, count=REGULAR_WEEKS):
    """The regular-season Fridays: week 1's friday_date when the weeks
    table has it, else back-computed from the earliest week row."""
    rows = con.execute(
        "SELECT week, friday_date FROM weeks WHERE season = ? ORDER BY week",
        (season,)).fetchall()
    if not rows:
        return []
    start = date.fromisoformat(rows[0]["friday_date"]) - timedelta(
        days=7 * (rows[0]["week"] - 1))
    return [(start + timedelta(days=7 * i)).isoformat()
            for i in range(count)]


def playoff_labels(con, season):
    """'Sep 17 + Sep 24' — the two Fridays after the 25 regular weeks."""
    fridays = season_fridays(con, season, count=REGULAR_WEEKS + 2)
    if len(fridays) < REGULAR_WEEKS + 2:
        return ""
    fmt = lambda iso: date.fromisoformat(iso).strftime("%b %-d")
    return f"{fmt(fridays[REGULAR_WEEKS])} + {fmt(fridays[REGULAR_WEEKS + 1])}"


def schedule_for_member(con, member_id, season, current_week):
    """The member's 25-week schedule. Each row: {week, friday_date,
    opponent (member dict or None), bye, kind ('played'|'upcoming'),
    status ('final'|'live'|'upcoming'|'bye'), my_points, opp_points,
    won, tied}."""
    fridays = season_fridays(con, season)
    ids = active_member_ids(con)
    members = {m["id"]: _with_display(m) for m in active_members(con)}
    rows = []
    for i, friday in enumerate(fridays, start=1):
        pairs, bye_id = pairings_for_week(season, i, ids)
        opp_id = None
        for a, b in pairs:
            if member_id in (a, b):
                opp_id = b if a == member_id else a
        date_label = date.fromisoformat(friday).strftime("%a %b %-d")
        if opp_id is None:
            rows.append({"week": i, "friday_date": friday,
                         "date_label": date_label, "opponent": None,
                         "bye": bye_id == member_id,
                         "kind": "played" if i <= current_week else "upcoming",
                         "status": "bye", "my_points": 0.0,
                         "opp_points": 0.0, "won": False, "tied": False})
            continue
        final = week_final(con, season, i)
        my_points = matchup_points(con, member_id, season, i)
        opp_points = matchup_points(con, opp_id, season, i)
        if final:
            status = "final"
        elif i > current_week:
            status = "upcoming"
        else:
            status = "live"
        rows.append({"week": i, "friday_date": friday,
                     "date_label": date_label,
                     "opponent": members[opp_id], "bye": False,
                     "kind": "played" if i <= current_week else "upcoming",
                     "status": status, "my_points": my_points,
                     "opp_points": opp_points,
                     "won": final and my_points > opp_points,
                     "tied": final and my_points == opp_points})
    return rows
