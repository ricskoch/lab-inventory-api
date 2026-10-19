import json
import sqlite3
from contextlib import asynccontextmanager

import sqlite3 as _sqlite3

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response

from .db import db_path, get_conn, init_db
from .plates import router as plates_router
from .security import require
from .models import Device, DeviceIn, Run, RunIn, Sample, SampleIn, SampleUpdate


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Lab Inventory API", version="0.1.0", lifespan=lifespan)
app.include_router(plates_router)


@app.middleware("http")
async def audit(request: Request, call_next):
    """Record every change (non-GET) and every rejected request (401/403)."""
    response = await call_next(request)
    if request.method != "GET" or response.status_code in (401, 403):
        # Blocking SQLite call inside async code is fine at lab scale; use a queue/thread at scale.
        conn = _sqlite3.connect(db_path())
        conn.execute(
            "INSERT INTO audit_log (actor, role, method, path, status, client_ip) VALUES (?,?,?,?,?,?)",
            (
                getattr(request.state, "actor", None),
                getattr(request.state, "role", None),
                request.method,
                request.url.path,
                response.status_code,
                request.client.host if request.client else None,
            ),
        )
        conn.commit()
        conn.close()
    return response


@app.get("/health")
def health():
    """Unauthenticated on purpose: load balancers and Docker health checks need it. Reveals nothing."""
    return {"status": "ok"}


@app.get("/audit", dependencies=[Depends(require("admin"))])
def audit_log(limit: int = Query(100, ge=1, le=1000), conn=Depends(get_conn)):
    return [dict(r) for r in conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))]


def _row(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()


# ---------- samples ----------

@app.post("/samples", response_model=Sample, status_code=201, dependencies=[Depends(require("admin"))])
def create_sample(body: SampleIn, conn=Depends(get_conn)):
    try:
        cur = conn.execute(
            "INSERT INTO samples (barcode, name, location) VALUES (?, ?, ?)",
            (body.barcode, body.name, body.location),
        )
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"Barcode {body.barcode!r} already exists")
    return dict(_row(conn, "SELECT * FROM samples WHERE id = ?", (cur.lastrowid,)))


@app.get("/samples", response_model=list[Sample], dependencies=[Depends(require("admin","reader"))])
def list_samples(
    status: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    conn=Depends(get_conn),
):
    sql, params = "SELECT * FROM samples", []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY id LIMIT ? OFFSET ?"
    return [dict(r) for r in conn.execute(sql, (*params, limit, offset))]


@app.get("/samples/{barcode}", response_model=Sample, dependencies=[Depends(require("admin","reader"))])
def get_sample(barcode: str, conn=Depends(get_conn)):
    row = _row(conn, "SELECT * FROM samples WHERE barcode = ?", (barcode,))
    if not row:
        raise HTTPException(404, "Sample not found")
    return dict(row)


@app.patch("/samples/{barcode}", response_model=Sample, dependencies=[Depends(require("admin"))])
def update_sample(barcode: str, body: SampleUpdate, conn=Depends(get_conn)):
    changes = body.model_dump(exclude_unset=True)
    if changes:
        # Column names come from the validated model, never from user input.
        sets = ", ".join(f"{k} = ?" for k in changes)
        cur = conn.execute(
            f"UPDATE samples SET {sets} WHERE barcode = ?", (*changes.values(), barcode)
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Sample not found")
    return get_sample(barcode, conn)


@app.delete("/samples/{barcode}", status_code=204, dependencies=[Depends(require("admin"))])
def delete_sample(barcode: str, conn=Depends(get_conn)):
    try:
        cur = conn.execute("DELETE FROM samples WHERE barcode = ?", (barcode,))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Sample has runs; mark it 'discarded' instead")
    if cur.rowcount == 0:
        raise HTTPException(404, "Sample not found")
    return Response(status_code=204)


# ---------- devices ----------

@app.post("/devices", response_model=Device, status_code=201, dependencies=[Depends(require("admin"))])
def create_device(body: DeviceIn, conn=Depends(get_conn)):
    try:
        cur = conn.execute(
            "INSERT INTO devices (name, kind, location) VALUES (?, ?, ?)",
            (body.name, body.kind, body.location),
        )
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"Device {body.name!r} already exists")
    return dict(_row(conn, "SELECT * FROM devices WHERE id = ?", (cur.lastrowid,)))


@app.get("/devices", response_model=list[Device], dependencies=[Depends(require("admin","reader","device"))])
def list_devices(conn=Depends(get_conn)):
    return [dict(r) for r in conn.execute("SELECT * FROM devices ORDER BY id")]


# ---------- runs (device results) ----------

def _run(row) -> dict:
    d = dict(row)
    d["result"] = json.loads(d["result"])
    return d


@app.post("/runs", response_model=Run, status_code=201, dependencies=[Depends(require("admin","device"))])
def create_run(body: RunIn, response: Response, conn=Depends(get_conn)):
    # Idempotency: an adapter that retries after a timeout sends the same external_id.
    # We return the existing run (200) instead of creating a duplicate measurement.
    if body.external_id:
        existing = _row(conn, "SELECT * FROM runs WHERE external_id = ?", (body.external_id,))
        if existing:
            response.status_code = 200
            return _run(existing)
    sample = _row(conn, "SELECT id FROM samples WHERE barcode = ?", (body.sample_barcode,))
    if not sample:
        raise HTTPException(404, f"Unknown sample barcode {body.sample_barcode!r}")
    if not _row(conn, "SELECT 1 FROM devices WHERE id = ?", (body.device_id,)):
        raise HTTPException(404, "Unknown device")
    cur = conn.execute(
        "INSERT INTO runs (sample_id, device_id, result, external_id) VALUES (?, ?, ?, ?)",
        (sample["id"], body.device_id, json.dumps(body.result), body.external_id),
    )
    return _run(_row(conn, "SELECT * FROM runs WHERE id = ?", (cur.lastrowid,)))


@app.get("/samples/{barcode}/runs", response_model=list[Run], dependencies=[Depends(require("admin","reader"))])
def runs_for_sample(barcode: str, conn=Depends(get_conn)):
    sample = _row(conn, "SELECT id FROM samples WHERE barcode = ?", (barcode,))
    if not sample:
        raise HTTPException(404, "Sample not found")
    rows = conn.execute(
        "SELECT * FROM runs WHERE sample_id = ? ORDER BY measured_at, id", (sample["id"],)
    )
    return [_run(r) for r in rows]
