"""SQLite helpers for prime5 baseball. Points are never stored; see scoring.py."""
import os
import secrets
import sqlite3
from datetime import datetime, timezone

DB_PATH = os.environ.get(
    "PRIME5BB_DB",
    os.path.join(os.path.dirname(__file__), "..", "baseball.db"),
)
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "..", "schema.sql")


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_db() -> None:
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    con = connect()
    with open(SCHEMA_PATH) as f:
        con.executescript(f.read())
    # Migrate DBs created before a column existed (e.g. seen_welcome,
    # registration fields).
    cols = [r[1] for r in con.execute("PRAGMA table_info(members)")]
    if "seen_welcome" not in cols:
        con.execute(
            "ALTER TABLE members ADD COLUMN seen_welcome"
            " INTEGER NOT NULL DEFAULT 0")
    for col, ddl in (
        ("status", "TEXT NOT NULL DEFAULT 'active'"),
        ("phone", "TEXT"),
        ("played_2026", "INTEGER"),
        ("referrer", "TEXT"),
        ("invite_token", "TEXT"),
    ):
        if col not in cols:
            con.execute(f"ALTER TABLE members ADD COLUMN {col} {ddl}")
    con.commit()
    con.close()


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- members

MEMBER_STATUSES = ("pending", "active", "declined")


def create_member(con, name: str, email: str | None = None,
                  is_test: bool = False, status: str = "active",
                  phone: str | None = None, played_2026: int | None = None,
                  referrer: str | None = None,
                  invite_token: str | None = None) -> dict:
    """Create a member and return {id, name, pick_token, is_test, status}.

    Pending members get a pick token at registration, but it is unusable
    until approval flips them active (member_by_token refuses them).
    """
    if status not in MEMBER_STATUSES:
        raise ValueError(f"unknown member status: {status!r}")
    token = secrets.token_urlsafe(24)
    cur = con.execute(
        "INSERT INTO members (name, email, pick_token, is_test, is_active,"
        " status, phone, played_2026, referrer, invite_token, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (name, email, token, 1 if is_test else 0,
         1 if status == "active" else 0, status, phone, played_2026,
         referrer, invite_token, utcnow_iso()),
    )
    con.commit()
    return {"id": cur.lastrowid, "name": name, "pick_token": token,
            "is_test": bool(is_test), "status": status}


def member_by_token(con, token: str):
    """Only ACTIVE members can sign in — pending/declined get nothing."""
    row = con.execute(
        "SELECT * FROM members WHERE pick_token = ? AND is_active = 1"
        " AND status = 'active'",
        (token,),
    ).fetchone()
    return dict(row) if row else None


def member_by_email(con, email: str):
    """Case-insensitive lookup; None when no member has that email."""
    email = (email or "").strip()
    if not email:
        return None
    row = con.execute(
        "SELECT * FROM members WHERE email IS NOT NULL"
        " AND LOWER(email) = LOWER(?)",
        (email,),
    ).fetchone()
    return dict(row) if row else None


def set_member_status(con, member_id: int, status: str) -> None:
    """Flip a member's approval status; is_active tracks active only."""
    if status not in MEMBER_STATUSES:
        raise ValueError(f"unknown member status: {status!r}")
    con.execute(
        "UPDATE members SET status = ?, is_active = ? WHERE id = ?",
        (status, 1 if status == "active" else 0, member_id),
    )
    con.commit()


def members_by_status(con, status: str) -> list:
    rows = con.execute(
        "SELECT * FROM members WHERE status = ? ORDER BY created_at",
        (status,),
    ).fetchall()
    return [dict(r) for r in rows]


def pending_members(con) -> list:
    return members_by_status(con, "pending")


def member_by_id(con, member_id: int):
    row = con.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()
    return dict(row) if row else None


def mark_welcome_seen(con, member_id: int) -> None:
    """Record that the member dismissed the first-visit welcome pop-up."""
    con.execute("UPDATE members SET seen_welcome = 1 WHERE id = ?",
                (member_id,))
    con.commit()


# ---------------------------------------------------------------- weeks / games

