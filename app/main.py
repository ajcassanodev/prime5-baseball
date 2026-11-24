"""2027 Prime5 Baseball — core app (scaffold).

Pick page only: 5 batters + 1 Friday starting pitcher, lineup dots,
game times + opposing pitchers, lock when the set is complete.
No captain, streak, milestone, or payments — those are later
sprinkle-ins.

Registration: public /register page (invite -> register -> commissioner
approval -> pick link). Admin: /admin/<ADMIN_TOKEN> lists pending
registrations with Approve / Decline.

Auth: member pick links. /submit/<token> sets a host-only cookie
(180 days); every page requires it, otherwise /locked. Pending or
declined members cannot sign in — their token works only once approved.
"""
import os
import secrets

from datetime import date, timedelta

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db as dbmod
from . import emailer
from . import lineup as lineup_mod
from . import mlb as mlb_mod
from . import picks as picks_mod
from .scoring import batter_points, pitcher_points, week_points

COOKIE_NAME = "prime5bb_pick"
COOKIE_DAYS = 180
BASE_URL = os.environ.get("PRIME5BB_BASE_URL", "http://127.0.0.1:8000")

app = FastAPI(title="Prime5 Baseball")
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")),
          name="static")
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))

# Ensure the schema (incl. migrations for DBs created before a column
# existed, e.g. members.seen_welcome) before serving any request.
dbmod.init_db()


# ---------------------------------------------------------------- auth

def _current_member(request: Request):
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    con = dbmod.connect()
    try:
        member = dbmod.member_by_token(con, token)
    finally:
        con.close()
    if member is None:
        return None
    # Display values for the header/dashboard templates: screen name
    # with the short name (or full name, when opted in) underneath.
    member["short_name"] = dbmod.short_name(member["name"])
    member["display_name"] = dbmod.display_name(member)
    member["display_sub"] = dbmod.display_sub(member)
    member["division"] = member.get("division") or "red"
    member["division_name"] = dbmod.division_display(member["division"])
    return member


def _need_member(request: Request):
    return _current_member(request)


def _current_week_or_none(con):
    season = dbmod.current_season(con)
    week = dbmod.current_week(con, season)
    return season, week


# Cache-busting for the stylesheet (Nov 24, 2026): browsers cached the
# old style.css after the header redesign and showed the new header
# unstyled. The version is the file's mtime, read at process start — a
# deploy restarts the service, so changed CSS always gets a fresh
# version and browsers fetch it once.
_STYLE_PATH = os.path.join(os.path.dirname(__file__), "static", "style.css")
try:
    CSS_VERSION = str(int(os.path.getmtime(_STYLE_PATH)))
except OSError:
    CSS_VERSION = "1"


def _render(request, name, context, status_code=200):
    """TemplateResponse with the current Starlette signature."""
    context = {**context, "css_version": CSS_VERSION}
    return templates.TemplateResponse(request, name, context,
                                      status_code=status_code)


# ---------------------------------------------------------------- pages

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    member = _current_member(request)
    if member is None:
        return RedirectResponse("/locked", status_code=303)
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/submit/{token}")
def submit_link(token: str):
    con = dbmod.connect()
    try:
        member = dbmod.member_by_token(con, token)
    finally:
        con.close()
    if member is None:
        return RedirectResponse("/locked", status_code=303)
    resp = RedirectResponse("/dashboard", status_code=303)
    resp.set_cookie(COOKIE_NAME, token, max_age=COOKIE_DAYS * 86400,
                    httponly=True, path="/")
    return resp


@app.get("/locked", response_class=HTMLResponse)
def locked(request: Request):
    return _render(request, "locked.html", {})


# ---------------------------------------------------------------- registration

