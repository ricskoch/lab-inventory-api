"""Plates, wells, bulk readings and workflow events for plate-based experiments."""
import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator

from .db import get_conn
from .security import require

router = APIRouter()

WELL_RE = re.compile(r"^[A-P](0[1-9]|1[0-9]|2[0-4])$")  # 384-well plate: rows A-P, columns 01-24
Role = Literal["drug", "vehicle", "kill", "blank"]


def _stamp(value: str | None, name: str = "measured_at") -> str:
    """ISO date/datetime in, stored UTC format out. None means 'now' (server clock)."""
    dt = datetime.now(timezone.utc)
    if value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(422, f"{name} must be an ISO datetime, e.g. 2026-10-19T08:00:00Z")
        if dt.tzinfo:
            dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _check_well(well: str) -> str:
    if not WELL_RE.match(well):
        raise ValueError(f"{well!r} is not a 384-well name (A01 to P24)")
    return well


# ---------- models ----------

class WellIn(BaseModel):
    well: str
    role: Role
    drug: Optional[str] = Field(default=None, max_length=100)
    conc_um: Optional[float] = Field(default=None, gt=0)

    _w = field_validator("well")(_check_well)

    @model_validator(mode="after")
    def drug_fields_match_role(self):
        if self.role == "drug" and (not self.drug or self.conc_um is None):
            raise ValueError("wells with role 'drug' need drug and conc_um")
        if self.role != "drug" and (self.drug or self.conc_um is not None):
            raise ValueError("only wells with role 'drug' may have drug or conc_um")
        return self


class PlateIn(BaseModel):
    barcode: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    experiment: str = Field(min_length=1, max_length=200)
    wells: list[WellIn] = Field(min_length=1, max_length=384)

    @model_validator(mode="after")
    def wells_unique(self):
        names = [w.well for w in self.wells]
        if len(names) != len(set(names)):
            raise ValueError("a well appears more than once")
        return self


class Plate(BaseModel):
    barcode: str
    experiment: str
    created_at: str
    n_wells: int


class PlateDetail(Plate):
    wells: list[WellIn]


class ReadingIn(BaseModel):
    well: str
    value: float

    _w = field_validator("well")(_check_well)


class ReadingBatch(BaseModel):
    batch_id: str = Field(min_length=1, max_length=120, description="unique per upload; makes resending safe")
    device_id: int
    day: int = Field(ge=0, le=365)
    metric: str = Field(min_length=1, max_length=50)
    measured_at: Optional[str] = Field(default=None, description="device time (ISO, UTC); default: server time")
    readings: list[ReadingIn] = Field(min_length=1, max_length=384)

    @model_validator(mode="after")
    def wells_unique(self):
        names = [r.well for r in self.readings]
        if len(names) != len(set(names)):
            raise ValueError("a well appears more than once in the batch")
        return self


class BatchResult(BaseModel):
    stored: int
    already_had: int


class ReadingOut(BaseModel):
    well: str
    role: Role
    drug: Optional[str]
    conc_um: Optional[float]
    day: int
    metric: str
    value: float
    device_name: str
    measured_at: str


class EventIn(BaseModel):
    kind: str = Field(min_length=1, max_length=50)
    plate_barcode: Optional[str] = None
    device_id: Optional[int] = None
    detail: dict[str, Any] = Field(default_factory=dict)
    ts: Optional[str] = Field(default=None, description="event time (ISO, UTC); default: server time")
    external_id: Optional[str] = Field(default=None, max_length=200)


class EventOut(BaseModel):
    id: int
    kind: str
    plate_barcode: Optional[str]
    device_name: Optional[str]
    detail: dict[str, Any]
    ts: str


# ---------- helpers ----------

def _plate_id(conn, barcode: str) -> int:
    row = conn.execute("SELECT id FROM plates WHERE barcode = ?", (barcode,)).fetchone()
    if not row:
        raise HTTPException(404, f"Plate {barcode!r} not found")
    return row["id"]


# ---------- plates ----------

@router.post("/plates", response_model=Plate, status_code=201, dependencies=[Depends(require("admin"))])
def create_plate(body: PlateIn, conn=Depends(get_conn)):
    conn.execute("BEGIN")
    try:
        cur = conn.execute("INSERT INTO plates (barcode, experiment) VALUES (?, ?)", (body.barcode, body.experiment))
        conn.executemany(
            "INSERT INTO wells (plate_id, well, role, drug, conc_um) VALUES (?, ?, ?, ?, ?)",
            [(cur.lastrowid, w.well, w.role, w.drug, w.conc_um) for w in body.wells],
        )
        conn.execute("COMMIT")
    except sqlite3.IntegrityError:
        conn.execute("ROLLBACK")
        raise HTTPException(409, f"Plate {body.barcode!r} already exists")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    row = conn.execute("SELECT barcode, experiment, created_at FROM plates WHERE id = ?", (cur.lastrowid,)).fetchone()
    return {**dict(row), "n_wells": len(body.wells)}


@router.get("/plates", response_model=list[Plate], dependencies=[Depends(require("admin", "reader"))])
def list_plates(conn=Depends(get_conn)):
    rows = conn.execute(
        "SELECT p.barcode, p.experiment, p.created_at, COUNT(w.well) AS n_wells "
        "FROM plates p LEFT JOIN wells w ON w.plate_id = p.id GROUP BY p.id ORDER BY p.id"
    )
    return [dict(r) for r in rows]


