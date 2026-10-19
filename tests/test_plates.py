from .conftest import headers

LAYOUT = [
    {"well": "A01", "role": "drug", "drug": "drugA", "conc_um": 10},
    {"well": "A02", "role": "drug", "drug": "drugA", "conc_um": 3.33},
    {"well": "B01", "role": "vehicle"},
    {"well": "B02", "role": "kill"},
    {"well": "P24", "role": "blank"},
]


def _setup(client, keys):
    admin = headers(keys["admin"])
    client.post("/plates", json={"barcode": "P001", "experiment": "screen-1", "wells": LAYOUT}, headers=admin)
    dev = client.post("/devices", json={"name": "microscope-1", "kind": "microscope"}, headers=admin).json()
    return dev["id"]


def _batch(dev, batch_id="img-d1", day=1, wells=("A01", "A02", "B01")):
    return {"batch_id": batch_id, "device_id": dev, "day": day, "metric": "confluence_pct",
            "readings": [{"well": w, "value": 10.0 + i} for i, w in enumerate(wells)]}


def test_create_and_read_plate(app_env):
    client, keys = app_env
    _setup(client, keys)
    r = client.get("/plates/P001", headers=headers(keys["reader"])).json()
    assert r["n_wells"] == 5 and r["wells"][0] == {"well": "A01", "role": "drug", "drug": "drugA", "conc_um": 10.0}
    assert [p["barcode"] for p in client.get("/plates", headers=headers(keys["reader"])).json()] == ["P001"]


def test_plate_validation(app_env):
    client, keys = app_env
    admin = headers(keys["admin"])
    def post(wells, barcode="P9"):
        return client.post("/plates", json={"barcode": barcode, "experiment": "x", "wells": wells}, headers=admin)
    assert post([{"well": "Z99", "role": "blank"}]).status_code == 422          # not a 384-well name
    assert post([{"well": "A01", "role": "drug"}]).status_code == 422           # drug needs name and dose
    assert post([{"well": "A01", "role": "vehicle", "drug": "x"}]).status_code == 422
    assert post([{"well": "A01", "role": "blank"}, {"well": "A01", "role": "blank"}]).status_code == 422
    assert post([{"well": "A01", "role": "blank"}]).status_code == 201
    assert post([{"well": "A01", "role": "blank"}]).status_code == 409           # duplicate barcode


def test_bulk_readings_store_and_resend_is_harmless(app_env):
    client, keys = app_env
    dev = _setup(client, keys)
    d = headers(keys["device"])
    first = client.post("/plates/P001/readings", json=_batch(dev), headers=d)
    assert first.status_code == 201 and first.json() == {"stored": 3, "already_had": 0}
    again = client.post("/plates/P001/readings", json=_batch(dev), headers=d)
    assert again.json() == {"stored": 0, "already_had": 3}
    rows = client.get("/plates/P001/readings", headers=headers(keys["reader"])).json()
    assert len(rows) == 3 and rows[0]["drug"] == "drugA" and rows[0]["device_name"] == "microscope-1"


def test_partial_resend_only_adds_new_wells(app_env):
    client, keys = app_env
    dev = _setup(client, keys)
    d = headers(keys["device"])
    client.post("/plates/P001/readings", json=_batch(dev, wells=("A01", "A02")), headers=d)
    r = client.post("/plates/P001/readings", json=_batch(dev, wells=("A01", "A02", "B01")), headers=d)
    assert r.json() == {"stored": 1, "already_had": 2}


def test_readings_reject_bad_input_and_store_nothing(app_env):
    client, keys = app_env
    dev = _setup(client, keys)
    d = headers(keys["device"])
    undefined = client.post("/plates/P001/readings", json=_batch(dev, wells=("A01", "C05")), headers=d)
    assert undefined.status_code == 404 and "C05" in undefined.text
    assert client.post("/plates/NOPE/readings", json=_batch(dev), headers=d).status_code == 404
    assert client.post("/plates/P001/readings", json=_batch(999), headers=d).status_code == 404
    assert client.post("/plates/P001/readings", json=_batch(dev, wells=("A01", "A01")), headers=d).status_code == 422
    assert client.get("/plates/P001/readings", headers=headers(keys["reader"])).json() == []


