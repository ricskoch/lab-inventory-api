import sqlite3

import pytest

from .conftest import headers

# (method, path, body) of every endpoint, and which roles must be allowed
SAMPLE = {"barcode": "S-9", "name": "x"}


def test_no_key_is_401(app_env):
    client, _ = app_env
    assert client.get("/samples").status_code == 401
    assert client.post("/samples", json=SAMPLE).status_code == 401


def test_wrong_key_is_401(app_env):
    client, _ = app_env
    assert client.get("/samples", headers=headers("lab_nope")).status_code == 401


def test_health_is_public(app_env):
    client, _ = app_env
    assert client.get("/health").json() == {"status": "ok"}


def test_reader_can_read_not_write(app_env):
    client, keys = app_env
    client.post("/samples", json=SAMPLE, headers=headers(keys["admin"]))
    assert client.get("/samples", headers=headers(keys["reader"])).status_code == 200
    assert client.post("/samples", json={**SAMPLE, "barcode": "S-10"}, headers=headers(keys["reader"])).status_code == 403
    assert client.delete("/samples/S-9", headers=headers(keys["reader"])).status_code == 403


def test_device_can_post_runs_only(app_env):
    client, keys = app_env
    admin, device = headers(keys["admin"]), headers(keys["device"])
    client.post("/samples", json=SAMPLE, headers=admin)
    dev = client.post("/devices", json={"name": "b", "kind": "balance"}, headers=admin).json()
    run = {"sample_barcode": "S-9", "device_id": dev["id"], "result": {"g": 1}}
    assert client.post("/runs", json=run, headers=device).status_code == 201
    # a compromised device key can't read inventory, change samples, register devices or see the audit log
    assert client.get("/samples", headers=device).status_code == 403
    assert client.post("/samples", json={**SAMPLE, "barcode": "S-10"}, headers=device).status_code == 403
    assert client.post("/devices", json={"name": "evil", "kind": "x"}, headers=device).status_code == 403
    assert client.get("/audit", headers=device).status_code == 403


def test_only_admin_reads_audit(app_env):
    client, keys = app_env
    assert client.get("/audit", headers=headers(keys["reader"])).status_code == 403
    assert client.get("/audit", headers=headers(keys["admin"])).status_code == 200


def test_revoked_key_stops_working(app_env, tmp_path):
    client, keys = app_env
    assert client.get("/samples", headers=headers(keys["reader"])).status_code == 200
    conn = sqlite3.connect(tmp_path / "test.db")
    conn.execute("UPDATE api_keys SET revoked = 1 WHERE name = 'reader-1'")
    conn.commit()
    assert client.get("/samples", headers=headers(keys["reader"])).status_code == 401


def test_plaintext_keys_are_not_stored(app_env, tmp_path):
    _, keys = app_env
    conn = sqlite3.connect(tmp_path / "test.db")
    stored = [r[0] for r in conn.execute("SELECT key_hash FROM api_keys")]
    for k in keys.values():
        assert k not in stored and all(k not in h for h in stored)


def test_audit_records_writes_and_rejections_with_actor(app_env):
    client, keys = app_env
    client.post("/samples", json=SAMPLE, headers=headers(keys["admin"]))
    client.post("/samples", json={**SAMPLE, "barcode": "S-10"}, headers=headers(keys["reader"]))  # 403
    client.get("/samples", headers=headers("lab_bad"))  # 401
    client.get("/samples", headers=headers(keys["admin"]))  # plain reads are not logged
    log = client.get("/audit", headers=headers(keys["admin"])).json()
    seen = {(e["actor"], e["method"], e["path"], e["status"]) for e in log}
    assert ("admin-1", "POST", "/samples", 201) in seen
    assert ("reader-1", "POST", "/samples", 403) in seen
    assert (None, "GET", "/samples", 401) in seen
    assert not any(e["method"] == "GET" and e["status"] == 200 for e in log)


def test_audit_log_cannot_be_edited_or_deleted(app_env, tmp_path):
    client, keys = app_env
    client.post("/samples", json=SAMPLE, headers=headers(keys["admin"]))
    conn = sqlite3.connect(tmp_path / "test.db")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM audit_log")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE audit_log SET actor = 'someone else'")
