"""Adapter for a balance that streams lines over TCP: 'S-001,12.345,g'.

Pattern: socket client -> parse line -> POST. Same shape works for a serial port
(replace the socket with pyserial's Serial().readline()).
"""
import socket
import sys
import time

from .client import ApiClient


def parse_line(line: str, seq: int, device_name: str) -> dict:
    barcode, value, unit = line.strip().split(",")
    return {
        "sample_barcode": barcode,
        "result": {"mass": float(value), "unit": unit},
        # a device-side counter would be best; here we use the timestamp+seq of receipt
        "external_id": f"{device_name}:{int(time.time() * 1000)}:{seq}",
    }


def run(host: str, port: int, client: ApiClient, device_id: int, device_name: str):
    seq = 0
    while True:
        try:
            with socket.create_connection((host, port), timeout=10) as s:
                buf = s.makefile("r")
                for line in buf:
                    if not line.strip():
                        continue
                    seq += 1
                    client.send_run({**parse_line(line, seq, device_name), "device_id": device_id})
                    client.flush_spool()
        except OSError as e:
            print("balance connection lost:", e, "- retrying in 3s")
            time.sleep(3)


if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 9100
    c = ApiClient("http://127.0.0.1:8000", spool="balance_spool.jsonl")
    dev = c.ensure_device("balance-1", "balance", "Bench 1")
    run(host, port, c, dev, "balance-1")
