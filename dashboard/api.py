"""Thin read-only client for the lab API, used by the dashboard. Needs a `reader` key."""
import os

import httpx
import pandas as pd


class LabApi:
    def __init__(self, http: httpx.Client):
        self.http = http

    def _get(self, path: str, **params):
        r = self.http.get(path, params={k: v for k, v in params.items() if v is not None})
        r.raise_for_status()
        return r.json()

    def plates(self) -> list[dict]:
        return self._get("/plates")

    def readings(self, barcode: str, metric: str | None = None, day: int | None = None) -> pd.DataFrame:
        rows = self._get(f"/plates/{barcode}/readings", metric=metric, day=day, limit=20000)
        return pd.DataFrame(rows, columns=["well", "role", "drug", "conc_um", "day", "metric", "value", "device_name", "measured_at"])

    def events(self, plate: str | None = None, limit: int = 2000) -> pd.DataFrame:
        rows = self._get("/events", plate=plate, limit=limit)
        df = pd.DataFrame(rows, columns=["id", "kind", "plate_barcode", "device_name", "detail", "ts"])
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
        return df


def connect() -> LabApi:
    """Settings come from environment variables (LAB_API_URL, LAB_API_KEY, optional LAB_CA_CERT),
    else from .streamlit/secrets.toml."""
    import streamlit as st

    def setting(name: str, default: str | None = None) -> str | None:
        if name in os.environ:  # environment wins, so a one-off run can override the secrets file
            return os.environ[name]
        try:
            if name in st.secrets:
                return st.secrets[name]
        except Exception:  # no secrets file at all
            pass
        return default

    key = setting("LAB_API_KEY")
    if not key:
        raise RuntimeError("No LAB_API_KEY found. Put it in .streamlit/secrets.toml (see docs/08-dashboard.md).")
    return LabApi(httpx.Client(
        base_url=setting("LAB_API_URL", "http://127.0.0.1:8000"),
        headers={"X-API-Key": key},
        verify=setting("LAB_CA_CERT") or True,
        timeout=15,
    ))
