"""Add a member and print their pick link.

Usage (from the project root):
    venv/bin/python scripts/add_member.py "Ada Lovelace" --email ada@example.com [--test]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import db as dbmod

BASE_URL = os.environ.get("PRIME5BB_BASE_URL", "http://127.0.0.1:8000")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--email", default=None)
    ap.add_argument("--test", action="store_true", help="flag as a test member")
    args = ap.parse_args()
    dbmod.init_db()
    con = dbmod.connect()
    member = dbmod.create_member(con, args.name, email=args.email,
                                 is_test=args.test)
    con.close()
    print(f"{member['name']} (id {member['id']},"
          f" {'test' if member['is_test'] else 'real'} member)")
    print(f"pick link: {BASE_URL}/submit/{member['pick_token']}")


if __name__ == "__main__":
    main()