def _register_form(first_name="", last_name="", screen_name="", email="",
                   phone="", returning="", referrer="", invite_token=""):
    """Form values round-tripped back to the register template."""
    return {"first_name": first_name, "last_name": last_name,
            "screen_name": screen_name, "email": email, "phone": phone,
            "returning": returning, "referrer": referrer,
            "invite_token": invite_token}


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request, invite: str = "", email: str = ""):
    """Public registration page. The invite link Adam sends can carry
    ?invite=<token> (stored on the member record) and ?email=<email>
    to prefill the email field."""
    return _render(request, "register.html",
                   {"error": None,
                    "form": _register_form(email=email, invite_token=invite)})


@app.post("/register", response_class=HTMLResponse)
def register_submit(request: Request,
                    first_name: str = Form(""), last_name: str = Form(""),
                    screen_name: str = Form(""), email: str = Form(""),
                    phone: str = Form(""),
                    returning: str = Form(""), referrer: str = Form(""),
                    invite_token: str = Form("")):
    first_name = first_name.strip()
    last_name = last_name.strip()
    screen_name = screen_name.strip()
    email = emailer.valid_email(email)
    phone = phone.strip()
    referrer = referrer.strip()
    invite_token = invite_token.strip()
    played_2026_val = {"yes": 1, "no": 0}.get(returning.strip().lower())
    form = _register_form(first_name, last_name, screen_name, email or "",
                          phone, returning, referrer, invite_token)
    if not first_name or not last_name:
        return _render(request, "register.html",
                       {"error": "First and last name are both required.",
                        "form": form})
    if email is None:
        return _render(request, "register.html",
                       {"error": "A valid email address is required —"
                        " your pick link goes there once you're approved.",
                        "form": form})
    screen_error = dbmod.validate_screen_name(screen_name)
    if screen_error is not None:
        return _render(request, "register.html",
                       {"error": screen_error, "form": form})
    con = dbmod.connect()
    try:
        existing = dbmod.member_by_email(con, email)
        if existing is not None:
            if existing["status"] == "active":
                return _render(request, "register_pending.html",
                               {"heading": "You're already registered",
                                "message": "This email already has a pick"
                                " link — check your inbox (and spam) for"
                                " your Prime5 Baseball pick link. If you"
                                " lost it, ask the commissioner (Adam)"
                                " for a new one.",
                                "note": ""})
            if existing["status"] == "pending":
                return _render(request, "register_pending.html",
                               {"heading": "Request received",
                                "message": "Adam reviews every registration."
                                " Once you're approved, your personal pick"
                                " link arrives by email.",
                                "note": "Your earlier request for this email"
                                " is still waiting — no need to send"
                                " another one."})
            # Declined before: reopen the same record as pending with the
            # new details instead of creating a duplicate. The screen
            # name must be free for someone else to hold (their own
            # previous one still counts as theirs).
            if not dbmod.screen_name_available(con, screen_name,
                                               exclude_member_id=existing["id"]):
                return _render(request, "register.html",
                               {"error": "That screen name is already taken"
                                " — try another one.",
                                "form": form})
            dbmod.set_member_status(con, existing["id"], "pending")
            con.execute(
                "UPDATE members SET name = ?, phone = ?, played_2026 = ?,"
                " referrer = ?, invite_token = ?, screen_name = ?"
                " WHERE id = ?",
                (f"{first_name} {last_name}", phone or None, played_2026_val,
                 referrer or None, invite_token or None, screen_name,
                 existing["id"]),
            )
            con.commit()
        else:
            if not dbmod.screen_name_available(con, screen_name):
                return _render(request, "register.html",
                               {"error": "That screen name is already taken"
                                " — try another one.",
                                "form": form})
            dbmod.create_member(
                con, f"{first_name} {last_name}", email=email,
                status="pending", phone=phone or None,
                played_2026=played_2026_val, referrer=referrer or None,
                invite_token=invite_token or None, screen_name=screen_name,
            )
    finally:
        con.close()
    return _render(request, "register_pending.html",
                   {"heading": "Request received",
                    "message": "Adam reviews every registration. Once"
                    " you're approved, your personal pick link arrives"
                    " by email.",
                    "note": "Your $45 entry is due when the 2027 season"
                    " starts — we'll collect it then."})


