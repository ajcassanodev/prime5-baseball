"""MLB Stats API client (free, no key) — statsapi.mlb.com/api/v1.

Endpoints used (verified live Nov 2026):
- /schedule?sportId=1&date=YYYY-MM-DD&hydrate=lineups,probablePitcher
  -> games with full starting lineups (homePlayers / awayPlayers) once
     teams announce them (a few hours before first pitch), plus probable
     pitchers. Lineups are NOT available at pick time during the week.
- /game/{gamePk}/boxscore
  -> 9-man batting order, per-player batting stats (hits/HR/RBI) and
     pitching stats (numberOfPitches, strikeOuts). Also lists the full
     game roster, including bench players who did not play.
- /people/{id}/stats?stats=gameLog&group=hitting|pitching&season=YYYY
  -> per-game hitting / pitching game logs.
- /api/v1.1/game/{gamePk}/feed/live (note: v1.1, not v1)
  -> live feed; use for in-game freshness checks.

No SLA, undocumented, commercial-use unguaranteed. The parsing helpers
(parse_schedule / parse_boxscore / parse_game_log) are pure functions so
tests can run on fixture dicts with no network.
"""
import json
import urllib.error
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

BASE_URL = "https://statsapi.mlb.com/api/v1"
LIVE_BASE_URL = "https://statsapi.mlb.com/api/v1.1"
ET = ZoneInfo("America/New_York")

# Team id -> abbreviation (MLB Stats API team ids). The schedule endpoint
# returns team ids/names but not abbreviations; the boxscore does.
TEAM_ABBR = {
    108: "LAA", 109: "ARI", 110: "BAL", 111: "BOS", 112: "CHC",
    113: "CIN", 114: "CLE", 115: "COL", 116: "DET", 117: "HOU",
    118: "KC", 119: "LAD", 120: "WSH", 121: "NYM", 133: "ATH",
    134: "PIT", 135: "SD", 136: "SEA", 137: "SF", 138: "STL",
    139: "TB", 140: "TEX", 141: "TOR", 142: "MIN", 143: "PHI",
    144: "ATL", 145: "CWS", 146: "MIA", 147: "NYY", 158: "MIL",
}


class MlbError(Exception):
    """Raised when the MLB Stats API call fails."""


def _get_json(url: str, timeout: int = 20) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "prime5-baseball/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        raise MlbError(f"MLB Stats API request failed: {url}: {exc}") from exc


def fetch_schedule(date: str, hydrate: str = "lineups,probablePitcher") -> dict:
    """Raw schedule payload for one date (YYYY-MM-DD)."""
    return _get_json(
        f"{BASE_URL}/schedule?sportId=1&date={date}&hydrate={hydrate}")


def fetch_boxscore(game_pk: int) -> dict:
    """Raw boxscore payload for one game."""
    return _get_json(f"{BASE_URL}/game/{game_pk}/boxscore")


def fetch_game_log(person_id: int, group: str, season: int) -> dict:
    """Raw gameLog payload (group 'hitting' or 'pitching')."""
    return _get_json(
        f"{BASE_URL}/people/{person_id}/stats?stats=gameLog"
        f"&group={group}&season={season}")


def fetch_people_stats(person_ids: list, group: str, season: int,
                       chunk: int = 100) -> dict:
    """Batched season stats for many players in one call per chunk.

    /people?personIds=...&hydrate=stats(group=[hitting|pitching],
    type=[season],season=YYYY) returns each player's season splits
    inline — far cheaper than one call per player. Returns the raw
    payloads concatenated (a list of 'people' dicts).
    """
    from urllib.parse import quote
    people = []
    for i in range(0, len(person_ids), chunk):
        ids = ",".join(str(p) for p in person_ids[i:i + chunk])
        hydrate = quote(
            f"stats(group=[{group}],type=[season],season={season})",
            safe="")
        payload = _get_json(
            f"{BASE_URL}/people?personIds={ids}&hydrate={hydrate}")
        people.extend(payload.get("people", []))
    return {"people": people}


