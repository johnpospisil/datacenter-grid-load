"""Robustness checks for the headline growth estimates (Prompt 15).

Every specification measures the same thing as Prompt 9: weather-adjusted growth from the
start year (2022; FPL/SOCO 2023) to the latest 12 months (AZPS and AZPS+SRP: to 2025-08),
as the ratio of ratios of actual to counterfactual demand. Specifications:

- gam_2016_19   headline: GAM fitted on 2016-2019 (DOM: 2018-09 to 2019-12)
- gbm, ddr      model family: LightGBM and degree-day OLS, same baseline (Prompts 7-8)
- gam_2017_19   shorter baseline (drops 2016; DOM unchanged)
- gam_start_yr  GAM fitted on the start year itself (2022 or 2023): growth relative to
                that year's own weather-demand relationship, no pre-COVID baseline
- gam_emp       GAM + a linear term in the BA's state-employment index (decision: kept
                out of the headline because it extrapolates the baseline trend; here as
                the 'with economic control' check)
- gam_phx_warm  Phoenix only: ERA5 temperature +0.3 C from 2023 (DATA.md "Phoenix drift")

Set-level checks (medians of the data-center vs comparison regions) are computed for each
specification, with and without BAs that had boundary or reporting changes.

Outputs: data/outputs/robustness_grid.csv (series x spec), robustness_medians.csv.

Run: python -m glg.models.robustness
"""

import logging
from datetime import date

import numpy as np
import polars as pl
from pygam import l

from glg.config import OUTPUTS_DIR
from glg.models import growth as g
from glg.models.common import TARGET, load_table
from glg.models.normalize_gam import GamModel

log = logging.getLogger(__name__)

GRID_PATH = OUTPUTS_DIR / "robustness_grid.csv"
MEDIANS_PATH = OUTPUTS_DIR / "robustness_medians.csv"

DC = ["DOM", "ERCO", "AZPS+SRP", "NEVP", "GCPD", "BPAT"]
COMP = ["MISO", "NYIS", "ISNE", "FPC", "CPLE", "CISO", "PACE", "FPL"]
BOUNDARY = {"AZPS+SRP", "FPL"}  # load moved between BAs (Phoenix split; Gulf Power)
PHOENIX = {"AZPS", "SRP"}
PHX_SHIFT_C = 0.3
PHX_SHIFT_FROM = date(2023, 1, 1)
SPECS = ["gam_2016_19", "gbm", "ddr", "gam_2017_19", "gam_start_yr", "gam_emp", "gam_phx_warm"]


class EmpGam(GamModel):
    """The headline GAM plus a linear employment term (robustness only, never headline)."""

    features = [*GamModel.features, "emp_index"]

    @staticmethod
    def _x(df: pl.DataFrame) -> np.ndarray:
        return np.column_stack([GamModel._x(df), df["emp_index"].to_numpy()]).astype(float)

    @staticmethod
    def _terms():
        return GamModel._terms() + l(6)


def _hour_start_year() -> pl.Expr:
    return (pl.col("period_end_utc") - pl.duration(hours=1)).dt.year()


def refit(ba: str, table: pl.DataFrame, start_year: int) -> pl.DataFrame:
    """Counterfactuals for one BA under the refitted specifications."""
    t = table.with_columns(pl.col("emp_index").forward_fill().backward_fill())
    feats = EmpGam.features
    rows = t.filter(_hour_start_year() >= g.FIRST_YEAR).drop_nulls([TARGET, *feats])
    base = rows.filter(pl.col("in_baseline"))
    out = rows.select("ba", "period_end_utc")
    preds = {
        "gam_2017_19": GamModel().fit(base.filter(_hour_start_year() >= 2017)).predict(rows),
        "gam_start_yr": GamModel().fit(rows.filter(_hour_start_year() == start_year)).predict(rows),
        "gam_emp": EmpGam().fit(base).predict(rows),
    }
    if ba in PHOENIX:
        warm = pl.col("period_end_utc") >= pl.datetime(
            *PHX_SHIFT_FROM.timetuple()[:3], time_zone="UTC"
        )
        shifted = rows.with_columns(
            pl.when(warm).then(pl.col(c) + PHX_SHIFT_C).otherwise(pl.col(c)).alias(c)
            for c in ["t2m_c", "t2m_c_mean72"]
        )
        preds["gam_phx_warm"] = GamModel().fit(base).predict(shifted)
    log.info("%s refitted", ba)
    return out.with_columns(pl.Series(k, v) for k, v in preds.items())


def hourly_specs(bas: list[str]) -> pl.DataFrame:
    """Actual plus every specification's counterfactual, per BA-hour."""
    cf = pl.read_parquet(g.HOURLY_PATH).rename(
        {"pred_gam": "gam_2016_19", "pred_gbm": "gbm", "pred_ddr": "ddr"}
    )
    table = load_table(bas)
    parts = []
    for ba in bas:
        start = g.RULES.get(ba, g.Rule()).start_year
        r = refit(ba, table.filter(pl.col("ba") == ba), start)
        parts.append(cf.filter(pl.col("ba") == ba).join(r, on=["ba", "period_end_utc"]))
    h = pl.concat(parts, how="diagonal_relaxed")
    # specs that don't apply to a BA fall back to the headline counterfactual
    return h.with_columns(pl.col("gam_phx_warm").fill_null(pl.col("gam_2016_19")))


