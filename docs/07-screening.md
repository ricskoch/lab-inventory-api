# Simulated weekly drug screen

A whole screening week, run by a controller PC, with simulated instruments, feeding the API.

```
 controller PC ──text commands──> robot arm :9201     MOVE <plate> <FROM> <TO>
   (schedule)   ──────────────────> dispenser :9202   SEED | ADD_DRUG
                ──────────────────> microscope :9203  IMAGE <plate> <day>   --> CSV file --+
                ──────────────────> plate reader :9204 READ <plate> <day>   --> CSV file --+
        |                                                                                  |
        | POST /events (every step)                          adapters/plate_files.py  <----+
        v                                                         | POST /plates/{id}/readings (384 wells)
                         [ API ] <────────────────────────────────+
```
The four devices live in one process (`screening/lab_sim.py`), each on its own TCP port, like instruments
with existing communication ports. The world enforces physics: a plate can only be imaged while it is on the
imager, a station holds one plate, a plate cannot be seeded twice.

## The week (day 0 = Monday)
| Day | Per plate |
|---|---|
| 0 Mon | arm: stock → dispenser, **seed** cells, arm: → incubator |
| 1 Tue | arm → imager, **baseline image**, arm → dispenser, **add drugs**, arm → incubator |
| 2, 3 | arm → imager, **image**, arm → incubator |
| 4 Fri | arm → imager, **image**, arm → reader, **plate reader** (luminescence), arm → waste |

Per plate that is 4 × 384 confluence values + 384 luminescence values = **1,920 readings**. Four plates are
handled one after another, 30 simulated minutes apart, starting at 08:00.

## Plate layout (384 wells)
Each row is one drug: columns 02-11 and 12-21 are two replicate 10-point dose series (10 µM down to 0.5 nM,
3-fold steps). Column 22 is vehicle (DMSO), column 23 is the kill control, columns 01 and 24 are media blanks.
320 drug wells + 16 vehicle + 16 kill + 32 blank. Drug names are unique across plates (`CMP-1A` ... `CMP-4P`).

## The biology
- Cells grow logistically (24 h doubling, capacity 40,000 per well, mild edge effect on rows A and P, 5% well variation).
- Each drug has a hidden **IC50** and Hill slope (`screening/biology.py: truth()`), spread over 0.003 to 3 µM. One drug in eight is inactive.
- After the drug goes on, survival falls towards `1 - 0.95 × effect` with a 24 h time constant. So the dose at which
  luminescence is half way between vehicle and kill control **is** the hidden IC50. The analysis should recover it.
- Confluence = cells / capacity plus noise. Luminescence = background + 5 RLU per cell, with 8% noise.

## Run it
The API must be running, with an admin key and a device key (create them with `python -m app.keys`).
```bash
export LAB_API_URL=http://127.0.0.1:8000  LAB_ADMIN_KEY=lab_...  LAB_DEVICE_KEY=lab_...
python -m screening.run_week --plates 4 --day-seconds 30         # a week in 2.5 minutes
python -m screening.run_week --plates 4 --day-seconds 6 --jam-rate 0.1   # faster, with random arm jams
```
On Windows PowerShell use `$env:LAB_API_URL="..."` and `py -m screening.run_week ...`.
It creates the devices and plates (admin key), starts the simulator and both adapters, runs the controller,
waits for the uploads, then prints how many readings each plate has and how well the IC50s were recovered.

## Where the realism stops
- Cells and drugs are modelled with one equation each. Real screens have edge effects, plate drift, outliers.
- The dispenser says "add drug library". A real one writes a transfer log (source well → destination well, volume),
  and the dose should be calculated from it.
- The controller trusts that the plate it moved is the plate it meant. A real robot reads the plate barcode at each station.
- Time is compressed with `--day-seconds`; readings carry their simulated timestamp (`measured_at`).
