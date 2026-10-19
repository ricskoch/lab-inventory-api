"""Smoke test: the Streamlit app renders every tab against a real (in-process) API with a simulated plate."""
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

from dashboard.api import LabApi
from screening.biology import PlateState, layout_payload

from .conftest import headers

ROOT = Path(__file__).resolve().parent.parent
T0 = datetime(2026, 10, 19, 8, tzinfo=timezone.utc)


def _load_plate(client, keys):
    admin = headers(keys["admin"])
    assert client.post("/plates", json={"barcode": "S1", "experiment": "x", "wells": layout_payload(1)},
                       headers=admin).status_code == 201
    mic = client.post("/devices", json={"name": "mic", "kind": "microscope"}, headers=admin).json()["id"]
    pr = client.post("/devices", json={"name": "pr", "kind": "plate_reader"}, headers=admin).json()["id"]
    p = PlateState("S1", 1, seed=3)
    p.seed(T0)
    p.add_drug(T0 + timedelta(hours=24))
    for day in range(1, 5):
        t = T0 + timedelta(hours=24 * day)
        client.post("/plates/S1/readings", headers=admin, json={
            "batch_id": f"c{day}", "device_id": mic, "day": day, "metric": "confluence_pct", "measured_at": t.isoformat(),
            "readings": [{"well": w, "value": v} for w, v in p.confluence(t).items()]})
    t = T0 + timedelta(hours=96)
    client.post("/plates/S1/readings", headers=admin, json={
        "batch_id": "lum", "device_id": pr, "day": 4, "metric": "luminescence", "measured_at": t.isoformat(),
        "readings": [{"well": w, "value": v} for w, v in p.luminescence(t).items()]})
    client.post("/events", headers=admin, json={"kind": "seed", "plate_barcode": "S1", "device_id": mic, "detail": {}})


def test_every_tab_renders(app_env, monkeypatch):
    client, keys = app_env
    _load_plate(client, keys)
    client.headers.update(headers(keys["reader"]))
    monkeypatch.setattr("dashboard.api.connect", lambda: LabApi(client))
    at = AppTest.from_file(str(ROOT / "dashboard" / "app.py"), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    assert not at.error
    assert len(at.tabs) == 5
