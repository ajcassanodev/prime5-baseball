"""scripts/load_seed_form_weather.py: fills the form + weather gap
on a DB seeded before those keys existed in the seed file (the
server's state, Nov 27, 2026 — seeded Nov 24, backfill landed Nov 25)."""
import json
import os
import subprocess
import sys

from app import db as dbmod
from conftest import make_member

ROOT = os.path.join(os.path.dirname(__file__), "..")
SEED_PATH = os.path.join(ROOT, "data", "seed_friday_2026-09-25.json")
SCRIPT = os.path.join(ROOT, "scripts", "load_seed_form_weather.py")

# Every table the loader must never touch.
OTHER_TABLES = ["members", "games", "players", "player_game_stats",
                "lineups", "rosters", "weeks", "picks",
                "head_to_head_grants", "auto_pick_declines", "settings"]


def _seed_without_form_weather(con):
    """The real seed file, minus its form + weather keys — the
    server's DB as seeded Nov 24, before the Nov 25 backfill."""
    with open(SEED_PATH) as f:
        seed = json.load(f)
    dbmod.ensure_week(con, seed["season"], seed["week"],
                      seed["friday_date"], label=seed.get("label"),
                      playground=seed.get("playground", False))
    for game in seed["games"]:
        dbmod.upsert_game(con, {**game, "season": seed["season"],
                                 "week": seed["week"],
                                 "friday_date": seed["friday_date"]})
    for player in seed["players"]:
        dbmod.upsert_player(con, player)
    for row in seed.get("rosters", []):
        dbmod.add_roster(con, row["game_pk"], row["mlb_id"])
    for row in seed["lineups"]:
        dbmod.set_lineup(con, row["game_pk"], row["role"], row["mlb_id"],
                         row.get("batting_order"))
    for stat in seed["stats"]:
        dbmod.upsert_stats(con, stat["game_pk"], stat)
    con.commit()
    return seed


def _run_loader():
    """Run the script exactly as Adam will on the server."""
    r = subprocess.run([sys.executable, SCRIPT, "--db",
                        os.environ["PRIME5BB_DB"]],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def _counts(con, tables):
    # Commit first: a pending read transaction would block the
    # loader subprocess's writes to the same file.
    con.commit()
    return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in tables}


def _dump_form_weather(con):
    """Full contents of both tables, minus computed_at (the loader
    stamps it fresh on every upsert, so it always changes)."""
    con.commit()
    form = [dict(r) for r in con.execute(
        "SELECT * FROM player_form ORDER BY season, week, mlb_id")]
    weather = [dict(r) for r in con.execute(
        "SELECT * FROM game_weather ORDER BY game_pk")]
    for row in form + weather:
        row.pop("computed_at", None)
    return form, weather


def test_loader_fills_form_and_weather_only(con):
    _seed_without_form_weather(con)
    make_member(con, "Ada")
    before = _counts(con, OTHER_TABLES)
    assert _counts(con, ["player_form", "game_weather"]) == {
        "player_form": 0, "game_weather": 0}

    out = _run_loader()
    assert "453 player_form rows, 17 game_weather rows" in out
    assert _counts(con, ["player_form", "game_weather"]) == {
        "player_form": 453, "game_weather": 17}
    # Nothing else moved — no Demo Tester created, no picks, no
    # standings/grants touched.
    assert _counts(con, OTHER_TABLES) == before


def test_loader_is_idempotent(con):
    _seed_without_form_weather(con)
    _run_loader()
    first = _dump_form_weather(con)

    out = _run_loader()
    assert "453 player_form rows, 17 game_weather rows" in out
    assert _counts(con, ["player_form", "game_weather"]) == {
        "player_form": 453, "game_weather": 17}
    assert _dump_form_weather(con) == first


def test_pick_page_renders_after_loader(con, client):
    _seed_without_form_weather(con)
    m = make_member(con, "Ada")
    client.get(f"/submit/{m['pick_token']}")
    assert "Form guide" not in client.get("/picks").text

    _run_loader()
    con.commit()
    page = client.get("/picks")
    assert page.status_code == 200
    assert "Form guide" in page.text
    # Real backfilled values (same strings as the full-seed test).
    assert "12 hits last 7 days · 6-game hit streak" in page.text
    assert "Last start: 93 pitches · 11 K" in page.text
    assert "Fenway Park · 66°F · Overcast · Wind 17 mph from NNE" in page.text
    assert "Yankee Stadium · 70°F · Overcast · Wind 15 mph from NNE" in page.text
