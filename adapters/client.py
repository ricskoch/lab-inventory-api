"""Shared API client for all adapters.

Two jobs that every adapter needs:
1. Retry-safe delivery: every run carries an external_id, so resending is harmless.
2. Local spool: if the API is unreachable, readings are appended to a JSONL file and
   re-sent later. A lab device measurement can't be repeated, so never drop one.
"""
import json
import os
from pathlib import Path

import httpx


def from_env(spool: Path) -> "ApiClient":
    """Config via environment so secrets never sit in code or on the command line.

    LAB_API_URL (e.g. https://lab-api.local:8443), LAB_API_KEY (a 'device' key),
    LAB_CA_CERT (path to the lab CA certificate; leave unset if the cert is publicly trusted).
    """
    http = httpx.Client(
        base_url=os.environ.get("LAB_API_URL", "http://127.0.0.1:8000"),
        headers={"X-API-Key": os.environ["LAB_API_KEY"]},
        verify=os.environ.get("LAB_CA_CERT") or True,
        timeout=5,
    )
    return ApiClient("", spool=spool, http=http)


class ApiClient:
    def __init__(self, base_url: str, spool: Path, http: httpx.Client | None = None):
        self.http = http or httpx.Client(base_url=base_url, timeout=5)
        self.spool = Path(spool)

    def device_id(self, name: str) -> int:
        """Devices are registered by an admin; a device key may only look itself up."""
        r = self.http.get("/devices")
        r.raise_for_status()
        for d in r.json():
            if d["name"] == name:
                return d["id"]
        raise SystemExit(f"Device {name!r} is not registered. Ask an admin to POST /devices first.")

    def send_run(self, payload: dict) -> bool:
        """True if delivered (or already known). False if spooled for later."""
        try:
            r = self.http.post("/runs", json=payload)
        except httpx.TransportError:
            self._spool(payload)
            return False
        if r.status_code in (200, 201):
            return True
        if r.status_code in (401, 403):
            raise SystemExit(f"API refused our key ({r.status_code}). Check LAB_API_KEY / role; not retrying.")
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
