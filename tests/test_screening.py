import socket
from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest

from adapters.client import ApiClient
from adapters.plate_files import parse_file, process_file
from screening.biology import DOSES_UM, PlateState, estimate_ic50, layout_payload, make_layout, truth
from screening.controller import LabError, LabLink, build_schedule
from screening.lab_sim import LabError as SimLabError, World, serve

from .conftest import headers

T0 = datetime(2026, 10, 19, 8, tzinfo=timezone.utc)


class FakeClock:
    """Simulated time we move by hand, so tests don't wait."""
    def __init__(self):
        self.t = T0

    def now(self):
        return self.t


def test_layout_is_a_full_384_well_plate():
    wells = make_layout(1)
    assert len(wells) == 384 and len({w.well for w in wells}) == 384
    assert Counter(w.role for w in wells) == {"drug": 320, "vehicle": 16, "kill": 16, "blank": 32}
    drug_a = [w for w in wells if w.drug == "CMP-1A"]
    assert len(drug_a) == 20 and sorted({w.conc_um for w in drug_a}) == sorted(round(d, 6) for d in DOSES_UM)
    assert {w.well[0] for w in drug_a} == {"A"}  # one drug per row


def test_layout_is_accepted_by_the_api(app_env):
    client, keys = app_env
    r = client.post("/plates", json={"barcode": "T1", "experiment": "x", "wells": layout_payload(1)},
                    headers=headers(keys["admin"]))
    assert r.status_code == 201 and r.json()["n_wells"] == 384


def test_truth_is_deterministic_and_potencies_are_recovered():
    assert truth("CMP-1A") == truth("CMP-1A") and truth("CMP-1A") != truth("CMP-1B")
    wells = make_layout(1)
    p = PlateState("X", 1, noise=False)
    p.seed(T0); p.add_drug(T0 + timedelta(hours=24))
    lum = p.luminescence(T0 + timedelta(hours=96))
    close = 0
    for row in "ABCDEFGHIJKLMNOP":
        drug = f"CMP-1{row}"
        est = estimate_ic50([(w.conc_um, lum[w.well]) for w in wells if w.drug == drug], lum[f"{row}22"], lum[f"{row}23"])
        ic50 = truth(drug)[0]
        if est is None:
            assert ic50 > DOSES_UM[0]  # only drugs inactive in the tested range may fail to fit
        else:
            close += abs(est / ic50 - 1) < 0.05
    assert close >= 13


def test_biology_behaves():
    p = PlateState("X", 1, noise=False)
    assert p.cells("C22", T0) == 0  # nothing before seeding
    p.seed(T0); p.add_drug(T0 + timedelta(hours=24))
    t = T0 + timedelta(hours=96)
    assert p.cells("A01", t) == 0                       # blank wells never have cells
    assert p.cells("C22", t) > 5 * p.cells("C22", T0 + timedelta(hours=1))  # vehicle grows
    assert p.cells("C23", t) < 0.15 * p.cells("C22", t)  # kill control mostly dies


def test_world_enforces_physics(tmp_path):
    w = World(["P1", "P2"], FakeClock(), tmp_path)
    with pytest.raises(SimLabError, match="is at STOCK, not IMAGER"):
        w.microscope(["IMAGE", "P1", "1"])
    with pytest.raises(SimLabError, match="is at STOCK, not DISPENSER"):
        w.dispenser(["SEED", "P1"])
    w.arm(["MOVE", "P1", "STOCK", "DISPENSER"])
    with pytest.raises(SimLabError, match="occupied"):
        w.arm(["MOVE", "P2", "STOCK", "DISPENSER"])
    w.dispenser(["SEED", "P1"])
    with pytest.raises(SimLabError, match="already seeded"):
        w.dispenser(["SEED", "P1"])
    w.arm(["MOVE", "P1", "DISPENSER", "STOCK"])
    w.arm(["MOVE", "P2", "STOCK", "DISPENSER"])
    with pytest.raises(SimLabError, match="no cells"):
        w.dispenser(["ADD_DRUG", "P2"])