# ---------------------------------------------------------------- admin

def _admin_token_ok(token: str) -> bool:
    """Admin gate, mirroring the football app: the token lives in the
    ADMIN_TOKEN env var; without one configured, nobody gets in."""
    admin = os.environ.get("ADMIN_TOKEN", "")
    return bool(admin) and secrets.compare_digest(token, admin)


@app.get("/admin/{token}", response_class=HTMLResponse)
def admin_page(request: Request, token: str):
    if not _admin_token_ok(token):
        return _render(request, "not_found.html", {}, status_code=404)
    con = dbmod.connect()
    try:
        return _render(request, "admin.html",
                       {"token": token,
                        "pending": dbmod.pending_members(con),
                        "approved": dbmod.members_by_status(con, "active"),
                        "declined": dbmod.members_by_status(con, "declined")})
    finally:
        con.close()


def _pick_link(member) -> str:
    return f"{BASE_URL}/submit/{member['pick_token']}"


@app.post("/admin/{token}/approve/{member_id}")
def admin_approve(request: Request, token: str, member_id: int):
    if not _admin_token_ok(token):
        return _render(request, "not_found.html", {}, status_code=404)
    con = dbmod.connect()
    try:
        member = dbmod.member_by_id(con, member_id)
        if member is None or member["status"] != "pending":
            return RedirectResponse(f"/admin/{token}", status_code=303)
        dbmod.set_member_status(con, member_id, "active")
        member = dbmod.member_by_id(con, member_id)
    finally:
        con.close()
    link = _pick_link(member)
    first = member["name"].split(" ")[0]
    sent = emailer.send_email(
        member["email"], "You're approved — your Prime5 Baseball pick link",
        f"Hi {first},\n\n"
        "You're approved for 2027 Prime5 Baseball — welcome in.\n\n"
        "Your personal pick link:\n"
        f"{link}\n\n"
        "Open it to sign in — it signs you in on every Ravix game, no"
        " password needed. Your first visit shows a quick how-to, and"
        " your $45 entry is due when the 2027 season starts.\n\n"
        "See you on the first Friday, March 26.\n\n"
        "Adam\nRavix Fantasy Sports\n",
    )
    if not sent:
        # Mail isn't configured (local dev): never fail the approval —
        # print the link so it can be retrieved and sent another way.
        print(f"[prime5bb] pick link for {member['name']}"
              f" <{member['email']}> (email not configured): {link}",
              flush=True)
    return RedirectResponse(f"/admin/{token}", status_code=303)


@app.post("/admin/{token}/decline/{member_id}")
def admin_decline(request: Request, token: str, member_id: int):
    if not _admin_token_ok(token):
        return _render(request, "not_found.html", {}, status_code=404)
    con = dbmod.connect()
    try:
        member = dbmod.member_by_id(con, member_id)
        if member is not None and member["status"] == "pending":
            dbmod.set_member_status(con, member_id, "declined")
    finally:
        con.close()
    return RedirectResponse(f"/admin/{token}", status_code=303)


