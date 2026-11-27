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

from datetime import date, datetime, timedelta, timezone

from . import db as dbmod
from . import mlb as mlb_mod
from .picks import _parse_utc
from .scoring import pick_points, week_points

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


def game_started(game: dict, now: datetime | None = None) -> bool:
    """True once the game's first pitch has passed — the same
    determination as the pick freeze in picks.py (game_date_utc vs
    now). A game with no date is never started."""
    if game is None or not game.get("game_date_utc"):
        return False
    now = now or datetime.now(timezone.utc)
    return _parse_utc(game["game_date_utc"]) <= now


def visible_picks(con, viewer_id, member_id, season, week,
                  now: datetime | None = None) -> list:
    """One side's base lineup with the reveal rule applied (Nov 24,
    2026 — the element of surprise). Returns slot order b1..b5, p1;
    each row is {slot, kind, visible, name, team, game_label,
    points}. A pick is visible when it belongs to the viewer or its
    game has started; otherwise the row carries NO name at all — the
    server omits it entirely, so a hidden pick never appears in the
    HTML (no name, no data attribute, no JSON payload). Points come
    from the same pick_points scoring path week_points uses; a hidden
    pick contributes 0 to the side total."""
    now = now or datetime.now(timezone.utc)
    picks = [p for p in
             dbmod.picks_for_member_week(con, member_id, season, week)
             if p["slot"] in BASE_SLOTS]
    games = {g["game_pk"]: g
             for g in dbmod.games_for_week(con, season, week)}
    stats = dbmod.stats_for_week(con, season, week)
    own = viewer_id == member_id
    out = []
    for p in picks:
        game = games.get(p["game_pk"])
        started = game_started(game, now)
        visible = own or started
        row = {"slot": p["slot"], "kind": p["kind"], "visible": visible,
               "game_label": (mlb_mod.first_pitch_et(game["game_date_utc"])
                              if game else ""),
               "name": None, "team": "", "points": 0.0}
        if visible:
            player = dbmod.player_by_id(con, p["mlb_id"])
            row["name"] = player["name"] if player else "Unknown"
            row["team"] = player["team"] if player else ""
            row["points"] = pick_points(
                p["kind"], stats.get((p["game_pk"], p["mlb_id"])))
        out.append(row)
    return out


def matchup_detail(con, viewer_id, season, week, a_id, b_id,
                   now: datetime | None = None):
    """Detail for one pairing of the week, or None when a/b aren't
    actually matched that week. {a, b (member dicts), a_picks,
    b_picks, a_total, b_total (revealed points only), final,
    verdict} — verdict is a plain-language line: once final,
    '<name> wins — +1 pick' or 'Tie — no pick'; while live,
    '<name> leads by X.XX' or 'Tied'."""
    pairs, _bye = pairings_for_week(season, week, active_member_ids(con))
    if (a_id, b_id) not in pairs and (b_id, a_id) not in pairs:
        return None
    if (b_id, a_id) in pairs:
        a_id, b_id = b_id, a_id
    members = {m["id"]: _with_display(m) for m in active_members(con)}
    if a_id not in members or b_id not in members:
        return None
    a_picks = visible_picks(con, viewer_id, a_id, season, week, now)
    b_picks = visible_picks(con, viewer_id, b_id, season, week, now)
    a_total = round(sum(p["points"] for p in a_picks if p["visible"]), 2)
    b_total = round(sum(p["points"] for p in b_picks if p["visible"]), 2)
    final = week_final(con, season, week)
    a_name, b_name = members[a_id]["display_name"], members[b_id]["display_name"]
    if final:
        if a_total > b_total:
            verdict = f"{a_name} wins — +1 batter pick for next week"
        elif b_total > a_total:
            verdict = f"{b_name} wins — +1 batter pick for next week"
        else:
            verdict = "Tie — no pick"
    else:
        if a_total > b_total:
            verdict = f"{a_name} leads by {a_total - b_total:.2f}"
        elif b_total > a_total:
            verdict = f"{b_name} leads by {b_total - a_total:.2f}"
        else:
            verdict = "Tied"
    return {"a": members[a_id], "b": members[b_id],
            "a_picks": a_picks, "b_picks": b_picks,
            "a_total": a_total, "b_total": b_total, "final": final,
            "verdict": verdict}


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


def week_started(con, season, week, now: datetime | None = None) -> bool:
    """True once any game in the week has started (first pitch passed).
    Before that the matchup drop-down shows a preview, not a scoreboard."""
    games = dbmod.games_for_week(con, season, week)
    return any(game_started(g, now) for g in games)


# ---------------------------------------------------------------- preview
# Shown in the same drop-down before any game starts (Nov 27, 2026):
# the previous meeting between the two members, their all-time record,
# season records, and form. Everything is derived from stored picks and
# stats — a missing value is omitted, never invented.

def _played_final_weeks(con, member_id, season, current_week):
    """Final weeks before `current_week` in which the member was paired,
    newest first: {week, points, opp_points, outcome ('win'|'loss'|
    'tie')}. A bye week never appears — the member simply didn't play."""
    ids = active_member_ids(con)
    out = []
    for w in range(current_week - 1, 0, -1):
        if not week_final(con, season, w):
            continue
        pairs, _bye = pairings_for_week(season, w, ids)
        for a, b in pairs:
            if member_id in (a, b):
                opp = b if a == member_id else a
                mine = matchup_points(con, member_id, season, w)
                theirs = matchup_points(con, opp, season, w)
                outcome = ("win" if mine > theirs else
                           ("loss" if theirs > mine else "tie"))
                out.append({"week": w, "points": mine,
                            "opp_points": theirs, "outcome": outcome})
    return out


