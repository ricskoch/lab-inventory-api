"""Authentication (who are you?) and authorisation (what may you do?).

API keys are random 256-bit tokens. Because they are high-entropy (unlike human passwords), a plain
SHA-256 is enough to store them safely: a database leak does not reveal usable keys, and there is
nothing to brute-force. Human passwords would need bcrypt/argon2 instead.
"""
import hashlib
import secrets
import sqlite3

from fastapi import Depends, HTTPException, Request
from fastapi.security import APIKeyHeader

from .db import get_conn

ROLES = ("admin", "device", "reader")

# Declaring the scheme this way makes the /docs page show an "Authorize" button.
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def new_key() -> str:
    return "lab_" + secrets.token_urlsafe(32)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def create_key(conn: sqlite3.Connection, name: str, role: str) -> str:
    """Returns the plaintext key. This is the only time it exists outside the caller's hands."""
    key = new_key()
    conn.execute(
        "INSERT INTO api_keys (name, role, key_hash) VALUES (?, ?, ?)", (name, role, hash_key(key))
    )
    conn.commit()
    return key


def require(*allowed_roles: str):
    """Dependency factory: `Depends(require("admin"))` protects an endpoint."""

    def dep(
        request: Request,
        x_api_key: str | None = Depends(api_key_header),
        conn=Depends(get_conn),
    ):
        if not x_api_key:
            raise HTTPException(401, "Missing X-API-Key header", headers={"WWW-Authenticate": "ApiKey"})
        row = conn.execute(
            "SELECT name, role FROM api_keys WHERE key_hash = ? AND revoked = 0", (hash_key(x_api_key),)
        ).fetchone()
        if not row:
            raise HTTPException(401, "Invalid or revoked API key", headers={"WWW-Authenticate": "ApiKey"})
        # Stored on the request so the audit middleware can record who acted.
        request.state.actor, request.state.role = row["name"], row["role"]
        if row["role"] not in allowed_roles:
            raise HTTPException(403, f"Role '{row['role']}' may not do this")
        return row

    return dep