def _pick_page_data(con, member, season, week):
    """Assemble pick-page rows grouped by game time."""
    games = dbmod.games_for_week(con, season, week)
    stats = dbmod.stats_for_week(con, season, week)
    mine = {p["slot"]: p for p in
            dbmod.picks_for_member_week(con, member["id"], season, week)}
    # Keyed by (game, player): on a doubleheader day the same player
    # has one row per game, and each row is its own pick.
    my_ids = {(p["game_pk"], p["mlb_id"]): p["slot"] for p in mine.values()}

    groups = []
    for g in games:
        starters = dbmod.game_starter_names(con, g["game_pk"])
        game_lineup = dbmod.lineup_for_game(con, g["game_pk"])
        # Pitcher list = announced starters plus the game's probable
        # starters (pre-game the lineup arrays may not list the pitcher).
        starter_ids = set(game_lineup["starters"]) | {
            g.get("home_starter_mlb_id"), g.get("away_starter_mlb_id")} - {None}
        roster = dbmod.players_for_game(con, g["game_pk"])
        batters, pitchers = [], []
        for player in roster:
            stat = stats.get((g["game_pk"], player["mlb_id"]))
            if player["is_pitcher"]:
                if player["mlb_id"] not in starter_ids:
                    continue  # pitcher list is Friday starters only
                opp = (starters["away"] if player["team"] == g["home_team"]
                       else starters["home"])
                pitchers.append({
                    **player,
                    "game_pk": g["game_pk"],
                    "status": lineup_mod.pitcher_lineup_status(
                        player["mlb_id"], sorted(starter_ids)),
                    "pitches": stat["pitches"] if stat else None,
                    "strikeouts": stat["strikeouts"] if stat else None,
                    "points": (pitcher_points(stat["pitches"], stat["strikeouts"])
                               if stat else None),
                    "vs": opp["name"],
                    "slot": my_ids.get((g["game_pk"], player["mlb_id"])),
                })
            else:
                order = (game_lineup["batting_order"].index(player["mlb_id"])
                         if player["mlb_id"] in game_lineup["batting_order"]
                         else 99)
                opp = (starters["away"] if player["team"] == g["home_team"]
                       else starters["home"])
                batters.append({
                    **player,
                    "game_pk": g["game_pk"],
                    "order": order,
                    "status": lineup_mod.batter_lineup_status(
                        player["mlb_id"], game_lineup["batting_order"]),
                    "hits": stat["hits"] if stat else None,
                    "points": (batter_points(stat["hits"]) if stat else None),
                    "vs": opp["name"],
                    "slot": my_ids.get((g["game_pk"], player["mlb_id"])),
                })
        batters.sort(key=lambda b: (b["status"] != "in", b["order"], b["name"]))
        pitchers.sort(key=lambda p: p["name"])
        groups.append({
            "game_pk": g["game_pk"],
            "label": f"{g['away_team']} @ {g['home_team']}",
            "time_et": mlb_mod.first_pitch_et(g["game_date_utc"]),
            "status": g["status"],
            "batters": batters,
            "pitchers": pitchers,
        })
    return {
        "member": member, "season": season, "week": week,
        "groups": groups,
        "my_batter_count": sum(1 for p in mine.values() if p["kind"] == "batter"),
        "my_pitcher": next((p for p in mine.values() if p["kind"] == "pitcher"), None),
        "complete": len(mine) == 6,
    }


@app.get("/picks", response_class=HTMLResponse)
def pick_page(request: Request):
    member = _need_member(request)
    if member is None:
        return RedirectResponse("/locked", status_code=303)
    con = dbmod.connect()
    try:
        season, week = _current_week_or_none(con)
        if week is None:
            return _render(request, "empty.html",
                           {"member": member,
                            "message": "No Friday scheduled yet."})
        data = _pick_page_data(con, member, season, week["week"])
        data["week_label"] = week.get("label") or f"Week {week['week']}"
        data["playground"] = bool(week.get("playground"))
        data["show_welcome"] = not member.get("seen_welcome")
        return _render(request, "pick.html", data)
    finally:
        con.close()


@app.post("/welcome-seen")
def welcome_seen(request: Request):
    """Dismiss the first-visit welcome pop-up; persists per member."""
    member = _need_member(request)
    if member is None:
        return RedirectResponse("/locked", status_code=303)
    con = dbmod.connect()
    try:
        dbmod.mark_welcome_seen(con, member["id"])
    finally:
        con.close()
    return {"ok": True}