def _ip_to_outs(ip) -> int:
    """Innings pitched as MLB reports it ('101.1' = 101 innings + 1
    out) -> total outs. Accepts a string or a number."""
    text = str(ip or "0")
    if "." in text:
        whole, frac = text.split(".", 1)
        return int(whole or 0) * 3 + int(frac or 0)
    return int(text or 0) * 3


def parse_people_stats(raw: dict, group: str) -> dict:
    """Batched /people stats payload -> {mlb_id: season stat dict}.

    group 'hitting': {avg, at_bats, hits}.
    group 'pitching': {era, strikeouts, ip_outs, games_started,
    games_played} — splits (a player traded mid-season has one per
    team) are summed; ERA is recomputed from summed earned runs and
    innings when the splits carry earnedRuns, else the IP-weighted
    mean of the split ERAs.
    """
    out = {}
    for person in raw.get("people", []):
        mlb_id = person.get("id")
        for s in person.get("stats", []):
            splits = s.get("splits", [])
            if group == "hitting":
                ab = hits = 0
                avg = None
                for sp in splits:
                    stat = sp.get("stat", {})
                    ab += stat.get("atBats", 0) or 0
                    hits += stat.get("hits", 0) or 0
                    if avg is None and stat.get("avg"):
                        try:
                            avg = float(stat["avg"])
                        except (TypeError, ValueError):
                            avg = None
                if avg is None and ab:
                    avg = hits / ab
                out[mlb_id] = {"avg": avg, "at_bats": ab, "hits": hits}
            else:
                so = outs = gs = games = 0
                earned = 0.0
                era_weighted = 0.0
                era_outs = 0
                for sp in splits:
                    stat = sp.get("stat", {})
                    sp_outs = _ip_to_outs(stat.get("inningsPitched"))
                    so += stat.get("strikeOuts", 0) or 0
                    outs += sp_outs
                    gs += stat.get("gamesStarted", 0) or 0
                    games += stat.get("gamesPlayed", 0) or 0
                    if stat.get("earnedRuns") is not None:
                        earned += stat["earnedRuns"]
                    if stat.get("era") is not None:
                        try:
                            era_weighted += float(stat["era"]) * sp_outs
                            era_outs += sp_outs
                        except (TypeError, ValueError):
                            pass
                if earned and outs:
                    era = earned * 27.0 / outs
                elif era_outs:
                    era = era_weighted / era_outs
                else:
                    era = None
                out[mlb_id] = {"era": era, "strikeouts": so,
                               "ip_outs": outs, "games_started": gs,
                               "games_played": games}
    return out


def fetch_live_feed(game_pk: int) -> dict:
    """Raw live feed payload (v1.1)."""
    return _get_json(f"{LIVE_BASE_URL}/game/{game_pk}/feed/live")


def first_pitch_et(game_date_utc: str) -> str:
    """'2026-09-25T17:05:00Z' -> '1:05 PM' (America/New_York)."""
    dt = datetime.fromisoformat(game_date_utc.replace("Z", "+00:00"))
    local = dt.astimezone(ET)
    return local.strftime("%-I:%M %p")


def _person(row: dict) -> dict:
    pos = (row.get("position") or {}).get("abbreviation") or ""
    return {
        "mlb_id": row["person"]["id"] if "person" in row else row["id"],
        "name": row.get("fullName") or row["person"]["fullName"],
        "position": pos,
        "is_pitcher": pos == "P",
    }


