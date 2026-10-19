import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.security import create_key


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    """Fresh DB + one key per role. Yields (client, keys) where client has no default key."""
    monkeypatch.setenv("LAB_DB", str(tmp_path / "test.db"))
    from app.main import app

    with TestClient(app) as client:
        conn = sqlite3.connect(tmp_path / "test.db")
        keys = {r: create_key(conn, f"{r}-1", r) for r in ("admin", "device", "reader")}
        conn.close()
        yield client, keys


def headers(key):
    return {"X-API-Key": key}


@pytest.fixture
def client(app_env):
    """Admin client: keeps the step 2 API tests unchanged."""
    c, keys = app_env
    c.headers.update(headers(keys["admin"]))
    return c
