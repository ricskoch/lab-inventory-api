"""Writes a fake plate-reader CSV into the watched folder every few seconds."""
import csv
import random
import sys
import time
from pathlib import Path

folder = Path(sys.argv[1] if len(sys.argv) > 1 else "plate_reader_out")
folder.mkdir(exist_ok=True)
n = 0
while True:
    n += 1
    tmp = folder / f".plate_{n:03d}.tmp"  # write hidden, then rename: avoids half-written files
    with tmp.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["sample_barcode", "od600"])
        for i in range(1, 4):
            w.writerow([f"S-{i:03d}", round(random.uniform(0.05, 1.2), 3)])
    tmp.rename(folder / f"plate_{n:03d}.csv")
    print("wrote plate", n)
    time.sleep(10)
