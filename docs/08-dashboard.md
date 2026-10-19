# 08 - Streamlit dashboard

A web page that reads the lab API and shows a screening run: where each plate is, the plate map, growth curves,
dose-response fits and plate quality. It is **read-only**: it holds a `reader` key, so even a bug in it cannot
change data.

```
 browser  ->  Streamlit (dashboard/app.py)  --reader key-->  API  ->  SQLite
```

## Files
- `dashboard/api.py`: small client for `/plates`, `/plates/{barcode}/readings`, `/events`.
- `dashboard/analysis.py`: the maths, no Streamlit in it, so it is unit tested (`tests/test_dashboard_analysis.py`).
- `dashboard/app.py`: the page itself (`tests/test_dashboard.py` renders it against an in-process API).

## What the analysis does
1. **Normalise**: viability % = (value - kill mean) / (vehicle mean - kill mean) x 100. Kill control = 0 %, vehicle = 100 %.
   Option "same row": use the controls of the drug's own row, which removes the row A/P edge effect.
2. **Fit** a four-parameter-style Hill curve `100 / (1 + (x / IC50)^slope)` per drug with `scipy.optimize.curve_fit`.
   A drug whose lowest viability stays above 70 % is reported `inactive`, not forced into a fit.
3. **Z'** = 1 - 3 (sd_vehicle + sd_kill) / |mean_vehicle - mean_kill|. Above 0.5 is a good assay, below 0 means the controls overlap.

## Quickest way: one command
```
py -m pip install -r requirements.txt -r requirements-dashboard.txt     (Windows PowerShell)
py -m screening.demo
```
On Linux or Codespaces use `python` instead of `py`. Run it from the project folder (the one that contains `app` and `dashboard`).
It makes a throwaway database and fresh keys in `demo_data/`, starts the API and the dashboard, runs a simulated week and prints
the dashboard address. Open it and tick "Live view". Ctrl+C stops everything. Re-running starts from an empty database.
In Codespaces open the Ports tab and click the globe next to the printed port.

The manual steps below do the same thing in pieces, which is how you would run it against a real API.

## Run it by hand on Windows (PowerShell, no git)
Use **three PowerShell windows**, all in the project folder. Replace the `lab_...` values with your own keys.

**Window 1: the API**
```powershell
py -m pip install -r requirements.txt
py -m app.keys create --name dashboard --role reader     # prints the reader key once: copy it
py -m uvicorn app.main:app
```
If you do not have admin and device keys yet, also run `py -m app.keys create --name richard --role admin` and
`py -m app.keys create --name controller --role device` (stop the API first, or use another window).

**Window 2: a simulated week** (fills the database; a few minutes)
```powershell
py -m pip install -r requirements-dashboard.txt
$env:LAB_API_URL="http://127.0.0.1:8000"
$env:LAB_ADMIN_KEY="lab_..."
$env:LAB_DEVICE_KEY="lab_..."
py -m screening.run_week --plates 4 --day-seconds 6 --jam-rate 0.1
```

**Window 3: the dashboard**
```powershell
mkdir .streamlit
copy .streamlit\secrets.toml.example .streamlit\secrets.toml
notepad .streamlit\secrets.toml        # paste the reader key after LAB_API_KEY, save
py -m streamlit run dashboard/app.py
```
Streamlit opens http://localhost:8501. Tick "Live view" in the sidebar to watch a run while Window 2 is still going.

`.streamlit/secrets.toml` is in `.gitignore`. Never commit it: it holds a key.

## Troubleshooting
- "Cannot reach the lab API": Window 1 is not running, or the key in `secrets.toml` is wrong or not a reader/admin key.
- "The API has no plates yet": run Window 2 first.
- `streamlit` not found: always start it as `py -m streamlit ...`.
