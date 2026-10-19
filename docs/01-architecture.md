# Step 1: Architecture and concepts

## The analogy
The API is the **reception desk of a core facility**. Nobody walks into the freezer room; you hand a
standardised submission form (a *request*) to the desk, and you get a standardised answer (a *response*).
The desk checks the form, applies the rules, and only then touches the storage (the database).

## The whole chain

```
 [Plate reader]  [Balance]  [Pipetting robot]      <- standalone devices
       |             |              |
   CSV in folder  TCP stream    vendor SDK/SiLA2   <- how each one "speaks"
       |             |              |
   [adapter]     [adapter]      [adapter]          <- small translator processes
        \            |             /
         \           |            /   HTTPS + device API key
          +----->  [ API  ] <-----+------ humans / ELN / dashboards (HTTPS + user login)
                     |
                [ Database ]      <- SQL, single source of truth
```

Devices should **never** talk to the database directly. The API is the one place where validation,
authentication, permissions and audit logging happen.

## HTTP in five lines
- **Endpoint** = URL that identifies a thing: `/samples/S-001`
- **Verb** = what to do: GET read, POST create, PATCH change, DELETE remove
- **Status code** = outcome: 200 ok, 201 created, 404 not found, 409 conflict, 422 invalid input, 401/403 not allowed
- **Body** = JSON data
- **Stateless** = every request carries everything needed (including credentials)

## How devices actually talk (and what the adapter does)
| Device interface | Typical adapter | Notes |
|---|---|---|
| Result file dropped into a folder (plate readers) | Watch folder, parse CSV, POST | Most common; robust, easy |
| Serial RS-232 / USB (balances, pH meters) | `pyserial` reader loop, POST per reading | Needs a PC next to the device |
| TCP/IP socket stream | Socket client, parse lines, POST | Often vendor-specific text protocol |
| Vendor SDK / REST (robots, newer readers) | Call SDK, POST | Check licence terms |
| SiLA 2 / OPC UA (standards) | Standard client library | Best long-term, still rare |

Adapter rules of thumb: **buffer locally if the API is down** (never lose a reading), **retry with the same
ID** so a retry doesn't create duplicates, and **keep the raw file** next to the parsed result.

## What must be connected for the service to be useful
A measurement is only meaningful if you can answer: *which sample* (barcode), *which device*, *what result*,
*when*, and *who/what submitted it*. Hence the `runs` table links `samples` and `devices` and stores the
result payload. Without sample identity (barcodes on tubes/plates that devices can read or an operator scans),
the data cannot be joined to anything.

Later steps add: device adapters (step 3), authentication/roles/audit (step 4), plates as a second resource.