def current_week(con, season: int):
    """Latest week with games, or None."""
    row = con.execute(
        "SELECT season, week, friday_date, label, playground FROM weeks"
        " WHERE season = ? ORDER BY week DESC LIMIT 1", (season,),
    ).fetchone()
    return dict(row) if row else None


def current_season(con) -> int:
    row = con.execute("SELECT MAX(season) AS s FROM weeks").fetchone()
    return row["s"] if row and row["s"] else 2026


def ensure_week(con, season: int, week: int, friday_date: str,
                label: str | None = None, playground: bool = False) -> None:
    con.execute(
        "INSERT OR IGNORE INTO weeks (season, week, friday_date, label, playground)"
        " VALUES (?, ?, ?, ?, ?)",
        (season, week, friday_date, label, 1 if playground else 0),
    )
    con.commit()


def upsert_game(con, game: dict) -> None:
    con.execute(
        "INSERT INTO games (game_pk, season, week, friday_date, game_date_utc,"
        " status, home_team, away_team, home_starter_mlb_id, away_starter_mlb_id)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(game_pk) DO UPDATE SET status = excluded.status,"
        " home_starter_mlb_id = COALESCE(excluded.home_starter_mlb_id, games.home_starter_mlb_id),"
        " away_starter_mlb_id = COALESCE(excluded.away_starter_mlb_id, games.away_starter_mlb_id)",
        (game["game_pk"], game["season"], game["week"], game["friday_date"],
         game["game_date_utc"], game.get("status", "Scheduled"),
         game["home_team"], game["away_team"],
         game.get("home_starter_mlb_id"), game.get("away_starter_mlb_id")),
    )
    con.commit()


def games_for_week(con, season: int, week: int) -> list:
    rows = con.execute(
        "SELECT * FROM games WHERE season = ? AND week = ?"
        " ORDER BY game_date_utc, game_pk", (season, week),
    ).fetchall()
    return [dict(r) for r in rows]


def game_by_pk(con, game_pk: int):
    row = con.execute("SELECT * FROM games WHERE game_pk = ?", (game_pk,)).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------- players / lineups / stats

def upsert_player(con, player: dict) -> None:
    con.execute(
        "INSERT INTO players (mlb_id, name, team, position, is_pitcher)"
        " VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT(mlb_id) DO UPDATE SET name = excluded.name,"
        " team = excluded.team, position = excluded.position,"
        " is_pitcher = excluded.is_pitcher",
        (player["mlb_id"], player["name"], player["team"],
         player.get("position"), 1 if player.get("is_pitcher") else 0),
    )


def add_roster(con, game_pk: int, mlb_id: int) -> None:
    con.execute(
        "INSERT OR IGNORE INTO rosters (game_pk, mlb_id) VALUES (?, ?)",
        (game_pk, mlb_id),
    )


def set_lineup(con, game_pk: int, role: str, mlb_id: int,
               batting_order: int | None) -> None:
    con.execute(
        "INSERT OR REPLACE INTO lineups (game_pk, mlb_id, role, batting_order)"
        " VALUES (?, ?, ?, ?)", (game_pk, mlb_id, role, batting_order),
    )


def lineup_for_game(con, game_pk: int) -> dict:
    """{batting_order: [mlb_id...], starters: [mlb_id...]}.

    batting_order lists every batter-role player (announced lineups
    from the schedule carry no order number, so they sort last at 99
    but still count as in the lineup); starters are the pitchers.
    """
    rows = con.execute(
        "SELECT mlb_id, role, batting_order FROM lineups WHERE game_pk = ?",
        (game_pk,),
    ).fetchall()
    order_of = {}
    for r in rows:
        if r["role"] == "batter":
            order_of[r["mlb_id"]] = (
                r["batting_order"] if r["batting_order"] is not None else 99)
    batting = sorted(order_of, key=lambda mid: (order_of[mid], mid))
    starters = [r["mlb_id"] for r in rows if r["role"] == "pitcher"]
    return {"batting_order": batting, "starters": starters}