def _member_form(con, member_id, season, current_week):
    """One member's form line, or None when they have no completed
    matchup week yet. {week, points, text} — text is 'Won N straight'
    on a win streak, 'Won last week' on a single win, 'Lost last week'
    or 'Tied last week' otherwise. Points are the last played week's
    matchup points (base lineup, same as the matchup itself)."""
    played = _played_final_weeks(con, member_id, season, current_week)
    if not played:
        return None
    last = played[0]
    if last["outcome"] == "win":
        n = 0
        for r in played:
            if r["outcome"] == "win":
                n += 1
            else:
                break
        text = f"Won {n} straight" if n > 1 else "Won last week"
    elif last["outcome"] == "loss":
        text = "Lost last week"
    else:
        text = "Tied last week"
    return {"week": last["week"], "points": last["points"], "text": text}


def _decided_line(con, winner_id, season, week):
    """One line on what decided a completed meeting: the winner's
    top-scoring pick that week. Returns None when the winner scored
    nothing (no pick to name) — the line is omitted, not filled in."""
    picks = [p for p in
             dbmod.picks_for_member_week(con, winner_id, season, week)
             if p["slot"] in BASE_SLOTS]
    stats = dbmod.stats_for_week(con, season, week)
    best = None
    for p in picks:
        pts = pick_points(p["kind"],
                          stats.get((p["game_pk"], p["mlb_id"])))
        if best is None or pts > best[0]:
            best = (pts, p)
    if best is None or best[0] <= 0:
        return None
    _pts, p = best
    player = dbmod.player_by_id(con, p["mlb_id"])
    name = player["name"] if player else "Unknown"
    stat = stats.get((p["game_pk"], p["mlb_id"])) or {}
    if p["kind"] == "batter":
        hits = stat.get("hits", 0)
        return (f"{name}'s {hits} hit{'s' if hits != 1 else ''}"
                " led the way")
    k = stat.get("strikeouts", 0)
    return (f"{name}'s {k} strikeout{'s' if k != 1 else ''}"
            " led the way")


def _record_line(rec) -> str:
    """'3–1' or '3–1–1' when ties exist."""
    line = f"{rec['wins']}–{rec['losses']}"
    if rec["ties"]:
        line += f"–{rec['ties']}"
    return line


def matchup_preview(con, season, week, a_id, b_id):
    """Preview for an upcoming pairing of the week, or None when a/b
    aren't actually matched that week. {kind: 'preview', a, b (member
    dicts), first_meeting, previous, h2h_line, record_line,
    a_form_line, b_form_line} — previous is None on a first meeting;
    previous_line/decided live on it when present. Display strings are
    built here so the template stays dumb."""
    pairs, _bye = pairings_for_week(season, week, active_member_ids(con))
    if (a_id, b_id) not in pairs and (b_id, a_id) not in pairs:
        return None
    if (b_id, a_id) in pairs:
        a_id, b_id = b_id, a_id
    members = {m["id"]: _with_display(m) for m in active_members(con)}
    if a_id not in members or b_id not in members:
        return None
    a_name, b_name = (members[a_id]["display_name"],
                      members[b_id]["display_name"])
    # History: final weeks before this one where the pair met.
    a_wins = b_wins = ties = 0
    previous = None
    for w in range(week - 1, 0, -1):
        if not week_final(con, season, w):
            continue
        wpairs, _ = pairings_for_week(season, w, active_member_ids(con))
        if (a_id, b_id) not in wpairs and (b_id, a_id) not in wpairs:
            continue
        pa = matchup_points(con, a_id, season, w)
        pb = matchup_points(con, b_id, season, w)
        if pa > pb:
            a_wins += 1
            winner, winner_id = "a", a_id
        elif pb > pa:
            b_wins += 1
            winner, winner_id = "b", b_id
        else:
            ties += 1
            winner, winner_id = "tie", None
        if previous is None:
            if winner == "a":
                line = (f"Week {w} — {a_name} won {pa:.2f} – {pb:.2f}")
            elif winner == "b":
                line = (f"Week {w} — {b_name} won {pb:.2f} – {pa:.2f}")
            else:
                line = f"Week {w} — Tie {pa:.2f} – {pb:.2f}"
            previous = {"week": w, "a_points": pa, "b_points": pb,
                        "winner": winner, "line": line,
                        "decided": (_decided_line(con, winner_id, season, w)
                                    if winner_id else None)}
    if a_wins + b_wins + ties == 0:
        h2h_line = "No meetings yet"
    elif a_wins == b_wins:
        h2h_line = f"Tied {a_wins} – {b_wins}"
    elif a_wins > b_wins:
        h2h_line = f"{a_name} leads {a_wins} – {b_wins}"
    else:
        h2h_line = f"{b_name} leads {b_wins} – {a_wins}"
    if ties:
        h2h_line += f" · {ties} tie{'s' if ties != 1 else ''}"
    records = {r["member"]["id"]: r
               for r in head_to_head_records(con, season, week)}
    a_rec = records.get(a_id, {"wins": 0, "losses": 0, "ties": 0})
    b_rec = records.get(b_id, {"wins": 0, "losses": 0, "ties": 0})
    record_line = (f"{a_name} {_record_line(a_rec)} · "
                   f"{b_name} {_record_line(b_rec)}")

    def _form_line(member_id):
        form = _member_form(con, member_id, season, week)
        if form is None:
            return None
        return (f"Last week {form['points']:.2f} pts · {form['text']}")

    return {"kind": "preview", "a": members[a_id], "b": members[b_id],
            "first_meeting": previous is None,
            "previous": previous, "h2h_line": h2h_line,
            "record_line": record_line,
            "a_form_line": _form_line(a_id),
            "b_form_line": _form_line(b_id)}


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
