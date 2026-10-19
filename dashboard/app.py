"""Screening dashboard. Run:  py -m streamlit run dashboard/app.py"""
import time

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

from dashboard import analysis, api as lab_api

try:  # only present in this repo: lets the dashboard check its fits against the simulation's hidden truth
    from screening.biology import truth as sim_truth
except ImportError:
    sim_truth = None

st.set_page_config(page_title="Screening dashboard", layout="wide")


@st.cache_resource
def get_api() -> lab_api.LabApi:
    return lab_api.connect()


@st.cache_data(ttl=5, show_spinner=False)
def load_plates(_api):
    return _api.plates()


@st.cache_data(ttl=5, show_spinner=False)
def load_readings(_api, barcode):
    return _api.readings(barcode)


@st.cache_data(ttl=5, show_spinner=False)
def load_events(_api):
    return _api.events()


# ---------------------------------------------------------------- connection and plate choice
try:
    api = get_api()
    plates = load_plates(api)
except Exception as e:  # wrong key, API not running, no secrets file
    st.title("Screening dashboard")
    st.error(f"Cannot reach the lab API: {e}")
    st.info("Check that the API is running, and that .streamlit/secrets.toml holds LAB_API_URL and a reader LAB_API_KEY.")
    st.stop()

st.sidebar.title("Screening")
if not plates:
    st.title("Screening dashboard")
    st.info("The API has no plates yet. Start a run with `py -m screening.run_week` and come back.")
    st.stop()

barcodes = [p["barcode"] for p in plates]
plate = st.sidebar.selectbox("Plate", barcodes, index=len(barcodes) - 1)
if st.sidebar.button("Refresh now"):
    st.cache_data.clear()
    st.rerun()
live = st.sidebar.checkbox("Live view (refresh every 5 s)", value=False)
st.sidebar.caption(f"{len(barcodes)} plates in the database")

df = load_readings(api, plate)
events = load_events(api)
st.title(f"Plate {plate}")

if df.empty:
    st.info("No readings for this plate yet. They appear as the microscope and plate reader upload files.")

tab_flow, tab_map, tab_growth, tab_dose, tab_qc = st.tabs(
    ["Workflow", "Plate map", "Growth", "Dose-response", "Plate QC"])

# ---------------------------------------------------------------- workflow
with tab_flow:
    if events.empty:
        st.info("No workflow events logged yet.")
    else:
        mine = events[events["plate_barcode"] == plate]
        c1, c2, c3 = st.columns(3)
        c1.metric("Events for this plate", len(mine))
        c2.metric("Events, all plates", len(events))
        c3.metric("Latest event", events["ts"].max().strftime("%a %H:%M") if len(events) else "-")
        fig = px.scatter(events, x="ts", y="plate_barcode", color="kind", hover_data=["device_name"], height=320)
        fig.update_traces(marker=dict(size=9))
        fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), yaxis_title=None, xaxis_title="simulated time")
        st.plotly_chart(fig, width="stretch")
        moves = events[events["kind"] == "move"].copy()
        moves["location"] = moves["detail"].apply(lambda d: d["command"].split()[-1])
        where = moves.sort_values("ts").groupby("plate_barcode").tail(1)[["plate_barcode", "location", "ts"]]
        st.subheader("Where each plate is now")
        st.dataframe(where.rename(columns={"plate_barcode": "plate", "ts": "since"}), hide_index=True, width="stretch")
        st.subheader("Event log")
        log = mine.sort_values("ts", ascending=False).head(50).copy()
        log["command"] = log["detail"].apply(lambda d: d.get("command", ""))
        log["reply"] = log["detail"].apply(lambda d: d.get("reply", ""))
        st.dataframe(log[["ts", "device_name", "kind", "command", "reply"]], hide_index=True, width="stretch")

