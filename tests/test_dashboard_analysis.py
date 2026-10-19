from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("scipy")
from dashboard import analysis  # noqa: E402
from screening.biology import PlateState, make_layout, truth  # noqa: E402

T0 = datetime(2026, 10, 19, 8, tzinfo=timezone.utc)


def _plate_df(noise: bool, seed: int = 1) -> pd.DataFrame:
    p = PlateState("X", 1, seed=seed, noise=noise)
    p.seed(T0); p.add_drug(T0 + timedelta(hours=24))
    lum = p.luminescence(T0 + timedelta(hours=96))
    rows = [{"well": w.well, "role": w.role, "drug": w.drug, "conc_um": w.conc_um, "day": 4,
             "metric": "luminescence", "value": lum[w.well]} for w in make_layout(1)]
    return pd.DataFrame(rows)


def test_normalise_puts_controls_at_100_and_0():
    n = analysis.normalise(_plate_df(noise=False))
    assert n.loc[n["role"] == "vehicle", "viability"].mean() == pytest.approx(100)
    assert n.loc[n["role"] == "kill", "viability"].mean() == pytest.approx(0)


def test_fit_recovers_hidden_ic50_without_noise():
    fits = analysis.fit_all(analysis.normalise(_plate_df(noise=False), per_row=True)).set_index("drug")
    ok = fits[fits["status"] == "ok"]
    assert len(ok) >= 13
    for drug, row in ok.iterrows():
        assert row["ic50"] == pytest.approx(truth(drug)[0], rel=0.03), drug
    assert (fits["status"] == "inactive").sum() >= 1 and all(truth(d)[0] > 100 for d in fits[fits["status"] == "inactive"].index)


def test_fit_with_noise_is_close_enough():
    fits = analysis.fit_all(analysis.normalise(_plate_df(noise=True))).set_index("drug")
    ok = fits[fits["status"] == "ok"]
    ratio = np.array([row["ic50"] / truth(d)[0] for d, row in ok.iterrows()])
    assert (np.abs(np.log10(ratio)) < np.log10(2)).mean() > 0.85  # most within 2x


def test_plate_quality_flags_good_and_overlapping_controls():
    q = analysis.plate_quality(_plate_df(noise=True))
    assert q["z_prime"] > 0.5 and q["signal_to_background"] > 100
    bad = _plate_df(noise=True)
    bad.loc[bad["role"] == "kill", "value"] = bad.loc[bad["role"] == "vehicle", "value"].mean() * 0.9
    assert analysis.plate_quality(bad)["z_prime"] < 0


def test_grid_and_growth_curves_shapes():
    df = _plate_df(noise=False)
    g = analysis.grid(df)
    assert g.shape == (16, 24) and not g.isna().any().any()
    df["metric"], gc = "confluence_pct", analysis.growth_curves(df, "CMP-1A", [10.0])
    assert set(gc["series"]) == {"vehicle", "kill control", "blank", "CMP-1A 10 µM"}


def test_inactive_drug_is_not_given_an_ic50():
    conc = np.array([10, 3.3, 1.1, 0.37, 0.12])
    r = analysis.fit_drug(conc, np.array([99, 101, 100, 98, 100]))
    assert r["status"] == "inactive" and np.isnan(r["ic50"])


def test_per_row_normalisation_corrects_the_edge_effect():
    df = _plate_df(noise=False)
    def err(per_row):
        fits = analysis.fit_all(analysis.normalise(df, per_row=per_row)).set_index("drug")
        edge = [d for d in ("CMP-1A", "CMP-1B", "CMP-1P") if fits.loc[d, "status"] == "ok"]
        return max(abs(fits.loc[d, "ic50"] / truth(d)[0] - 1) for d in edge)
    assert err(per_row=True) < 0.03 < err(per_row=False)
