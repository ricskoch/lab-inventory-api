"""Run a whole simulated screening week with one command.

  export LAB_API_URL=http://127.0.0.1:8000   LAB_ADMIN_KEY=lab_...   LAB_DEVICE_KEY=lab_...
  python -m screening.run_week --plates 4 --day-seconds 30

Needs the API running, one admin key (creates devices and plates) and one device key (used by the
controller and the file adapters). It starts the lab simulator and both adapters, runs the controller
through Monday to Friday, waits for the uploads to finish, and prints a summary.
"""
import argparse
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from .biology import DOSES_UM, estimate_ic50, layout_payload, make_layout, truth
from .controller import DEVICE_NAME, LabLink, build_schedule, run
from .lab_sim import SimClock

KINDS = {"arm": "robot_arm", "dispenser": "dispenser", "microscope": "microscope", "reader": "plate_reader"}


def monday_8am_utc() -> datetime:
    now = datetime.now(timezone.utc)
    return (now - timedelta(days=now.weekday())).replace(hour=8, minute=0, second=0, microsecond=0)


def setup_api(api: httpx.Client, plates: list[str]):
    """Admin work: register the four devices and create the plates with their well maps (safe to repeat)."""
    have = {d["name"] for d in api.get("/devices").json()}
    for dev, name in DEVICE_NAME.items():
        if name not in have:
            api.post("/devices", json={"name": name, "kind": KINDS[dev], "location": "Screening lab"}).raise_for_status()
    for i, barcode in enumerate(plates, start=1):
        r = api.post("/plates", json={"barcode": barcode, "experiment": "weekly drug screen", "wells": layout_payload(i)})
        if r.status_code not in (201, 409):
            r.raise_for_status()


def wait_for_port(port: int, timeout: float = 15):
    end = time.time() + timeout
    while time.time() < end:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            return
        except OSError:
            time.sleep(0.1)
    raise SystemExit(f"lab simulator did not start on port {port}")


def summarize(api: httpx.Client, plates: list[str]):
    print("\n=== Summary ===")
    for i, barcode in enumerate(plates, start=1):
        rows = api.get(f"/plates/{barcode}/readings", params={"limit": 20000}).json()
        by_metric = {}
        for r in rows:
            by_metric[r["metric"]] = by_metric.get(r["metric"], 0) + 1
        print(f"{barcode}: {len(rows)} readings {by_metric}")
    # Does the end-point data recover the hidden potencies? (first plate)
    lum = [r for r in api.get(f"/plates/{plates[0]}/readings", params={"metric": "luminescence", "limit": 20000}).json()]
    veh = [r["value"] for r in lum if r["role"] == "vehicle"]
    kill = [r["value"] for r in lum if r["role"] == "kill"]
    drugs = sorted({r["drug"] for r in lum if r["drug"]})
    print(f"\nIC50 recovery on {plates[0]} (vehicle mean {sum(veh)/len(veh):.0f} RLU, kill mean {sum(kill)/len(kill):.0f} RLU)")
    print(f"{'drug':8} {'true uM':>10} {'estimated':>10}")
    good = 0
    for d in drugs:
        est = estimate_ic50([(r["conc_um"], r["value"]) for r in lum if r["drug"] == d], sum(veh) / len(veh), sum(kill) / len(kill))
        true = truth(d)[0]
        in_range = DOSES_UM[-1] <= true <= DOSES_UM[0]
        good += bool(est and 0.5 < est / true < 2)
        print(f"{d:8} {true:10.4g} {'no fit' if est is None else format(est, '10.4g')}{'' if in_range else '   (outside tested doses)'}")
    print(f"{good} of {len(drugs)} within 2x of the truth")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plates", type=int, default=4)
    ap.add_argument("--day-seconds", type=float, default=30.0, help="real seconds per simulated day")
    ap.add_argument("--work", default="screening_run")
    ap.add_argument("--base-port", type=int, default=9201)
    ap.add_argument("--jam-rate", type=float, default=0.0, help="chance a robot move jams and is retried")
    ap.add_argument("--sim-start", default=None, help="simulated Monday 08:00 UTC, default: this week's Monday")
    a = ap.parse_args()

    url = os.environ.get("LAB_API_URL", "http://127.0.0.1:8000")
    admin_key, device_key = os.environ["LAB_ADMIN_KEY"], os.environ["LAB_DEVICE_KEY"]
    verify = os.environ.get("LAB_CA_CERT") or True
    admin = httpx.Client(base_url=url, headers={"X-API-Key": admin_key}, verify=verify, timeout=10)
    dev = httpx.Client(base_url=url, headers={"X-API-Key": device_key}, verify=verify, timeout=10)

    run_id = time.strftime("%m%d%H%M%S")
    plates = [f"S{run_id}-P{i}" for i in range(1, a.plates + 1)]
    work = Path(a.work) / run_id
    sim_start = datetime.fromisoformat(a.sim_start.replace("Z", "+00:00")) if a.sim_start else monday_8am_utc()
    t0 = time.time() + 3  # everybody agrees on when the simulated week starts (after a short start-up)
    clock = SimClock(sim_start, t0, a.day_seconds)  # sim time is sim_start (Monday 08:00) at real time t0

    setup_api(admin, plates)
    env = {**os.environ, "LAB_API_URL": url, "LAB_API_KEY": device_key}
    procs = []
    try:
        procs.append(subprocess.Popen([sys.executable, "-m", "screening.lab_sim", "--plates", *plates, "--out", str(work),
                                       "--base-port", str(a.base_port), "--t0", str(t0), "--sim-start", sim_start.isoformat(),
                                       "--day-seconds", str(a.day_seconds), "--jam-rate", str(a.jam_rate)]))
        for folder, name in (("microscope", "microscope-1"), ("reader", "plate-reader-1")):
            procs.append(subprocess.Popen([sys.executable, "-m", "adapters.plate_files", str(work / folder), name], env=env))
        for p in range(a.base_port, a.base_port + 4):
            wait_for_port(p)
        link = LabLink("127.0.0.1", a.base_port)
        steps = build_schedule(plates)
        print(f"Run {run_id}: {len(plates)} plates, {len(steps)} steps, one simulated day = {a.day_seconds:g} s. Starting in 3 s.\n")
        stats = run(steps, link, clock, dev, run_id)
        link.close()
        expected = len(plates) * 5  # 4 imaging days + 1 reader file per plate
        end = time.time() + 30
        while time.time() < end:
            done = len(list((work / "microscope" / "processed").glob("*.csv"))) + len(list((work / "reader" / "processed").glob("*.csv")))
            if done >= expected:
                break
            time.sleep(0.3)
        print(f"\nController finished {stats['steps']} steps; adapters uploaded {done} of {expected} files.")
        summarize(admin, plates)
    finally:
        for p in procs:
            p.terminate()


if __name__ == "__main__":
    main()