@app.post("/settings")
async def update_settings(request: Request):
    """Flip the signed-in member's "Show my full name" setting.
    Body: {"show_full_name": true/false}."""
    member = _need_member(request)
    if member is None:
        return RedirectResponse("/locked", status_code=303)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"detail": "invalid JSON"}, status_code=400)
    value = body.get("show_full_name") if isinstance(body, dict) else None
    if not isinstance(value, bool):
        return JSONResponse(
            {"detail": "show_full_name must be true or false"},
            status_code=400)
    con = dbmod.connect()
    try:
        dbmod.set_show_full_name(con, member["id"], value)
    finally:
        con.close()
    return {"ok": True, "show_full_name": value}


@app.post("/picks", response_class=HTMLResponse)
def submit_picks(request: Request,
                 batter_1: int = Form(0), batter_2: int = Form(0),
                 batter_3: int = Form(0), batter_4: int = Form(0),
                 batter_5: int = Form(0),
                 batter_1_game: int = Form(0), batter_2_game: int = Form(0),
                 batter_3_game: int = Form(0), batter_4_game: int = Form(0),
                 batter_5_game: int = Form(0),
                 pitcher: int = Form(0)):
    member = _need_member(request)
    if member is None:
        return RedirectResponse("/locked", status_code=303)
    con = dbmod.connect()
    try:
        season, week = _current_week_or_none(con)
        if week is None:
            return RedirectResponse("/picks", status_code=303)
        batter_picks = [(b, g) for b, g in
                        ((batter_1, batter_1_game), (batter_2, batter_2_game),
                         (batter_3, batter_3_game), (batter_4, batter_4_game),
                         (batter_5, batter_5_game)) if b]
        try:
            picks_mod.submit_picks(con, member["id"], season, week["week"],
                                   batter_picks, pitcher or None)
            return RedirectResponse("/dashboard", status_code=303)
        except picks_mod.PickError as exc:
            data = _pick_page_data(con, member, season, week["week"])
            data["week_label"] = week.get("label") or f"Week {week['week']}"
            data["playground"] = bool(week.get("playground"))
            data["show_welcome"] = not member.get("seen_welcome")
            data["error"] = str(exc)
            return _render(request, "pick.html", data)
    finally:
        con.close()


def _ordinal(n: int) -> str:
    """1 -> '1st', 2 -> '2nd', 3 -> '3rd', 4 -> '4th', 11 -> '11th'."""
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _week_total(con, member_id: int, season: int, week_num: int) -> float:
    picks = dbmod.picks_for_member_week(con, member_id, season, week_num)
    stats = dbmod.stats_for_week(con, season, week_num)
    return week_points(
        [{"kind": p["kind"], "mlb_id": p["mlb_id"], "game_pk": p["game_pk"]}
         for p in picks], stats)


