"""Adapter for a plate reader that drops a CSV file into a folder.

Pattern: watch folder -> parse -> POST -> move the file to processed/ (raw file kept).
CSV columns: sample_barcode,od600
"""
import csv
import shutil
import sys
import time
from pathlib import Path

from .client import ApiClient


def parse_csv(path: Path) -> list[dict]:
    rows = []
    with path.open(newline="") as f:
        for i, row in enumerate(csv.DictReader(f), start=1):
            rows.append({
                "sample_barcode": row["sample_barcode"].strip(),
                "result": {"od600": float(row["od600"])},
                # filename + row number is stable, so re-processing the same file is harmless
                "external_id": f"{path.name}:{i}",
            })
    return rows


def process_file(path: Path, client: ApiClient, device_id: int, processed: Path) -> int:
    rows = parse_csv(path)
    for r in rows:
        client.send_run({**r, "device_id": device_id})
    processed.mkdir(exist_ok=True)
    shutil.move(str(path), processed / path.name)
    return len(rows)


def watch(folder: Path, client: ApiClient, device_id: int, interval: float = 2.0):
    while True:
        client.flush_spool()
        for f in sorted(folder.glob("*.csv")):
            n = process_file(f, client, device_id, folder / "processed")
            print(f"{f.name}: {n} readings")
        time.sleep(interval)


if __name__ == "__main__":
    folder = Path(sys.argv[1] if len(sys.argv) > 1 else "plate_reader_out")
    folder.mkdir(exist_ok=True)
    c = ApiClient("http://127.0.0.1:8000", spool=folder / "spool.jsonl")
    dev = c.ensure_device("plate-reader-1", "plate_reader", "Bench 2")
    print("watching", folder)
    watch(folder, c, dev)
