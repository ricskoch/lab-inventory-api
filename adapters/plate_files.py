"""Adapter for plate instruments (microscope, plate reader) that drop one CSV per plate and day.

File name:   <plate>__day<N>__<metric>.csv      e.g.  S1-P1__day3__confluence_pct.csv
Columns:     well,value,measured_at
Pattern: watch folder -> parse -> one bulk upload of up to 384 wells -> move file to processed/.
The file name doubles as the batch_id, so an upload that is repeated stores nothing twice.

  python -m adapters.plate_files <folder> <device-name>      (LAB_API_URL / LAB_API_KEY / LAB_CA_CERT as usual)
"""
import csv
import re
import shutil
import sys
import time
from pathlib import Path

from .client import ApiClient, from_env

NAME_RE = re.compile(r"^(?P<plate>[A-Za-z0-9_-]+)__day(?P<day>\d+)__(?P<metric>[a-z0-9_]+)\.csv$")


def parse_file(path: Path) -> tuple[str, dict]:
    """Returns (plate barcode, upload body without device_id)."""
    m = NAME_RE.match(path.name)
    if not m:
        raise ValueError(f"unexpected file name {path.name}")
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    return m["plate"], {
        "batch_id": path.name,
        "day": int(m["day"]),
        "metric": m["metric"],
        "measured_at": rows[0]["measured_at"] if rows else None,
        "readings": [{"well": r["well"], "value": float(r["value"])} for r in rows],
    }


def process_file(path: Path, client: ApiClient, device_id: int, processed: Path) -> int:
    plate, body = parse_file(path)
    client.send(f"/plates/{plate}/readings", {**body, "device_id": device_id})
    processed.mkdir(exist_ok=True)
    shutil.move(str(path), processed / path.name)
    return len(body["readings"])


def watch(folder: Path, client: ApiClient, device_id: int, interval: float = 0.5):
    while True:
        client.flush_spool()
        for f in sorted(folder.glob("*.csv")):
            try:
                n = process_file(f, client, device_id, folder / "processed")
                print(f"{f.name}: {n} wells", flush=True)
            except ValueError as e:
                print("skipping:", e, flush=True)
                shutil.move(str(f), folder / (f.name + ".rejected"))
        time.sleep(interval)


if __name__ == "__main__":
    folder, device = Path(sys.argv[1]), sys.argv[2]
    folder.mkdir(parents=True, exist_ok=True)
    c = from_env(spool=folder / "spool.jsonl")
    watch(folder, c, c.device_id(device))