def _next_lock_label(friday_date: str) -> str:
    """'Next Friday · 1:05 PM ET' — earliest first pitch of the next
    Friday from the MLB schedule. Falls back to plain 'Next Friday'
    when the schedule is unavailable; never blocks the dashboard."""
    try:
        nxt = (date.fromisoformat(friday_date) + timedelta(days=7)).isoformat()
        games = mlb_mod.parse_schedule(mlb_mod.fetch_schedule(nxt))
        if not games:
            return "Next Friday"
        earliest = min(g["game_date_utc"] for g in games)
        return f"Next Friday · {mlb_mod.first_pitch_et(earliest)} ET"
    except Exception:
        return "Next Friday"


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    member = _need_member(request)
    if member is None:
        return RedirectResponse("/locked", status_code=303)
    con = dbmod.connect()
    try:
        season, week = _current_week_or_none(con)
        if week is None:
            return _render(request, "empty.html",
                           {"member": member,
                            "message": "No Friday scheduled yet."})
        picks = dbmod.picks_for_member_week(con, member["id"], season,
                                            week["week"])
        stats = dbmod.stats_for_week(con, season, week["week"])
        games = {g["game_pk"]: g for g in
                 dbmod.games_for_week(con, season, week["week"])}
        rows = []
        for p in picks:
            player = dbmod.player_by_id(con, p["mlb_id"]) or {}
            stat = stats.get((p["game_pk"], p["mlb_id"]))
            pts = 0.0
            detail = "no stats yet"
            if stat:
                if p["kind"] == "batter":
                    pts = batter_points(stat["hits"])
                    detail = f"{stat['hits']} H"
                else:
                    pts = pitcher_points(stat["pitches"], stat["strikeouts"])
                    detail = f"{stat['pitches']} pitches · {stat['strikeouts']} K"
            game = games.get(p["game_pk"], {})
            rows.append({
                "slot": p["slot"], "kind": p["kind"],
                "name": player.get("name", "?"), "team": player.get("team", "?"),
                "game_label": (f"{game.get('away_team', '?')} @ "
                               f"{game.get('home_team', '?')}"
                               if game else "?"),
                "time_et": (mlb_mod.first_pitch_et(game["game_date_utc"])
                            if game else "?"),
                "detail": detail, "points": pts,
            })
        total = week_points(
            [{"kind": p["kind"], "mlb_id": p["mlb_id"], "game_pk": p["game_pk"]}
             for p in picks], stats)
        # The dashboard is division-scoped: rank, gap and the top-5
        # standings card all come from the viewing member's division.
        division = member.get("division") or "red"
        div_rows = [r for r in dbmod.standings(con, season, week["week"])
                    if r.get("division") == division]
        rank = next((i + 1 for i, r in enumerate(div_rows)
                     if r["member_id"] == member["id"]), None)
        leader = div_rows[0] if div_rows else None
        gap = (leader["points"] - total) if leader else 0.0
        rank_label = (f"{_ordinal(rank)} in "
                      f"{dbmod.division_display(division)}" if rank else "")
        # Season points by week (one slice per week played).
        season_pie = []
        for w in dbmod.all_weeks(con, season):
            if w["week"] > week["week"]:
                continue
            wtotal = _week_total(con, member["id"], season, w["week"])
            if wtotal > 0:
                name = ("Playground week" if w.get("playground")
                        else (w.get("label") or f"Week {w['week']}"))
                season_pie.append({"name": name, "points": wtotal})
        # This week by player, biggest slice first (stable sort keeps
        # slot order on ties); zero-point players don't chart.
        week_pie = sorted(
            ({"name": r["name"], "points": r["points"]} for r in rows
             if r["points"] > 0),
            key=lambda d: -d["points"])
        week_date_label = date.fromisoformat(
            week["friday_date"]).strftime("%a %b %-d").upper()
        return _render(request, "dashboard.html",
                       {"member": member, "season": season, "week": week,
                        "week_label": week.get("label") or f"Week {week['week']}",
                        "week_date_label": week_date_label,
                        "playground": bool(week.get("playground")),
                        "rows": rows, "total": total, "complete": len(picks) == 6,
                        "rank": rank, "gap": gap,
                        "leader_name": leader["display_name"] if leader else None,
                        "rank_label": rank_label,
                        "division_name": dbmod.division_display(division),
                        "division_class": f"div-{division}",
                        "div_rows": div_rows[:5],
                        "season_pie": season_pie, "week_pie": week_pie,
                        "top": dbmod.top_performers(con, season, week["week"]),
                        "streak": dbmod.hit_streak(con, member["id"], season,
                                                   week["week"]),
                        "next_lock": _next_lock_label(week["friday_date"])})
    finally:
        con.close()


@app.get("/standings", response_class=HTMLResponse)
def standings(request: Request):
    member = _need_member(request)
    if member is None:
        return RedirectResponse("/locked", status_code=303)
    con = dbmod.connect()
    try:
        season, week = _current_week_or_none(con)
        if week is None:
            return _render(request, "empty.html",
                           {"member": member,
                            "message": "No Friday scheduled yet."})
        rows = dbmod.standings(con, season, week["week"])
        return _render(request, "standings.html",
                       {"member": member, "season": season, "week": week,
                        "week_label": week.get("label") or f"Week {week['week']}",
                        "rows": rows})
    finally:
        con.close()


@app.get("/health")
def health():
    return {"ok": True}