def parse_schedule(raw: dict) -> list:
    """Schedule payload -> list of game dicts.

    Each game: game_pk, game_date_utc, status, home/away {team_id, abbr},
    home_starter / away_starter ({mlb_id, name} or None), and
    lineups {home: [player dicts], away: [player dicts]} from the
    hydrate=lineups homePlayers / awayPlayers arrays (empty until the
    teams announce them).
    """
    games = []
    for day in raw.get("dates", []):
        for g in day.get("games", []):
            home = g["teams"]["home"]
            away = g["teams"]["away"]
            lineups = g.get("lineups") or {}
            games.append({
                "game_pk": g["gamePk"],
                "game_date_utc": g["gameDate"],
                "status": g["status"]["detailedState"],
                "home": {
                    "team_id": home["team"]["id"],
                    "abbr": TEAM_ABBR.get(home["team"]["id"], "?"),
                },
                "away": {
                    "team_id": away["team"]["id"],
                    "abbr": TEAM_ABBR.get(away["team"]["id"], "?"),
                },
                "home_starter": _probable(home),
                "away_starter": _probable(away),
                "lineups": {
                    "home": [_person(p) for p in lineups.get("homePlayers", [])],
                    "away": [_person(p) for p in lineups.get("awayPlayers", [])],
                },
            })
    return games


def _probable(team_row: dict):
    pp = team_row.get("probablePitcher")
    if not pp:
        return None
    return {"mlb_id": pp["id"], "name": pp["fullName"]}


def parse_boxscore(raw: dict) -> dict:
    """Boxscore payload -> game roster, batting orders, starters, stats.

    Returns: {game_pk, batting_order: {home: [mlb_id...], away: [...]},
    starters: [mlb_id...], players: [{mlb_id, name, team, position,
    is_pitcher}], stats: [{mlb_id, at_bats, hits, hr, rbi, pitches,
    strikeouts}]}. Players listed with no stats are bench players who
    did not appear (the gray-dot case).
    """
    game_pk = raw.get("gamePk")
    batting_order = {"home": [], "away": []}
    starters: list = []
    players: list = []
    stats: list = []
    for side in ("home", "away"):
        team = raw["teams"][side]
        abbr = team["team"]["abbreviation"]
        batting_order[side] = list(team.get("battingOrder", []))
        for _key, p in team["players"].items():
            person = p["person"]
            pos = (p.get("position") or {}).get("abbreviation") or ""
            players.append({
                "mlb_id": person["id"],
                "name": person["fullName"],
                "team": abbr,
                "position": pos,
                "is_pitcher": pos == "P",
            })
            bat = (p.get("stats") or {}).get("batting", {}) or {}
            pit = (p.get("stats") or {}).get("pitching", {}) or {}
            if pit.get("gamesStarted"):
                starters.append(person["id"])
            if bat or pit:
                stats.append({
                    "mlb_id": person["id"],
                    "at_bats": bat.get("atBats", 0),
                    "hits": bat.get("hits", 0),
                    "hr": bat.get("homeRuns", 0),
                    "rbi": bat.get("rbi", 0),
                    "pitches": pit.get("numberOfPitches", 0),
                    "strikeouts": pit.get("strikeOuts", 0),
                })
    return {
        "game_pk": game_pk,
        "batting_order": batting_order,
        "starters": starters,
        "players": players,
        "stats": stats,
    }


def parse_game_log(raw: dict, group: str) -> list:
    """gameLog payload -> per-game stat dicts, newest last.

    group 'hitting': {game_pk, date, at_bats, hits, hr, rbi}.
    group 'pitching': {game_pk, date, pitches, strikeouts}.
    """
    out = []
    for split in (raw.get("stats") or [{}])[0].get("splits", []):
        stat = split.get("stat", {})
        game = split.get("game", {})
        if group == "hitting":
            out.append({
                "game_pk": game.get("gamePk"),
                "date": split.get("date"),
                "at_bats": stat.get("atBats", 0),
                "hits": stat.get("hits", 0),
                "hr": stat.get("homeRuns", 0),
                "rbi": stat.get("rbi", 0),
            })
        else:
            out.append({
                "game_pk": game.get("gamePk"),
                "date": split.get("date"),
                "pitches": stat.get("numberOfPitches", 0),
                "strikeouts": stat.get("strikeOuts", 0),
            })
    return out
