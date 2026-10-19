"""The simulated biology: plate layout, hidden drug potencies, cell growth and readouts.

Layout of one 384-well plate (rows A-P = 16 drugs):
    col 01        blank (media only, also an edge buffer)
    col 02-11     drug dose series, replicate 1 (10 doses, 3-fold steps, 10 uM down to 0.5 nM)
    col 12-21     drug dose series, replicate 2
    col 22        vehicle (DMSO only)
    col 23        kill control (positive control, full effect)
    col 24        blank
= 320 drug wells + 16 vehicle + 16 kill + 32 blank.
"""
import math
import random
from dataclasses import dataclass
from datetime import datetime

ROWS = "ABCDEFGHIJKLMNOP"
DOSES_UM = [10 / 3 ** i for i in range(10)]  # 10, 3.33, 1.11 ... 0.0005 uM

# Biology constants
SEED_CELLS = 2000            # cells per well at seeding
CAPACITY = 40000             # cells per well at full confluence
GROWTH_PER_H = math.log(2) / 24
DRUG_TAU_H = 24.0            # the drug effect builds up with this time constant
MAX_KILL = 0.95              # even the kill control leaves 5% survivors
RLU_PER_CELL = 5.0           # luminescence per cell
RLU_BACKGROUND = 300.0


@dataclass(frozen=True)
class Well:
    well: str
    role: str                # drug | vehicle | kill | blank
    drug: str | None = None
    conc_um: float | None = None


def make_layout(plate_index: int) -> list[Well]:
    """plate_index starts at 1. Drug names are unique across plates: CMP-<plate><row>, e.g. CMP-1A."""
    wells: list[Well] = []
    for r, row in enumerate(ROWS):
        drug = f"CMP-{plate_index}{row}"
        wells.append(Well(f"{row}01", "blank"))
        for rep in range(2):
            for d, conc in enumerate(DOSES_UM):
                col = 2 + rep * 10 + d
                wells.append(Well(f"{row}{col:02d}", "drug", drug, round(conc, 6)))
        wells.append(Well(f"{row}22", "vehicle"))
        wells.append(Well(f"{row}23", "kill"))
        wells.append(Well(f"{row}24", "blank"))
    return sorted(wells, key=lambda w: (w.well[0], w.well[1:]))


def layout_payload(plate_index: int) -> list[dict]:
    """The wells list as the API's POST /plates wants it."""
    out = []
    for w in make_layout(plate_index):
        d = {"well": w.well, "role": w.role}
        if w.role == "drug":
            d.update(drug=w.drug, conc_um=w.conc_um)
        out.append(d)
    return out


def truth(drug: str) -> tuple[float, float]:
    """Hidden (IC50 in uM, Hill slope) of a drug. Deterministic, so every process agrees.

    IC50s are spread over 3 decades (0.003 to 3 uM). One drug in eight is inactive (IC50 1000 uM)."""
    rng = random.Random(f"truth:{drug}")
    if rng.random() < 0.125:
        return 1000.0, 1.0
    return 10 ** rng.uniform(-2.5, 0.5), rng.uniform(0.8, 1.8)


def drug_effect(conc_um: float, ic50: float, hill: float) -> float:
    """Fraction of the maximal effect (0..1) at a concentration: the Hill equation."""
    return conc_um ** hill / (conc_um ** hill + ic50 ** hill)


def _logistic(n0: float, r: float, dt_h: float) -> float:
    if n0 <= 0:
        return 0.0
    return CAPACITY / (1 + (CAPACITY / n0 - 1) * math.exp(-r * dt_h))


class PlateState:
    """Cell numbers per well over simulated time, computed in closed form (no time stepping)."""

    def __init__(self, barcode: str, plate_index: int, seed: int = 1, noise: bool = True):
        self.barcode = barcode
        self.wells = {w.well: w for w in make_layout(plate_index)}
        self.rng = random.Random(f"{seed}:{barcode}")
        self.noise = noise
        self.seeded_at: datetime | None = None
        self.dosed_at: datetime | None = None
        # per-well biological variability (5%) and a mild edge effect on the top and bottom rows
        self.rate = {
            k: GROWTH_PER_H * (0.95 if k[0] in "AP" else 1.0) * (self.rng.lognormvariate(0, 0.05) if noise else 1.0)
            for k in self.wells
        }

    def seed(self, t: datetime):
        self.seeded_at = t

    def add_drug(self, t: datetime):
        self.dosed_at = t

    def _effect(self, w: Well) -> float:
        if w.role == "kill":
            return 1.0
        if w.role == "drug":
            ic50, hill = truth(w.drug)
            return drug_effect(w.conc_um, ic50, hill)
        return 0.0

    def cells(self, well: str, t: datetime) -> float:
        """Cells in a well at time t: undisturbed growth times the fraction that survives the drug.

        Survival falls from 1 towards 1 - MAX_KILL * effect, with time constant DRUG_TAU_H. Because the
        drug scales survival (not the growth rate), the dose at which viability is half way between
        vehicle and kill control is the drug's hidden IC50, which is what the analysis should recover."""
        w = self.wells[well]
        if w.role == "blank" or self.seeded_at is None:
            return 0.0
        hours = max(0.0, (t - self.seeded_at).total_seconds() / 3600)
        n = _logistic(SEED_CELLS, self.rate[well], hours)
        if self.dosed_at and self.dosed_at < t:
            dt = (t - self.dosed_at).total_seconds() / 3600
            n *= 1 - MAX_KILL * self._effect(w) * (1 - math.exp(-dt / DRUG_TAU_H))
        return n

    def confluence(self, t: datetime) -> dict[str, float]:
        out = {}
        for well in self.wells:
            v = 100 * self.cells(well, t) / CAPACITY
            if self.noise:
                v += self.rng.gauss(0.2, 0.4) if self.wells[well].role == "blank" else self.rng.gauss(0, 1.5)
            out[well] = round(min(100.0, max(0.0, v)), 2)
        return out

    def luminescence(self, t: datetime) -> dict[str, float]:
        out = {}
        for well in self.wells:
            v = RLU_BACKGROUND + RLU_PER_CELL * self.cells(well, t)
            if self.noise:
                v = RLU_BACKGROUND * self.rng.lognormvariate(0, 0.05) + RLU_PER_CELL * self.cells(well, t) * self.rng.lognormvariate(0, 0.08)
            out[well] = round(v, 1)
        return out


def estimate_ic50(points: list[tuple[float, float]], vehicle: float, kill: float) -> float | None:
    """IC50 from (conc_um, signal) points: normalise to vehicle=100% / kill=0%, average replicates,
    then interpolate where the curve crosses 50% (log-linear). None if it never crosses."""
    span = vehicle - kill
    if span <= 0:
        return None
    by_conc: dict[float, list[float]] = {}
    for c, s in points:
        by_conc.setdefault(c, []).append((s - kill) / span)
    curve = sorted((c, sum(v) / len(v)) for c, v in by_conc.items())  # ascending concentration
    for (c1, v1), (c2, v2) in zip(curve, curve[1:]):
        if v1 >= 0.5 >= v2:
            frac = (v1 - 0.5) / (v1 - v2) if v1 != v2 else 0.5
            return 10 ** (math.log10(c1) + frac * (math.log10(c2) - math.log10(c1)))
    return None
