"""Screening analysis: normalisation, dose-response fits and plate quality. Plain functions on DataFrames.

Columns expected (what GET /plates/{barcode}/readings returns): well, role, drug, conc_um, day, metric, value.
"""
import numpy as np
import pandas as pd
from scipy.optimize import curve_fit

INACTIVE_ABOVE = 70.0  # a drug whose strongest dose still leaves >70% viability is called inactive


def add_row_col(df: pd.DataFrame) -> pd.DataFrame:
    """Adds row (A..P) and col (1..24) from the well name."""
    out = df.copy()
    out["row"] = out["well"].str[0]
    out["col"] = out["well"].str[1:].astype(int)
    return out


def control_means(df: pd.DataFrame) -> tuple[float, float]:
    """(vehicle mean, kill mean) of a single metric on a single day."""
    return (df.loc[df["role"] == "vehicle", "value"].mean(), df.loc[df["role"] == "kill", "value"].mean())


def normalise(df: pd.DataFrame, per_row: bool = False) -> pd.DataFrame:
    """Adds percent viability: 100% = vehicle, 0% = kill control. Expects one metric, one day.

    per_row=False uses the plate-wide control means. per_row=True uses the vehicle and kill wells in the
    same row as each well, which corrects row-wise edge effects (each row holds one drug and its own
    controls) but is noisier because only one control well of each kind is averaged."""
    out = df.copy()
    if not per_row:
        veh, kill = control_means(df)
        out["viability"] = (out["value"] - kill) / (veh - kill) * 100
        return out
    out["row"] = out["well"].str[0]
    veh = out[out["role"] == "vehicle"].groupby("row")["value"].mean()
    kill = out[out["role"] == "kill"].groupby("row")["value"].mean()
    v, k = out["row"].map(veh), out["row"].map(kill)
    out["viability"] = (out["value"] - k) / (v - k) * 100
    return out


def hill(conc, log_ic50, slope):
    """Viability (%) at a concentration for a curve that falls from 100% to 0%."""
    return 100.0 / (1.0 + (np.asarray(conc, dtype=float) / 10 ** log_ic50) ** slope)


def fit_drug(conc: np.ndarray, viability: np.ndarray) -> dict:
    """Fit IC50 and Hill slope. Returns dict(ic50, slope, r2, status). status: ok | inactive | no_fit."""
    conc, viability = np.asarray(conc, float), np.asarray(viability, float)
    by_conc = pd.Series(viability).groupby(conc).mean()
    if by_conc.iloc[0:1].empty or by_conc.min() > INACTIVE_ABOVE:
        return {"ic50": np.nan, "slope": np.nan, "r2": np.nan, "status": "inactive"}
    try:
        (log_ic50, slope), _ = curve_fit(
            hill, conc, viability, p0=[np.log10(np.median(conc)), 1.0],
            bounds=([-6.0, 0.2], [4.0, 6.0]), maxfev=5000)
    except (RuntimeError, ValueError):
        return {"ic50": np.nan, "slope": np.nan, "r2": np.nan, "status": "no_fit"}
    resid = viability - hill(conc, log_ic50, slope)
    ss_tot = ((viability - viability.mean()) ** 2).sum()
    r2 = 1 - (resid ** 2).sum() / ss_tot if ss_tot > 0 else np.nan
    return {"ic50": 10 ** log_ic50, "slope": slope, "r2": r2, "status": "ok"}


def fit_all(df_norm: pd.DataFrame) -> pd.DataFrame:
    """One row per drug: ic50 (uM), slope, r2, status. Expects the output of normalise()."""
    rows = []
    for drug, g in df_norm[df_norm["role"] == "drug"].groupby("drug"):
        rows.append({"drug": drug, **fit_drug(g["conc_um"].to_numpy(), g["viability"].to_numpy())})
    return pd.DataFrame(rows, columns=["drug", "ic50", "slope", "r2", "status"])


def plate_quality(df: pd.DataFrame) -> dict:
    """Standard screening QC on a plate's end-point readout (one metric, one day).

    Z' = 1 - 3(sd_vehicle + sd_kill) / |mean_vehicle - mean_kill|. Above 0.5 is an excellent assay, 0 to 0.5
    marginal, below 0 the controls overlap."""
    veh = df.loc[df["role"] == "vehicle", "value"]
    kill = df.loc[df["role"] == "kill", "value"]
    blank = df.loc[df["role"] == "blank", "value"]
    gap = abs(veh.mean() - kill.mean())
    return {
        "z_prime": 1 - 3 * (veh.std() + kill.std()) / gap if gap else float("nan"),
        "signal_to_background": veh.mean() / blank.mean() if len(blank) and blank.mean() else float("nan"),
        "vehicle_cv_pct": veh.std() / veh.mean() * 100,
        "vehicle_mean": veh.mean(),
        "kill_mean": kill.mean(),
    }


def grid(df: pd.DataFrame, rows="ABCDEFGHIJKLMNOP", cols=range(1, 25)) -> pd.DataFrame:
    """Reshape one metric/day into the 16 x 24 plate picture (NaN where a well has no value)."""
    return add_row_col(df).pivot(index="row", columns="col", values="value").reindex(index=list(rows), columns=list(cols))


def growth_curves(df: pd.DataFrame, drug: str, doses: list[float]) -> pd.DataFrame:
    """Mean and sd per day for vehicle, kill, blank and the chosen doses of a drug (one metric)."""
    parts = []
    for label, sel in (("vehicle", df["role"] == "vehicle"), ("kill control", df["role"] == "kill"), ("blank", df["role"] == "blank")):
        parts.append(df[sel].assign(series=label))
    for d in doses:
        parts.append(df[(df["drug"] == drug) & (df["conc_um"].sub(d).abs() < 1e-9)].assign(series=f"{drug} {d:.3g} µM"))
    both = pd.concat(parts)
    return both.groupby(["series", "day"])["value"].agg(["mean", "std"]).reset_index()