# ---------------------------------------------------------------- plate map
with tab_map:
    if df.empty:
        st.info("Nothing to show yet.")
    else:
        c1, c2, c3 = st.columns(3)
        metric = c1.selectbox("Readout", sorted(df["metric"].unique()), key="map_metric")
        days = sorted(df.loc[df["metric"] == metric, "day"].unique())
        day = c2.selectbox("Day", days, index=len(days) - 1, key="map_day")
        view = c3.radio("Show", ["Measured values", "Plate layout"], horizontal=True)
        sub = df[(df["metric"] == metric) & (df["day"] == day)]
        meta = analysis.add_row_col(sub)
        rows, cols = list("ABCDEFGHIJKLMNOP"), list(range(1, 25))
        hover = meta.assign(
            text=lambda d: d["well"] + "<br>" + d["role"] + np.where(d["drug"].notna(), "<br>" + d["drug"].fillna("") + " " + d["conc_um"].map("{:.3g}".format) + " µM", "")
            + "<br>value " + d["value"].map("{:.4g}".format))
        txt = hover.pivot(index="row", columns="col", values="text").reindex(index=rows, columns=cols)
        if view == "Measured values":
            z = analysis.grid(sub)
            fig = go.Figure(go.Heatmap(z=z.values, x=cols, y=rows, text=txt.values, hoverinfo="text", colorscale="Viridis",
                                       colorbar=dict(title=metric)))
        else:
            codes = {"blank": 0, "vehicle": 1, "kill": 2, "drug": 3}
            z = meta.assign(code=meta["role"].map(codes)).pivot(index="row", columns="col", values="code").reindex(index=rows, columns=cols)
            fig = go.Figure(go.Heatmap(z=z.values, x=cols, y=rows, text=txt.values, hoverinfo="text", showscale=False,
                                       colorscale=[[0, "#bbb"], [0.33, "#4c9"], [0.66, "#d55"], [1, "#58c"]], zmin=0, zmax=3))
            st.caption("grey = media blank, green = vehicle (DMSO), red = kill control, blue = drug wells (one drug per row)")
        fig.update_yaxes(autorange="reversed", title=None)
        fig.update_xaxes(dtick=1, title=None, side="top")
        fig.update_layout(height=520, margin=dict(l=0, r=0, t=30, b=0))
        st.plotly_chart(fig, width="stretch")

