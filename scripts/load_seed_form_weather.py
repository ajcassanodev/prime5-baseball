"""Load only the pick-help form + weather rows from the playground
seed into an existing DB (idempotent).

Usage (from the project root):
    venv/bin/python scripts/load_seed_form_weather.py [--db path/to/baseball.db]

Why this exists: the server DB was seeded Nov 24, 2026, before the
seed file gained its "form" and "weather" keys (backfilled Nov 25 by
scripts/backfill_form_seed.py), so its player_form and game_weather
tables are empty and the pick page shows no form lines, Form guide,
or weather. A git pull brings the new code and the updated seed file
but does not re-seed the DB — run this once after pulling.

Only touches player_form and game_weather, via the same upsert
helpers scripts/seed.py uses (dbmod.upsert_player_form /
upsert_game_weather — INSERT ... ON CONFLICT DO UPDATE). Re-running
re-upserts the same rows and changes nothing. Members, picks,
standings, grants, and every other table are untouched, and no
member is created. The pick route reads these tables per request,
so no restart is needed afterwards.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import db as dbmod

SEED = os.path.join(os.path.dirname(__file__), "..", "data",
                    "seed_friday_2026-09-25.json")


def load_form_weather(con, seed: dict) -> tuple[int, int]:
    """Upsert the seed's form + weather rows; return (form, weather)
    counts written. Touches only player_form and game_weather."""
    form = seed.get("form", [])
    for row in form:
        dbmod.upsert_player_form(con, seed["season"], seed["week"], row)
    weather = seed.get("weather", [])
    for row in weather:
        dbmod.upsert_game_weather(con, row["game_pk"], row)
    con.commit()
    return len(form), len(weather)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    args = ap.parse_args()
    if args.db:
        os.environ["PRIME5BB_DB"] = args.db
    # db module reads the env var at import; re-point if overridden.
    import importlib
    importlib.reload(dbmod)
    # Creates player_form / game_weather if this DB predates them;
    # otherwise a no-op (same init the app runs at startup).
    dbmod.init_db()
    with open(SEED) as f:
        seed = json.load(f)
    con = dbmod.connect()
    try:
        form_n, weather_n = load_form_weather(con, seed)
    finally:
        con.close()
    print(f"loaded seed form + weather for {seed['label']}:"
          f" {form_n} player_form rows, {weather_n} game_weather rows")


if __name__ == "__main__":
    main()
