"""Shared API client for all adapters.

Two jobs that every adapter needs:
1. Retry-safe delivery: every run carries an external_id, so resending is harmless.
2. Local spool: if the API is unreachable, readings are appended to a JSONL file and
   re-sent later. A lab device measurement can't be repeated, so never drop one.
"""
import json
from pathlib import Path

import httpx


class ApiClient:
    def __init__(self, base_url: str, spool: Path, http: httpx.Client | None = None):
        self.http = http or httpx.Client(base_url=base_url, timeout=5)
        self.spool = Path(spool)

    def ensure_device(self, name: str, kind: str, location: str | None = None) -> int:
        r = self.http.get("/devices")
        r.raise_for_status()
        for d in r.json():
            if d["name"] == name:
                return d["id"]
        r = self.http.post("/devices", json={"name": name, "kind": kind, "location": location})
        r.raise_for_status()
        return r.json()["id"]

    def ensure_sample(self, barcode: str) -> None:
        """Dev convenience for the simulators. Real setups register samples via the LIMS/ELN."""
        if self.http.get(f"/samples/{barcode}").status_code == 404:
            self.http.post("/samples", json={"barcode": barcode, "name": f"Sample {barcode}"})

    def send_run(self, payload: dict) -> bool:
        """True if delivered (or already known). False if spooled for later."""
        try:
            r = self.http.post("/runs", json=payload)
        except httpx.TransportError:
            self._spool(payload)
            return False
        if r.status_code in (200, 201):
            return True
        if r.status_code in (404, 422):
            # Our data is wrong (unknown barcode, bad payload); retrying won't help.
            print(f"REJECTED {r.status_code}: {r.text} payload={payload}")
            return True
        self._spool(payload)  # 5xx etc.: try again later
        return False

    def _spool(self, payload: dict) -> None:
        with self.spool.open("a") as f:
            f.write(json.dumps(payload) + "\n")

    def flush_spool(self) -> int:
        if not self.spool.exists():
            return 0
        lines = self.spool.read_text().splitlines()
        self.spool.unlink()
        sent = 0
        for line in lines:
            if self.send_run(json.loads(line)):
                sent += 1
        return sent
