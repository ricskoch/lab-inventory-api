# Plates, wells, readings and events

Why new tables? The first model tracks single samples (tubes). A screening plate has 384 wells, each with its
own drug and dose, measured many times. One row per well-measurement needs its own place:

```
 plates --< wells --< readings >-- devices
    \                                  
     `--< events >-- devices          (robot moves, dispenses: things that are not measurements)
```
- `plates`: barcode + experiment name.
- `wells`: what is in each well (`role` = drug / vehicle / kill / blank; `drug` and `conc_um` only for drug wells).
- `readings`: one value per well, day and metric (`confluence_pct`, `cell_count`, `luminescence`).
- `events`: workflow steps, with a free-form JSON `detail`.

## Endpoints
| Request | Who | What |
|---|---|---|
| `POST /plates` | admin | create a plate and its well layout (1 to 384 wells, names A01..P24) |
| `GET /plates`, `GET /plates/{barcode}` | reader, admin | list plates / one plate with layout |
| `POST /plates/{barcode}/readings` | device, admin | upload up to 384 well values in one request |
| `GET /plates/{barcode}/readings` | reader, admin | filter by `metric`, `day`, `well`, `drug` |
| `POST /events`, `GET /events` | device, admin / reader, admin | workflow events, filter by `plate`, `kind` |

## Bulk upload and resending
A batch carries a `batch_id`. Each reading is stored as `batch_id:well`, which is unique. If the connection drops
and the controller resends the whole batch, wells already stored are skipped. The answer says how many were new:
`{"stored": 384, "already_had": 0}`. A batch with a well that is not on the plate is rejected as a whole.

## Times
`measured_at` / `ts` are optional. Without them the server's clock is used. A device may send its own time
(ISO, UTC). The simulation will use this to compress a week into minutes.
