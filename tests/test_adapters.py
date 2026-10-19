import httpx
import pytest
from fastapi.testclient import TestClient

from adapters.balance import parse_line
from adapters.client import ApiClient
from adapters.plate_reader import parse_csv, process_file


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_DB", str(tmp_path / "t.db"))
    from app.main import app

    with TestClient(app) as http:
        client = ApiClient("", spool=tmp_path / "spool.jsonl", http=http)
        for b in ("S-001", "S-002"):
            client.ensure_sample(b)
        yield client, http


def test_parse_balance_line():
    r = parse_line("S-001,12.345,g\n", 1, "balance-1")
    assert r["sample_barcode"] == "S-001" and r["result"] == {"mass": 12.345, "unit": "g"}


def test_plate_reader_file_ingested_and_archived(api, tmp_path):
    client, http = api
    dev = client.ensure_device("pr", "plate_reader")
    f = tmp_path / "plate_001.csv"
    f.write_text("sample_barcode,od600\nS-001,0.5\nS-002,0.7\n")
    assert process_file(f, client, dev, tmp_path / "processed") == 2
    assert not f.exists() and (tmp_path / "processed" / "plate_001.csv").exists()
    assert http.get("/samples/S-001/runs").json()[0]["result"] == {"od600": 0.5}


def test_resending_same_external_id_creates_no_duplicate(api, tmp_path):
    client, http = api
    dev = client.ensure_device("pr", "plate_reader")
    payload = {"sample_barcode": "S-001", "device_id": dev, "result": {"od600": 1}, "external_id": "x:1"}
    assert http.post("/runs", json=payload).status_code == 201
    assert http.post("/runs", json=payload).status_code == 200
    assert len(http.get("/samples/S-001/runs").json()) == 1


def test_spool_when_api_down_then_flush(api, tmp_path):
    client, http = api
    dev = client.ensure_device("b", "balance")
    good = client.http
    payload = {"sample_barcode": "S-001", "device_id": dev, "result": {"mass": 1}, "external_id": "b:1"}

    class Down:
        def post(self, *a, **k):
            raise httpx.ConnectError("api down")

    client.http = Down()
    assert client.send_run(payload) is False
    client.http = good
    assert client.flush_spool() == 1
    assert len(http.get("/samples/S-001/runs").json()) == 1
    assert client.flush_spool() == 0