@router.get("/plates/{barcode}", response_model=PlateDetail, dependencies=[Depends(require("admin", "reader"))])
def get_plate(barcode: str, conn=Depends(get_conn)):
    pid = _plate_id(conn, barcode)
    p = conn.execute("SELECT barcode, experiment, created_at FROM plates WHERE id = ?", (pid,)).fetchone()
    wells = [dict(r) for r in conn.execute(
        "SELECT well, role, drug, conc_um FROM wells WHERE plate_id = ? ORDER BY well", (pid,))]
    return {**dict(p), "n_wells": len(wells), "wells": wells}


# ---------- readings ----------

@router.post("/plates/{barcode}/readings", response_model=BatchResult, status_code=201,
             dependencies=[Depends(require("admin", "device"))])
def upload_readings(barcode: str, body: ReadingBatch, conn=Depends(get_conn)):
    """Store up to 384 well values in one request. Sending the same batch_id again stores nothing new."""
    pid = _plate_id(conn, barcode)
    if not conn.execute("SELECT 1 FROM devices WHERE id = ?", (body.device_id,)).fetchone():
        raise HTTPException(404, "Unknown device")
    known = {r["well"] for r in conn.execute("SELECT well FROM wells WHERE plate_id = ?", (pid,))}
    missing = sorted(r.well for r in body.readings if r.well not in known)
    if missing:
        raise HTTPException(404, f"Wells not defined on plate {barcode!r}: {', '.join(missing[:5])}")
    ts = _stamp(body.measured_at)
    before = conn.total_changes
    conn.execute("BEGIN")
    try:
        conn.executemany(
            "INSERT OR IGNORE INTO readings (plate_id, well, device_id, day, metric, value, measured_at, external_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(pid, r.well, body.device_id, body.day, body.metric, r.value, ts, f"{body.batch_id}:{r.well}")
             for r in body.readings],
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    stored = conn.total_changes - before
    return {"stored": stored, "already_had": len(body.readings) - stored}


@router.get("/plates/{barcode}/readings", response_model=list[ReadingOut],
            dependencies=[Depends(require("admin", "reader"))])
def list_readings(
    barcode: str,
    metric: str | None = None,
    day: int | None = Query(None, ge=0),
    well: str | None = None,
    drug: str | None = None,
    limit: int = Query(5000, ge=1, le=20000),
    offset: int = Query(0, ge=0),
    conn=Depends(get_conn),
):
    pid = _plate_id(conn, barcode)
    where, params = ["r.plate_id = ?"], [pid]
    for col, val in (("r.metric", metric), ("r.day", day), ("r.well", well), ("w.drug", drug)):
        if val is not None:
            where.append(f"{col} = ?"); params.append(val)
    sql = (
        "SELECT r.well, w.role, w.drug, w.conc_um, r.day, r.metric, r.value, d.name AS device_name, r.measured_at "
        "FROM readings r JOIN wells w ON w.plate_id = r.plate_id AND w.well = r.well "
        "JOIN devices d ON d.id = r.device_id WHERE " + " AND ".join(where)  # fixed fragments; values are ? params
        + " ORDER BY r.day, r.well LIMIT ? OFFSET ?"
    )
    return [dict(r) for r in conn.execute(sql, (*params, limit, offset))]


# ---------- workflow events ----------

@router.post("/events", response_model=EventOut, status_code=201, dependencies=[Depends(require("admin", "device"))])
def create_event(body: EventIn, conn=Depends(get_conn)):
    if body.external_id:
        dup = conn.execute("SELECT id FROM events WHERE external_id = ?", (body.external_id,)).fetchone()
        if dup:
            return _event(conn, dup["id"])
    pid = _plate_id(conn, body.plate_barcode) if body.plate_barcode else None
    if body.device_id is not None and not conn.execute("SELECT 1 FROM devices WHERE id = ?", (body.device_id,)).fetchone():
        raise HTTPException(404, "Unknown device")
    cur = conn.execute(
        "INSERT INTO events (plate_id, device_id, kind, detail, ts, external_id) VALUES (?, ?, ?, ?, ?, ?)",
        (pid, body.device_id, body.kind, json.dumps(body.detail), _stamp(body.ts, "ts"), body.external_id),
    )
    return _event(conn, cur.lastrowid)


def _event(conn, event_id: int) -> dict:
    r = conn.execute(
        "SELECT e.id, e.kind, p.barcode AS plate_barcode, d.name AS device_name, e.detail, e.ts "
        "FROM events e LEFT JOIN plates p ON p.id = e.plate_id LEFT JOIN devices d ON d.id = e.device_id "
        "WHERE e.id = ?", (event_id,)).fetchone()
    return {**dict(r), "detail": json.loads(r["detail"])}


@router.get("/events", response_model=list[EventOut], dependencies=[Depends(require("admin", "reader"))])
def list_events(
    plate: str | None = None,
    kind: str | None = None,
    limit: int = Query(200, ge=1, le=2000),
    conn=Depends(get_conn),
):
    where, params = [], []
    if plate:
        where.append("p.barcode = ?"); params.append(plate)
    if kind:
        where.append("e.kind = ?"); params.append(kind)
    sql = (
        "SELECT e.id FROM events e LEFT JOIN plates p ON p.id = e.plate_id"
        + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY e.ts DESC, e.id DESC LIMIT ?"
    )
    return [_event(conn, r["id"]) for r in conn.execute(sql, (*params, limit)).fetchall()]
