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
    measured_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_runs_sample ON runs(sample_id);
