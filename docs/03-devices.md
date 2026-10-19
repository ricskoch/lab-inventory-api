# Step 3: Connecting devices

Two simulated devices, two adapters. The adapters are the only code that knows how a device speaks;
the API only ever sees clean JSON.

| Device | Interface | Simulator | Adapter |
|---|---|---|---|
| Plate reader | CSV file dropped in a folder | `simulators/plate_reader_sim.py` | `adapters/plate_reader.py` |
| Balance | text lines over TCP | `simulators/balance_sim.py` | `adapters/balance.py` |

## Try it (4 terminals, API running on :8000)
```bash
uvicorn app.main:app                      # 0. API (register samples S-001..S-003 via /docs first)
python -m simulators.balance_sim          # 1. fake balance on :9100
python -m adapters.balance                # 2. its adapter
python -m simulators.plate_reader_sim     # 3. fake plate reader writing CSVs
python -m adapters.plate_reader           # 4. its adapter
curl localhost:8000/samples/S-001/runs    # see results arrive
```

## Design decisions worth understanding
1. **Idempotency (`external_id`).** Network calls fail in the middle: did the API get it or not? The adapter
   can't know, so it resends. A unique `external_id` per measurement (`file:row` or `device:timestamp:seq`)
   lets the API answer "already have it" (HTTP 200) instead of storing a duplicate. Without this, every
   outage creates duplicate data.
2. **Spool file.** If the API is down, the adapter appends the reading to a local JSONL file and flushes it
   when the API is back. A physical measurement often can't be repeated, so adapters must never drop one.
3. **Rejected vs retryable.** 404/422 means our data is wrong (unknown barcode); retrying forever would block
   the queue, so it's logged and skipped. Network errors/5xx are retried.
4. **Keep the raw file.** The plate-reader adapter moves files to `processed/` rather than deleting them,
   so a parsing bug can be fixed and the data replayed.
5. **Write-then-rename.** The simulator writes to a hidden temp name and renames when finished, so the adapter
   never reads a half-written file. Real instruments need the same care (or a "file stable for N seconds" check).

## Adapter pattern for your real devices
Parse -> build `{sample_barcode, device_id, result, external_id}` -> `client.send_run(...)`. For a serial device
replace the socket with `serial.Serial(port).readline()`. For a vendor SDK, call it inside the loop. For
SiLA 2/OPC UA use their client libraries. The rest (spool, retry, idempotency) is shared in `adapters/client.py`.

## What's missing (step 4)
Anyone on the network can currently POST. Step 4 adds per-device credentials, roles, audit log, HTTPS, Docker.

Note: `external_id` was added to the `runs` table. There is no migration tooling yet, so delete any existing
`lab.db` and restart. Real data would need an Alembic migration.
