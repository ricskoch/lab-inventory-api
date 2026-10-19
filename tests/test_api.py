def _sample(client, barcode="S-001"):
    return client.post("/samples", json={"barcode": barcode, "name": "Lysate A"})


def test_create_and_get_sample(client):
    r = _sample(client)
    assert r.status_code == 201
    assert r.json()["status"] == "registered"
    assert client.get("/samples/S-001").json()["name"] == "Lysate A"


def test_duplicate_barcode_conflicts(client):
    _sample(client)
    assert _sample(client).status_code == 409


def test_invalid_barcode_rejected(client):
    r = client.post("/samples", json={"barcode": "bad barcode!", "name": "x"})
    assert r.status_code == 422


def test_patch_status_and_invalid_status(client):
    _sample(client)
    assert client.patch("/samples/S-001", json={"status": "in_use"}).json()["status"] == "in_use"
    assert client.patch("/samples/S-001", json={"status": "nonsense"}).status_code == 422
    assert client.patch("/samples/NOPE", json={"status": "in_use"}).status_code == 404


def test_run_links_sample_and_device(client):
    _sample(client)
    dev = client.post("/devices", json={"name": "reader-1", "kind": "plate_reader"}).json()
    r = client.post(
        "/runs", json={"sample_barcode": "S-001", "device_id": dev["id"], "result": {"od600": 0.42}}
    )
    assert r.status_code == 201
    runs = client.get("/samples/S-001/runs").json()
    assert runs[0]["result"] == {"od600": 0.42}


def test_run_for_unknown_sample_404(client):
    dev = client.post("/devices", json={"name": "b1", "kind": "balance"}).json()
    r = client.post("/runs", json={"sample_barcode": "X", "device_id": dev["id"], "result": {}})
    assert r.status_code == 404


def test_cannot_delete_sample_with_runs(client):
    _sample(client)
    dev = client.post("/devices", json={"name": "b1", "kind": "balance"}).json()
    client.post("/runs", json={"sample_barcode": "S-001", "device_id": dev["id"], "result": {"g": 1}})
    assert client.delete("/samples/S-001").status_code == 409


def test_sql_injection_attempt_is_inert(client):
    _sample(client)
    assert client.get("/samples/S-001'; DROP TABLE samples;--").status_code == 404
    assert client.get("/samples/S-001").status_code == 200
