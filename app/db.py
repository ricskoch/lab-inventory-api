import os
import sqlite3
from pathlib import Path

SCHEMA = Path(__file__).with_name("schema.sql")


def db_path() -> str:
    return os.environ.get("LAB_DB", "lab.db")


def get_conn():
    """FastAPI dependency: one connection per request, always closed."""
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    conn = sqlite3.connect(db_path())
    conn.executescript(SCHEMA.read_text())
    conn.close()
