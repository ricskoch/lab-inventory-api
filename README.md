# Lab Inventory API

Learning project: a small API that tracks lab samples and the results measured on them by devices.
Built step by step (see `docs/`). Stack: Python, FastAPI, SQLite (plain SQL), pytest.

## Run it
```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m app.keys create --name me --role admin   # prints your API key once
uvicorn app.main:app --reload
```
Open http://127.0.0.1:8000/docs, click Authorize and paste the key. Every endpoint except `/health` needs `X-API-Key`.
For HTTPS on the lab network with Docker see `docs/04-security.md`.

## Test it
```bash
pytest -q
```

## Layout
- `app/schema.sql`: the data model, in plain SQL
- `app/security.py`, `app/keys.py`: API keys, roles, key CLI
- `deploy/`, `Dockerfile`, `docker-compose.yml`: certificates and LAN deployment
- `app/db.py`: connection per request (a FastAPI dependency)
- `app/models.py`: request/response shapes + validation (Pydantic)
- `app/main.py`: endpoints
- `adapters/`: device adapters + shared client (retry, spool); `simulators/`: fake devices (see `docs/03-devices.md`)
- `tests/`: API, adapter and SQL-injection tests

## How a request flows
1. Client sends `POST /samples` with JSON.
2. FastAPI matches the route and validates the body against `SampleIn` (bad input -> 422, never reaches SQL).
3. The handler runs a **parameterised** SQL statement (`?` placeholders), which is injection-safe.
4. The row is read back, converted to the `Sample` response model, returned as JSON with status 201.

## How to expand
- New resource: add a table to `schema.sql`, models to `models.py`, endpoints to `main.py`, tests.
- Changing the schema later: move to migrations (Alembic) once data matters.
- Roadmap: plates resource, rate limiting, key expiry, PostgreSQL (see `docs/04-security.md`).
