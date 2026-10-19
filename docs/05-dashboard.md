# Querying runs for a dashboard

`GET /runs` lists measurements across all samples, newest first. Needs a `reader` (or `admin`) key.

| Parameter | Meaning | Example |
|---|---|---|
| `device` | device name | `plate-reader-1` |
| `sample` | sample barcode | `S-001` |
| `since` | at or after (UTC) | `2026-10-19` or `2026-10-19T08:00:00Z` |
| `until` | before (UTC) | `2026-10-20` |
| `limit` / `offset` | paging, default 100, max 1000 | `limit=500` |

Each row has names instead of IDs: `sample_barcode`, `device_name`, `result`, `measured_at`, `external_id`.

```bash
curl -s -H "X-API-Key: $KEY" "http://127.0.0.1:8000/runs?device=plate-reader-1&since=2026-10-19"
```

Streamlit sketch (runs on the server, key kept in `st.secrets`):
```python
import requests, pandas as pd, streamlit as st
API, H = st.secrets["LAB_API_URL"], {"X-API-Key": st.secrets["LAB_API_KEY"]}
rows = requests.get(f"{API}/runs", params={"device": "plate-reader-1", "limit": 500}, headers=H, timeout=5).json()
df = pd.json_normalize(rows)
st.dataframe(df)
```
