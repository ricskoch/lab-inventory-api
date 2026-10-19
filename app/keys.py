"""Key management CLI (run on the server, not exposed over HTTP).

  python -m app.keys create --name richard --role admin
  python -m app.keys create --name plate-reader-1 --role device
  python -m app.keys list
  python -m app.keys revoke --name plate-reader-1
"""
import argparse
import sqlite3

from .db import db_path, init_db
from .security import ROLES, create_key


def main():
    p = argparse.ArgumentParser(prog="app.keys")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--name", required=True)
    c.add_argument("--role", required=True, choices=ROLES)
    sub.add_parser("list")
    r = sub.add_parser("revoke")
    r.add_argument("--name", required=True)
    a = p.parse_args()

    init_db()
    conn = sqlite3.connect(db_path())
    if a.cmd == "create":
        key = create_key(conn, a.name, a.role)
        print(f"Key for {a.name} ({a.role}). Store it now, it cannot be shown again:\n{key}")
    elif a.cmd == "list":
        for n, role, rev, ts in conn.execute("SELECT name, role, revoked, created_at FROM api_keys"):
            print(f"{n:25} {role:7} {'REVOKED' if rev else 'active':8} {ts}")
    else:
        cur = conn.execute("UPDATE api_keys SET revoked = 1 WHERE name = ?", (a.name,))
        conn.commit()
        print("revoked" if cur.rowcount else "no such key")


if __name__ == "__main__":
    main()
