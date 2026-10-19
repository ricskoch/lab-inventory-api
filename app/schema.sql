-- Plain SQL schema: the single source of truth for the data model.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS devices (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL,              -- e.g. 'plate_reader', 'balance'
    location    TEXT
);

CREATE TABLE IF NOT EXISTS samples (
    id          INTEGER PRIMARY KEY,
    barcode     TEXT NOT NULL UNIQUE,       -- sample identity: the key that ties everything together
    name        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'registered'
                CHECK (status IN ('registered','in_use','consumed','discarded')),
    location    TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- A run = one device measuring one sample. This is what makes the service useful:
-- sample identity + device + result + time.
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY,
    sample_id   INTEGER NOT NULL REFERENCES samples(id) ON DELETE RESTRICT,
    device_id   INTEGER NOT NULL REFERENCES devices(id) ON DELETE RESTRICT,
    result      TEXT NOT NULL,              -- JSON payload from the device
    external_id TEXT UNIQUE,                -- adapter-supplied ID; makes retries idempotent
    measured_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_runs_sample ON runs(sample_id);

-- Security: who may call the API. Only a hash of each key is stored.
CREATE TABLE IF NOT EXISTS api_keys (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,       -- e.g. 'plate-reader-1', 'richard'
    role        TEXT NOT NULL CHECK (role IN ('admin','device','reader')),
    key_hash    TEXT NOT NULL UNIQUE,
    revoked     INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- Append-only record of who did what. Written for every change and every rejected request.
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY,
    ts          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    actor       TEXT,                       -- NULL when the key was missing/unknown
    role        TEXT,
    method      TEXT NOT NULL,
    path        TEXT NOT NULL,
    status      INTEGER NOT NULL,
    client_ip   TEXT
);
-- Make the log tamper-resistant at the database level: no edits, no deletes.
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;

-- Plate-based experiments (e.g. a 384-well drug screen) -------------------------------------------
CREATE TABLE IF NOT EXISTS plates (
    id          INTEGER PRIMARY KEY,
    barcode     TEXT NOT NULL UNIQUE,
    experiment  TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- What was put in each well. One row per well that is used.
CREATE TABLE IF NOT EXISTS wells (
    plate_id    INTEGER NOT NULL REFERENCES plates(id) ON DELETE RESTRICT,
    well        TEXT NOT NULL,              -- 'A01' .. 'P24' on a 384-well plate
    role        TEXT NOT NULL CHECK (role IN ('drug','vehicle','kill','blank')),
    drug        TEXT,                       -- only for role = 'drug'
    conc_um     REAL,                       -- micromolar, only for role = 'drug'
    PRIMARY KEY (plate_id, well)
);

-- One measured value for one well: e.g. confluence of A01 on day 3 from the microscope.
CREATE TABLE IF NOT EXISTS readings (
    id          INTEGER PRIMARY KEY,
    plate_id    INTEGER NOT NULL,
    well        TEXT NOT NULL,
    device_id   INTEGER NOT NULL REFERENCES devices(id) ON DELETE RESTRICT,
    day         INTEGER NOT NULL,           -- experiment day, 0 = seeding day
    metric      TEXT NOT NULL,              -- 'confluence_pct', 'cell_count', 'luminescence'
    value       REAL NOT NULL,
    measured_at TEXT NOT NULL,
    external_id TEXT NOT NULL UNIQUE,       -- batch_id:well, so resending a batch is harmless
    FOREIGN KEY (plate_id, well) REFERENCES wells(plate_id, well) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_readings_plate ON readings(plate_id, metric, day);

-- Workflow steps that are not measurements: robot moves, dispenses, incubator in/out.
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY,
    plate_id    INTEGER REFERENCES plates(id) ON DELETE RESTRICT,
    device_id   INTEGER REFERENCES devices(id) ON DELETE RESTRICT,
    kind        TEXT NOT NULL,              -- 'move', 'seed', 'add_drug', 'image', 'read'
    detail      TEXT NOT NULL DEFAULT '{}', -- JSON
    ts          TEXT NOT NULL,
    external_id TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_events_plate ON events(plate_id, ts);