def test_reading_filters_and_measured_at(app_env):
    client, keys = app_env
    dev = _setup(client, keys)
    d, r = headers(keys["device"]), headers(keys["reader"])
    client.post("/plates/P001/readings", json={**_batch(dev, "d1", 1), "measured_at": "2026-10-20T09:00:00Z"}, headers=d)
    client.post("/plates/P001/readings", json=_batch(dev, "d2", 2), headers=d)
    day2 = client.get("/plates/P001/readings", params={"day": 2}, headers=r).json()
    assert {x["day"] for x in day2} == {2}
    assert client.get("/plates/P001/readings", params={"drug": "drugA"}, headers=r).json()[0]["drug"] == "drugA"
    day1 = client.get("/plates/P001/readings", params={"day": 1}, headers=r).json()
    assert day1[0]["measured_at"] == "2026-10-20T09:00:00Z"
    bad = client.post("/plates/P001/readings", json={**_batch(dev, "d3", 3), "measured_at": "soon"}, headers=d)
    assert bad.status_code == 422


def test_roles_on_plate_endpoints(app_env):
    client, keys = app_env
    dev = _setup(client, keys)
    d, r = headers(keys["device"]), headers(keys["reader"])
    plate = {"barcode": "P2", "experiment": "x", "wells": [{"well": "A01", "role": "blank"}]}
    assert client.post("/plates", json=plate, headers=d).status_code == 403
    assert client.post("/plates", json=plate, headers=r).status_code == 403
    assert client.post("/plates/P001/readings", json=_batch(dev), headers=r).status_code == 403
    assert client.get("/plates", headers=d).status_code == 403
    assert client.get("/plates/P001/readings", headers=d).status_code == 403
    assert client.get("/plates").status_code == 401


def test_events_roundtrip_and_idempotency(app_env):
    client, keys = app_env
    dev = _setup(client, keys)
    d, r = headers(keys["device"]), headers(keys["reader"])
    ev = {"kind": "move", "plate_barcode": "P001", "device_id": dev,
          "detail": {"from": "incubator", "to": "imager"}, "ts": "2026-10-20T08:00:00Z", "external_id": "arm:1"}
    a = client.post("/events", json=ev, headers=d)
    b = client.post("/events", json=ev, headers=d)
    assert a.status_code == 201 and a.json()["id"] == b.json()["id"]
    got = client.get("/events", params={"plate": "P001", "kind": "move"}, headers=r).json()
    assert len(got) == 1 and got[0]["detail"] == {"from": "incubator", "to": "imager"} and got[0]["device_name"] == "microscope-1"
    assert client.post("/events", json={"kind": "x", "plate_barcode": "NOPE"}, headers=d).status_code == 404
    assert client.post("/events", json=ev, headers=r).status_code == 403


def test_full_384_well_batch(app_env):
    client, keys = app_env
    admin, d = headers(keys["admin"]), headers(keys["device"])
    rows, cols = "ABCDEFGHIJKLMNOP", range(1, 25)
    wells = [{"well": f"{r}{c:02d}", "role": "blank"} for r in rows for c in cols]
    assert len(wells) == 384
    client.post("/plates", json={"barcode": "FULL", "experiment": "x", "wells": wells}, headers=admin)
    dev = client.post("/devices", json={"name": "pr", "kind": "plate_reader"}, headers=admin).json()["id"]
    body = {"batch_id": "b", "device_id": dev, "day": 4, "metric": "luminescence",
            "readings": [{"well": w["well"], "value": 1.0} for w in wells]}
    assert client.post("/plates/FULL/readings", json=body, headers=d).json() == {"stored": 384, "already_had": 0}