def test_schedule_shape():
    plates = ["A", "B", "C", "D"]
    steps = build_schedule(plates)
    assert len(steps) == 76  # 3 + 5 + 3 + 3 + 5 steps per plate
    day1 = [s.kind for s in steps if s.plate == "A" and s.day == 1]
    assert day1 == ["move", "image", "move", "add_drug", "move"]  # baseline image before the drug goes on
    assert [s.day for s in steps if s.kind == "read"] == [4] * 4
    assert [s.at for s in steps] == sorted(s.at for s in steps)


def test_full_week_produces_files_and_uploads_every_well(app_env, tmp_path):
    client, keys = app_env
    admin, dev_key = headers(keys["admin"]), headers(keys["device"])
    plates = ["W-P1", "W-P2"]
    for i, bc in enumerate(plates, start=1):
        assert client.post("/plates", json={"barcode": bc, "experiment": "x", "wells": layout_payload(i)}, headers=admin).status_code == 201
    ids = {n: client.post("/devices", json={"name": n, "kind": k}, headers=admin).json()["id"]
           for n, k in (("microscope-1", "microscope"), ("plate-reader-1", "plate_reader"))}

    clock = FakeClock()
    world = World(plates, clock, tmp_path / "lab")
    for s in build_schedule(plates):          # every step must be physically possible, in order
        clock.t = T0 + timedelta(seconds=s.at)
        reply = getattr(world, {"arm": "arm", "dispenser": "dispenser", "microscope": "microscope", "reader": "reader"}[s.device])(s.command.split())
        assert reply.startswith("OK")
    assert set(world.where.values()) == {"WASTE"}

    client.headers.update(dev_key)
    api = ApiClient("", spool=tmp_path / "spool.jsonl", http=client)
    stored = 0
    for sub, dev in (("microscope", "microscope-1"), ("reader", "plate-reader-1")):
        files = sorted((tmp_path / "lab" / sub).glob("*.csv"))
        for f in files:
            stored += process_file(f, api, ids[dev], tmp_path / "lab" / sub / "processed")
    assert stored == 2 * 1920
    rows = client.get("/plates/W-P1/readings", params={"metric": "luminescence"}, headers=headers(keys["reader"])).json()
    assert len(rows) == 384 and rows[0]["measured_at"].startswith("2026-10-23T08")  # Friday, simulated time
    # re-reading the same file names would store nothing new
    assert parse_file(tmp_path / "lab" / "reader" / "processed" / "W-P1__day4__luminescence.csv")[1]["batch_id"] == "W-P1__day4__luminescence.csv"


def test_adapter_rejects_unexpected_file_names(tmp_path):
    bad = tmp_path / "notes.csv"
    bad.write_text("well,value,measured_at\n")
    with pytest.raises(ValueError):
        parse_file(bad)


def _free_port_block(n=4):
    for base in range(19300, 19900, 10):
        socks = []
        try:
            for i in range(n):
                s = socket.socket(); s.bind(("127.0.0.1", base + i)); socks.append(s)
            return base
        except OSError:
            continue
        finally:
            for s in socks:
                s.close()
    raise RuntimeError("no free ports")


def test_controller_retries_a_jammed_arm_but_not_a_real_error(tmp_path):
    base = _free_port_block()
    world = World(["P1"], FakeClock(), tmp_path, jam_rate=0.5, seed=3)
    servers = serve(world, base)
    link = LabLink("127.0.0.1", base)
    try:
        assert link.send("arm", "MOVE P1 STOCK DISPENSER", retries=8).startswith("OK moved")
        with pytest.raises(LabError, match="is at DISPENSER, not IMAGER"):
            link.send("microscope", "IMAGE P1 1")   # a real error is raised straight away
    finally:
        link.close()
        for s in servers:
            s.shutdown(); s.server_close()