def series_frames(h: pl.DataFrame, names: list[str]) -> dict[str, pl.DataFrame]:
    out = {}
    for name in names:
        parts = g.COMBINED.get(name, [g.ALIASES.get(name, name)])
        frames = [h.filter(pl.col("ba") == p).drop("ba") for p in parts]
        if any(f.is_empty() for f in frames):
            continue
        f = frames[0]
        for other in frames[1:]:
            f = f.join(other, on="period_end_utc", suffix="_r")
            for c in ["actual", *SPECS]:
                f = f.with_columns((pl.col(c) + pl.col(f"{c}_r")).alias(c)).drop(f"{c}_r")
        out[name] = f.with_columns(
            (pl.col("period_end_utc") - pl.duration(hours=1))
            .dt.truncate("1mo")
            .dt.date()
            .alias("month")
        )
    return out


def growth_by_spec(f: pl.DataFrame, rank_row: dict) -> dict:
    y0 = rank_row["from_year"]
    l0 = date.fromisoformat(rank_row["last12_from"])
    l1 = date.fromisoformat(rank_row["last12_to"])
    w0 = f.filter(pl.col("month").dt.year() == y0)
    w1 = f.filter((pl.col("month") >= l0) & (pl.col("month") <= l1))
    out = {"ba": rank_row["ba"]}
    for s in SPECS:
        r0 = w0["actual"].sum() / w0[s].sum()
        r1 = w1["actual"].sum() / w1[s].sum()
        out[s] = 100 * (r1 / r0 - 1)
    return out


# Pre-registered roles from Prompt 2 (data/reference/ba_shortlist.csv, set before any
# weather-adjusted result): every headline 'focus' series vs every headline 'comparison'.
FOCUS_ALL = ["PJM", "DOM", "ERCO", "SOCO", "AZPS+SRP", "DUK", "TVA", "NEVP", "GCPD", "BPAT"]
COMP_ALL = ["MISO", "CISO", "NYIS", "ISNE", "FPL", "FPC", "CPLE", "SWPP", "PACE"]
GROUPINGS = {
    "dc6_vs_d10": (DC, COMP),
    "prompt2_roles": (FOCUS_ALL, COMP_ALL),
}


def medians(grid: pl.DataFrame) -> pl.DataFrame:
    rows = []
    for grouping, (dc_set, comp_set) in GROUPINGS.items():
        for exclude in (False, True):
            dc = [b for b in dc_set if not (exclude and b in BOUNDARY)]
            comp = [b for b in comp_set if not (exclude and b in BOUNDARY)]
            for s in SPECS:
                d = grid.filter(pl.col("ba").is_in(dc))[s].median()
                c = grid.filter(pl.col("ba").is_in(comp))[s].median()
                rows.append(
                    {
                        "grouping": grouping,
                        "spec": s,
                        "excl_boundary_bas": exclude,
                        "dc_median_pct": d,
                        "comp_median_pct": c,
                        "gap_pts": d - c,
                        "ratio": d / c if c else None,
                    }
                )
    return pl.DataFrame(rows)


def run() -> tuple[pl.DataFrame, pl.DataFrame]:
    from glg.build.features import modelled_bas

    rank = pl.read_csv(g.RANKING_PATH)
    h = hourly_specs(modelled_bas())
    frames = series_frames(h, rank["ba"].to_list())
    grid = pl.DataFrame(
        [
            growth_by_spec(frames[r["ba"]], r)
            for r in rank.iter_rows(named=True)
            if r["ba"] in frames
        ]
    )
    grid = grid.with_columns(
        pl.min_horizontal(SPECS).alias("min_pct"),
        pl.max_horizontal(SPECS).alias("max_pct"),
    ).join(rank.select("ba", "headline", "role"), on="ba", how="left")
    med = medians(grid)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    grid.write_csv(GRID_PATH, float_precision=3)
    med.write_csv(MEDIANS_PATH, float_precision=3)
    with pl.Config(tbl_rows=40, tbl_cols=14, tbl_width_chars=220):
        print(grid.sort("gam_2016_19", descending=True).with_columns(pl.col(pl.Float64).round(1)))
        print(med.with_columns(pl.col(pl.Float64).round(2)))
    return grid, med


def remedian() -> pl.DataFrame:
    """Recompute the medians from the saved grid (no refits)."""
    med = medians(pl.read_csv(GRID_PATH))
    med.write_csv(MEDIANS_PATH, float_precision=3)
    return med


def main() -> None:
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if "--medians-only" in sys.argv:
        print(remedian())
    else:
        run()


if __name__ == "__main__":
    main()
