# Step 4: Security and deployment on the lab network

## Threat model (what are we actually defending against?)
| Threat | Example | Control |
|---|---|---|
| Eavesdropping on the network | Someone sniffs keys or results on shared Wi-Fi/VLAN | HTTPS (TLS) |
| Unauthenticated access | Any machine on the LAN posts or deletes data | API keys on every endpoint |
| Over-privileged client | A hacked balance PC deletes all samples | Roles / least privilege |
| Stolen credentials | A key leaks from a script | Revocation, per-client keys, hashed storage |
| Denial of accountability | "I didn't change that" | Append-only audit log |
| Bad input | SQL injection, malformed JSON | Validation (Pydantic) + parameterised SQL |

## 1. Authentication: API keys
Every request needs `X-API-Key`. Each client (person, device) gets **its own key**, so one leak means revoking
one key, and the audit log shows exactly who acted. Keys are random 256-bit tokens, stored only as SHA-256
hashes (`app/security.py`): a database leak does not reveal usable keys. Human *passwords* would instead need
bcrypt/argon2 and a login flow, which is what you would add (or replace this with OAuth/SSO) for many human users.

```bash
python -m app.keys create --name richard --role admin
python -m app.keys create --name balance-1 --role device
python -m app.keys list
python -m app.keys revoke --name balance-1
```
The key is printed once. Key management is a server-side CLI on purpose: there is no HTTP endpoint to attack.

## 2. Authorisation: roles
| Role | May do |
|---|---|
| `admin` | everything, incl. register devices/samples and read `/audit` |
| `reader` | read samples, devices, runs (dashboards, ELN) |
| `device` | list devices, post runs. Nothing else |

A stolen device key therefore can add (idempotent) measurements but cannot read inventory, edit samples or
touch the audit log. `tests/test_security.py` pins these rules so a future change can't silently widen them.

## 3. Audit log
Every change and every rejected request (401/403) is recorded: who, role, method, path, status, IP.
SQLite triggers make the table append-only (no UPDATE/DELETE). Honest limits: someone with file access to
the database can still drop the triggers, so for GxP / 21 CFR Part 11 you would also ship the log to a
separate system and add electronic signatures and reason-for-change. This is the foundation, not compliance.

## 4. HTTPS
`deploy/make_certs.sh lab-api.local <server-ip>` creates a private lab CA plus a server certificate.
- `server.crt` + `server.key` stay on the API machine.
- `ca.crt` is copied to every client; adapters set `LAB_CA_CERT=/path/ca.crt`, so they only trust *your* CA.
- `ca.key` should be kept offline. Python 3.13 is strict: the CA needs the CA:TRUE and keyUsage extensions
  (the script sets them).
Alternative: put Caddy or nginx in front of the API and let it manage certificates; the API then listens on localhost only.

## 5. Deploy with Docker on the LAN
```bash
deploy/make_certs.sh lab-api.local 192.168.1.50      # use the server's real name/IP
docker compose up -d --build                         # API on https://<server>:8443
docker compose exec api python -m app.keys create --name richard --role admin
```
The container runs as an unprivileged user; the database lives in the `lab-data` volume. Open only port 8443 in
the host firewall, and only to the lab VLAN.

Run an adapter on the device PC (secrets via environment, never in code):
```bash
export LAB_API_URL=https://lab-api.local:8443 LAB_API_KEY=lab_... LAB_CA_CERT=ca.crt
python -m adapters.balance
```

## Verified vs not verified
Verified here: all 22 tests; the API served over TLS with a private CA; a device-key adapter ingesting over HTTPS;
plain HTTP and untrusted-cert clients being refused; audit entries for writes and rejections.
**Not verified:** the Dockerfile/compose were not built in the sandbox (no Docker daemon). Run
`docker compose up --build` and tell me if anything fails.

## Known gaps / next steps
- No rate limiting or lockout on repeated bad keys. Add `slowapi` or rate-limit at the reverse proxy.
- No key expiry/rotation policy. Add `expires_at` to `api_keys`.
- `/docs` is public (shows the API shape, no data). Disable with `FastAPI(docs_url=None)` in production.
- SQLite is fine for one lab; move to PostgreSQL for many concurrent writers (the SQL stays almost the same).
- Backups: copy the `lab-data` volume regularly; an untested backup is not a backup.