def upsert_stats(con, game_pk: int, stat: dict) -> None:
    con.execute(
        "INSERT INTO player_game_stats (game_pk, mlb_id, at_bats, hits, hr,"
        " rbi, pitches, strikeouts) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(game_pk, mlb_id) DO UPDATE SET at_bats = excluded.at_bats,"
        " hits = excluded.hits, hr = excluded.hr, rbi = excluded.rbi,"
        " pitches = excluded.pitches, strikeouts = excluded.strikeouts",
        (game_pk, stat["mlb_id"], stat.get("at_bats", 0), stat.get("hits", 0),
         stat.get("hr", 0), stat.get("rbi", 0), stat.get("pitches", 0),
         stat.get("strikeouts", 0)),
    )


def stats_for_week(con, season: int, week: int) -> dict:
    """{(game_pk, mlb_id): stat dict} for all games in the week."""
    rows = con.execute(
        "SELECT s.* FROM player_game_stats s JOIN games g ON g.game_pk = s.game_pk"
        " WHERE g.season = ? AND g.week = ?", (season, week),
    ).fetchall()
    return {(r["game_pk"], r["mlb_id"]): dict(r) for r in rows}


def players_for_game(con, game_pk: int) -> list:
    """All players on the game's roster, with team/pos."""
    rows = con.execute(
        "SELECT p.* FROM players p JOIN rosters r"
        " ON r.mlb_id = p.mlb_id AND r.game_pk = ?"
        " ORDER BY p.is_pitcher, p.name",
        (game_pk,),
    ).fetchall()
    return [dict(r) for r in rows]


def player_by_id(con, mlb_id: int):
    row = con.execute("SELECT * FROM players WHERE mlb_id = ?", (mlb_id,)).fetchone()
    return dict(row) if row else None


def game_starter_names(con, game_pk: int) -> dict:
    """{home: {mlb_id, name} | None, away: ...} for a game."""
    game = game_by_pk(con, game_pk)
    out = {}
    for side, col in (("home", "home_starter_mlb_id"),
                      ("away", "away_starter_mlb_id")):
        mlb_id = game[col] if game else None
        name = None
        if mlb_id:
            p = player_by_id(con, mlb_id)
            name = p["name"] if p else None
        out[side] = {"mlb_id": mlb_id, "name": name}
    return out


# ---------------------------------------------------------------- picks

BATTER_SLOTS = ["b1", "b2", "b3", "b4", "b5"]
PITCHER_SLOT = "p1"


def picks_for_member_week(con, member_id: int, season: int, week: int) -> list:
    rows = con.execute(
        "SELECT * FROM picks WHERE member_id = ? AND season = ? AND week = ?"
        " ORDER BY slot", (member_id, season, week),
    ).fetchall()
    return [dict(r) for r in rows]


def save_pick(con, member_id: int, season: int, week: int, slot: str,
              kind: str, mlb_id: int, game_pk: int) -> None:
    now = utcnow_iso()
    con.execute(
        "INSERT INTO picks (member_id, season, week, slot, kind, mlb_id,"
        " game_pk, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(member_id, season, week, slot) DO UPDATE SET"
        " kind = excluded.kind, mlb_id = excluded.mlb_id,"
        " game_pk = excluded.game_pk, updated_at = excluded.updated_at",
        (member_id, season, week, slot, kind, mlb_id, game_pk, now, now),
    )
    con.commit()


def delete_pick(con, member_id: int, season: int, week: int, slot: str) -> None:
    con.execute(
        "DELETE FROM picks WHERE member_id = ? AND season = ? AND week = ?"
        " AND slot = ?", (member_id, season, week, slot),
    )
    con.commit()


def standings(con, season: int, week: int) -> list:
    """[{member, points}] for one week, test members flagged, best first."""
    members = con.execute(
        "SELECT id, name, is_test FROM members WHERE is_active = 1"
        " ORDER BY name").fetchall()
    stats = stats_for_week(con, season, week)
    out = []
    for m in members:
        picks = picks_for_member_week(con, m["id"], season, week)
        from .scoring import week_points
        total = week_points(
            [{"kind": p["kind"], "mlb_id": p["mlb_id"], "game_pk": p["game_pk"]}
             for p in picks],
            stats,
        )
        out.append({"member_id": m["id"], "name": m["name"],
                    "is_test": bool(m["is_test"]), "points": total,
                    "picks_made": len(picks)})
    out.sort(key=lambda r: (-r["points"], r["name"]))
    return out