# ---------------------------------------------------------------- growth
with tab_growth:
    conf = df[df["metric"] == "confluence_pct"]
    if conf.empty:
        st.info("No microscope readings yet.")
    else:
        drugs = sorted(conf["drug"].dropna().unique())
        drug = st.selectbox("Drug", drugs, key="growth_drug")
        doses = sorted(conf.loc[conf["drug"] == drug, "conc_um"].unique())
        pick = st.multiselect("Doses (µM)", doses, default=[doses[-1], doses[len(doses) // 2], doses[0]],
                              format_func=lambda x: f"{x:.3g}")
        gc = analysis.growth_curves(conf, drug, pick)
        fig = px.line(gc, x="day", y="mean", color="series", error_y="std", markers=True)
        fig.add_vline(x=1, line_dash="dot", annotation_text="drug added", annotation_position="top left")
        fig.update_layout(yaxis_title="confluence (%)", xaxis_title="day (0 = seeding)", height=420, margin=dict(l=0, r=0, t=30, b=0))
        fig.update_xaxes(dtick=1)
        st.plotly_chart(fig, width="stretch")
        st.caption("Mean ± sd of the wells in each group. Day 1 is the baseline image, taken before the drugs went on.")

# ---------------------------------------------------------------- dose-response
with tab_dose:
    lum = df[df["metric"] == "luminescence"]
    if lum.empty:
        st.info("The plate reader end point has not been uploaded yet (it runs on the last day).")
    else:
        c1, c2 = st.columns([1, 2])
        day = c1.selectbox("Day", sorted(lum["day"].unique()), key="dose_day")
        per_row = c2.checkbox("Normalise with the controls in the same row (corrects edge effects)", value=False)
        norm = analysis.normalise(lum[lum["day"] == day], per_row=per_row)
        fits = analysis.fit_all(norm)
        show_truth = False
        if sim_truth is not None:
            show_truth = st.checkbox("Compare with the simulation's hidden true IC50", value=True)
            if show_truth:
                fits["true_ic50"] = fits["drug"].map(lambda d: sim_truth(d)[0])
                fits["fit / true"] = fits["ic50"] / fits["true_ic50"]
        drug = st.selectbox("Drug", fits["drug"].tolist(), key="dose_drug")
        g = norm[norm["drug"] == drug]
        row = fits[fits["drug"] == drug].iloc[0]
        x = np.logspace(np.log10(g["conc_um"].min()) - 0.2, np.log10(g["conc_um"].max()) + 0.2, 200)
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=g["conc_um"], y=g["viability"], mode="markers", name="wells", marker=dict(size=9)))
        if row["status"] == "ok":
            fig.add_trace(go.Scatter(x=x, y=analysis.hill(x, np.log10(row["ic50"]), row["slope"]), mode="lines", name="fit"))
            fig.add_vline(x=row["ic50"], line_dash="dash", annotation_text=f"IC50 {row['ic50']:.3g} µM")
        fig.add_hline(y=50, line_dash="dot", line_color="#999")
        fig.update_xaxes(type="log", title="concentration (µM)")
        fig.update_yaxes(title="viability (% of vehicle)", range=[-15, 130])
        fig.update_layout(height=400, margin=dict(l=0, r=0, t=30, b=0))
        left, right = st.columns([2, 1])
        left.plotly_chart(fig, width="stretch")
        labels = {"ok": "fitted", "inactive": "inactive in tested range", "no_fit": "fit failed"}
        right.metric("IC50 (µM)", f"{row['ic50']:.3g}" if row["status"] == "ok" else "-")
        right.caption(f"{labels[row['status']]}" + (f", Hill slope {row['slope']:.2f}, R² {row['r2']:.3f}" if row["status"] == "ok" else ""))
        st.subheader(f"All drugs on {plate}")
        shown = fits.assign(status=fits["status"].map(labels))
        cfg = {"ic50": st.column_config.NumberColumn("IC50 (µM)", format="%.3g"),
               "slope": st.column_config.NumberColumn("Hill slope", format="%.2f"),
               "r2": st.column_config.NumberColumn("R²", format="%.3f")}
        if show_truth:
            cfg["true_ic50"] = st.column_config.NumberColumn("true IC50 (µM)", format="%.3g")
            cfg["fit / true"] = st.column_config.NumberColumn("fit / true", format="%.2f")
        st.dataframe(shown, hide_index=True, column_config=cfg, width="stretch")

# ---------------------------------------------------------------- plate QC
with tab_qc:
    lum = df[df["metric"] == "luminescence"]
    if lum.empty:
        st.info("QC needs the plate reader end point.")
    else:
        q = analysis.plate_quality(lum[lum["day"] == lum["day"].max()])
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Z'", f"{q['z_prime']:.2f}")
        c2.metric("Signal / background", f"{q['signal_to_background']:.0f}")
        c3.metric("Vehicle CV", f"{q['vehicle_cv_pct']:.1f} %")
        c4.metric("Vehicle / kill mean (RLU)", f"{q['vehicle_mean']:.0f} / {q['kill_mean']:.0f}")
        if q["z_prime"] >= 0.5:
            st.success("Z' ≥ 0.5: the controls are well separated, the assay window is excellent.")
        elif q["z_prime"] >= 0:
            st.warning("0 ≤ Z' < 0.5: marginal. Check the controls before trusting weak hits.")
        else:
            st.error("Z' < 0: vehicle and kill controls overlap. This plate cannot separate hits from noise.")
        st.caption("Z' = 1 − 3 (sd vehicle + sd kill) / |mean vehicle − mean kill|, from the 16 vehicle and 16 kill wells.")

if live:
    time.sleep(5)
    st.rerun()
