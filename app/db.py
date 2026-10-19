import os
import sqlite3
from pathlib import Path

SCHEMA = Path(__file__).with_name("schema.sql")


def db_path() -> str:
    return os.environ.get("LAB_DB", "lab.db")


def get_conn():
    """FastAPI dependency: one connection per request, always closed."""
    # isolation_level=None = autocommit: each statement commits immediately. Handlers here are
    # single-statement writes, and it means a request never holds a write lock while the audit
    # middleware (which runs after the handler) writes its own row. For multi-statement
    # transactions, wrap them in explicit BEGIN/COMMIT.
    conn = sqlite3.connect(db_path(), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    conn = sqlite3.connect(db_path())
    conn.executescript(SCHEMA.read_text())
    conn.close()
