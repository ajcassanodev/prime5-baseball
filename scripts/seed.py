"""Load the playground seed file into the DB (idempotent).

Usage (from the project root):
    venv/bin/python scripts/seed.py [--db path/to/baseball.db]

Creates the 2026 playground week (Fri Sep 25, 2026) and a demo test
member, then prints the demo pick link. Re-running re-upserts the data
and re-prints the existing demo member's link.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import db as dbmod

SEED = os.path.join(os.path.dirname(__file__), "..", "data",
                    "seed_friday_2026-09-25.json")
BASE_URL = os.environ.get("PRIME5BB_BASE_URL", "http://127.0.0.1:8000")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    args = ap.parse_args()
    if args.db:
        os.environ["PRIME5BB_DB"] = args.db
    # db module reads the env var at import; re-point if overridden.
    import importlib
    importlib.reload(dbmod)
    dbmod.init_db()
    with open(SEED) as f:
        seed = json.load(f)
    con = dbmod.connect()
    dbmod.ensure_week(con, seed["season"], seed["week"], seed["friday_date"],
                      label=seed.get("label"), playground=seed.get("playground", False))
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
    row = con.execute(
        "SELECT * FROM members WHERE name = 'Demo Tester' AND is_test = 1"
    ).fetchone()
    if row:
        member = dict(row)
    else:
        member = dbmod.create_member(con, "Demo Tester",
                                     email="demo@example.com", is_test=True)
    con.close()
    print(f"seeded {seed['label']}: {len(seed['games'])} games,"
          f" {len(seed['players'])} players")
    print(f"demo pick link: {BASE_URL}/submit/{member['pick_token']}")


if __name__ == "__main__":
    main()
