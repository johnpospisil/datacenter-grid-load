"""Structural (weather-adjusted) demand growth per BA (Prompt 9).

For every hour since 2016 the baseline GAM (fitted on 2016-2019, decision D4) predicts
what demand would have been with the baseline's relationship to weather and calendar.
Structural growth is actual minus that counterfactual. Aggregates use ratios of sums,
r = sum(actual) / sum(predicted), so `resid_pct` = 100 (r - 1) and the index is
100 r_t / r_2019. Only hours with clean demand and complete weather count.

Uncertainty (95% intervals) combines:
- jackknife over baseline years: the GAM refitted with each baseline year left out
  (DOM: each baseline month), i.e. how much the counterfactual depends on which years
  trained it;
- a moving-block bootstrap of daily residuals (14-day blocks) within each period.
The GBM and degree-day OLS give the model-family check (Prompt 8), reported alongside.

Growth since 2022 is reported two ways (decision D11): against the flat baseline
(headline) and beyond each BA's own 2016-2019 residual trend extended forward.

Series rules (DATA.md):
- FPL / SOCO: Gulf Power moved from SOCO to FPL in EIA-930 in 2022-07, so their growth
  is measured from 2023; the combined FPL+SOCO series is boundary-consistent from 2022.
- AZPS: EIA-930 runs 8-22% above FERC 714 from 2025-09 (unresolved), so the headline
  window ends 2025-08; the full window is reported as a sensitivity (AZPS_full).
- AZPS / SRP: from 2025 the split of Phoenix load between the two BAs swings (SRP falls
  as AZPS rises), so the combined AZPS+SRP series (to 2025-08) is the Phoenix headline.

Run: python -m glg.models.growth
"""

import logging
from dataclasses import dataclass
from datetime import date

import numpy as np
import polars as pl
import statsmodels.api as sm

from glg.build import features as feat
from glg.config import OUTPUTS_DIR, REFERENCE_DIR
from glg.models.common import SHORT_BASELINE, TARGET, baseline_rows, fold_ids, load_table
from glg.models.normalize_ddr import DdrModel
from glg.models.normalize_gam import GamModel
from glg.models.normalize_gbm import GbmModel

log = logging.getLogger(__name__)

HOURLY_PATH = OUTPUTS_DIR / "counterfactual_hourly.parquet"
MONTHLY_PATH = OUTPUTS_DIR / "structural_growth_monthly.parquet"
ANNUAL_PATH = OUTPUTS_DIR / "structural_growth_annual.csv"
RANKING_PATH = OUTPUTS_DIR / "growth_ranking.csv"

FIRST_YEAR = 2016
INDEX_YEAR = 2019
SINCE_YEAR = 2022
MIN_MONTH_HOURS = 24 * 20  # a month counts with at least 20 days of data
BLOCK_DAYS = 14
N_BOOT = 500
SEED = 42
Z95 = 1.959964
HOURS_PER_YEAR = 8766.0
ROLL_MIN_SHARE = 0.8  # a trailing-12-month window needs 80% of its hours
FEATURES = sorted(set(GamModel.features) | set(GbmModel.features) | set(DdrModel.features))

# Left out of headline comparisons (decision D10).
NOT_HEADLINE = {"DOPD", "PSCO", "LGEE"}


@dataclass(frozen=True)
class Rule:
    start_year: int = SINCE_YEAR
    last_month: date | None = None  # None = latest complete month
    note: str = ""


GULF = "Gulf Power moved from SOCO to FPL in EIA-930 in 2022-07 (~1.3-1.5 GW)"
RULES = {
    "FPL": Rule(start_year=2023, note=f"{GULF}; growth measured from 2023"),
    "SOCO": Rule(start_year=2023, note=f"{GULF}; growth measured from 2023"),
    "FPL+SOCO": Rule(note="FPL and SOCO combined: unaffected by the Gulf Power move"),
    "AZPS": Rule(
        last_month=date(2025, 8, 1),
        note="EIA-930 8-22% above FERC 714 from 2025-09 (unresolved): window ends 2025-08",
    ),
    "AZPS_full": Rule(note="AZPS incl. the disputed months from 2025-09 (sensitivity only)"),
    "SRP": Rule(
        note="SRP falls from 2026-04 as AZPS rises: likely load/reporting moving between "
        "the Phoenix BAs; see AZPS+SRP"
    ),
    "AZPS+SRP": Rule(
        last_month=date(2025, 8, 1),
        note="Phoenix BAs combined (robust to load moving between them); window ends 2025-08 "
        "as for AZPS",
    ),
}
COMBINED = {"FPL+SOCO": ["FPL", "SOCO"], "AZPS+SRP": ["AZPS", "SRP"]}
ALIASES = {"AZPS_full": "AZPS"}


# --------------------------------------------------------------------------- predictions


def predict_ba(ba: str, table: pl.DataFrame) -> tuple[pl.DataFrame, np.ndarray, list]:
    """Hourly actual and counterfactuals since 2016, plus jackknife counterfactuals.

    Returns (hourly frame, jackknife matrix [hours x folds], fold labels).
    """
    base = baseline_rows(table, FEATURES)
    # Select by the hour's start, the same convention used to group (the hour ending
    # 2016-01-01 00:00 belongs to 2015), so hourly rows and jackknife rows always align.
    start = pl.col("period_end_utc") - pl.duration(hours=1)
    rows = table.filter(start.dt.year() >= FIRST_YEAR).drop_nulls([TARGET, *FEATURES])
    out = rows.select(
        "ba", "period_end_utc", "in_baseline", "is_covid", pl.col(TARGET).alias("actual")
    ).with_columns(
        pl.Series("pred_gam", GamModel().fit(base).predict(rows)),
        pl.Series("pred_gbm", GbmModel().fit(base).predict(rows)),
        pl.Series("pred_ddr", DdrModel().fit(base).predict(rows)),
    )
    folds = fold_ids(base, ba)
    labels = list(np.unique(folds))
    jk = np.column_stack(
        [GamModel().fit(base.filter(pl.Series(folds != k))).predict(rows) for k in labels]
    )
    log.info("%s: %d hours, %d jackknife fits", ba, rows.height, len(labels))
    return out, jk, labels


# --------------------------------------------------------------------------- aggregation


def _ratio(a: np.ndarray, p: np.ndarray) -> float:
    return float(a.sum() / p.sum())


def jackknife_se(values: np.ndarray) -> float:
    """Jackknife standard error from leave-one-group-out estimates."""
    n = len(values)
    return float(np.sqrt((n - 1) / n * np.sum((values - values.mean()) ** 2)))


def block_bootstrap(a_day: np.ndarray, p_day: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Bootstrap replicates of sum(a)/sum(p) from daily sums, moving blocks of BLOCK_DAYS."""
    n = len(a_day)
    if n < 2:
        return np.full(N_BOOT, np.nan)
    b = min(BLOCK_DAYS, n)
    starts = rng.integers(0, n - b + 1, size=(N_BOOT, int(np.ceil(n / b))))
    idx = (starts[:, :, None] + np.arange(b)).reshape(N_BOOT, -1)[:, :n]
    return a_day[idx].sum(1) / p_day[idx].sum(1)


@dataclass
class Series:
    """Hourly actual and counterfactuals for one BA (or combination), ready to aggregate."""

    name: str
    hourly: pl.DataFrame  # period_end_utc, actual, pred_gam, pred_gbm, pred_ddr, in_baseline
    jk: np.ndarray

    def frame(self) -> pl.DataFrame:
        t = pl.col("period_end_utc") - pl.duration(hours=1)  # hour's start, for grouping
        return self.hourly.with_columns(
            t.dt.truncate("1mo").dt.date().alias("month"),
            t.dt.year().alias("year"),
            t.dt.date().alias("day"),
        )


def combine(name: str, parts: list[tuple[pl.DataFrame, np.ndarray, list]]) -> Series:
    """Sum several BAs hour by hour (hours present in all of them; same jackknife folds)."""
    keys = None
    for h, _, _ in parts:
        k = set(h["period_end_utc"].to_list())
        keys = k if keys is None else keys & k
    hs, jks = [], []
    for h, jk, _ in parts:
        sel = h["period_end_utc"].is_in(list(keys)).to_numpy()
        hs.append(h.filter(pl.Series(sel)).sort("period_end_utc"))
        order = np.argsort(h.filter(pl.Series(sel))["period_end_utc"].to_numpy(), kind="stable")
        jks.append(jk[sel][order])
    total = (
        hs[0]
        .select("period_end_utc", "in_baseline", "is_covid")
        .with_columns(
            *(
                pl.Series(c, sum(h[c].to_numpy() for h in hs))
                for c in ["actual", "pred_gam", "pred_gbm", "pred_ddr"]
            )
        )
    )
    return Series(name, total, sum(jks))


def aggregate(s: Series, by: str, rng: np.random.Generator) -> pl.DataFrame:
    """Per period (month or year): MW, % vs counterfactual with 95% interval, model family."""
    f = s.frame()
    a, jk = f["actual"].to_numpy(), s.jk
    rows = []
    for key, g in f.with_row_index("_i").group_by(by, maintain_order=True):
        i = g["_i"].to_numpy()
        pa, pg = a[i], g["pred_gam"].to_numpy()
        r = _ratio(pa, pg)
        r_jk = np.array([_ratio(pa, jk[i, k]) for k in range(jk.shape[1])])
        day = g.group_by("day").agg(pl.col("actual").sum(), pl.col("pred_gam").sum())
        r_bs = block_bootstrap(day["actual"].to_numpy(), day["pred_gam"].to_numpy(), rng)
        se_jk, se_bs = 100 * jackknife_se(r_jk), 100 * float(np.nanstd(r_bs, ddof=1))
        rows.append(
            {
                "ba": s.name,
                by: key[0],
                "n_hours": len(i),
                "actual_mw": float(pa.mean()),
                "pred_mw": float(pg.mean()),
                "resid_mw": float((pa - pg).mean()),
                "resid_twh_per_yr": float((pa - pg).mean() * HOURS_PER_YEAR / 1e6),
                "resid_pct": 100 * (r - 1),
                "resid_pct_se": float(np.hypot(se_jk, se_bs)),
                "_se_bs": se_bs,
                "resid_pct_gbm": 100 * (_ratio(pa, g["pred_gbm"].to_numpy()) - 1),
                "resid_pct_ddr": 100 * (_ratio(pa, g["pred_ddr"].to_numpy()) - 1),
                "covid_share": float(g["is_covid"].mean()),
                "_r_jk": r_jk.tolist(),
            }
        )
    df = pl.DataFrame(rows).with_columns(
        (pl.col("resid_pct") - Z95 * pl.col("resid_pct_se")).alias("resid_pct_lo"),
        (pl.col("resid_pct") + Z95 * pl.col("resid_pct_se")).alias("resid_pct_hi"),
    )
    return add_index(df, by)


def add_index(df: pl.DataFrame, by: str) -> pl.DataFrame:
    """Weather-adjusted demand index, INDEX_YEAR = 100, with a 95% interval.

    Each jackknife fit is normalised by its own INDEX_YEAR, so the index interval leaves out
    the level uncertainty shared by every period; the bootstrap part is the period's own.
    """
    year = pl.col(by).dt.year() if by == "month" else pl.col(by)
    ref = df.filter(year == INDEX_YEAR)
    if ref.is_empty():
        return df
    w = ref["n_hours"].to_numpy()
    r_ref = float(np.average(1 + ref["resid_pct"].to_numpy() / 100, weights=w))
    jk_ref = np.average(np.array(ref["_r_jk"].to_list()), axis=0, weights=w)
    r = 1 + df["resid_pct"].to_numpy() / 100
    idx = 100 * r / r_ref
    se_jk = np.array([jackknife_se(row) for row in 100 * np.array(df["_r_jk"].to_list()) / jk_ref])
    se = np.hypot(se_jk, idx * df["_se_bs"].to_numpy() / 100 / r)
    return df.with_columns(
        pl.Series("index_2019", idx),
        pl.Series("index_lo", idx - Z95 * se),
        pl.Series("index_hi", idx + Z95 * se),
    )


# --------------------------------------------------------------------------- growth since 2022


def _decimal(m: pl.Series) -> np.ndarray:
    return (m.dt.year() + (m.dt.month() - 0.5) / 12).to_numpy()


def slope(monthly: pl.DataFrame) -> tuple[float, float, float]:
    """OLS slope (%/yr) of monthly resid_pct, HAC SE, and intercept."""
    m = monthly.filter(pl.col("n_hours") >= MIN_MONTH_HOURS)
    t = _decimal(m["month"])
    fit = sm.OLS(m["resid_pct"].to_numpy(), sm.add_constant(t)).fit(
        cov_type="HAC", cov_kwds={"maxlags": 3}
    )
    return float(fit.params[1]), float(fit.bse[1]), float(fit.params[0])


def window(f: pl.DataFrame, start: date, end_excl: date) -> pl.DataFrame:
    return f.filter(pl.col("month") >= start, pl.col("month") < end_excl)


def growth_since(s: Series, monthly: pl.DataFrame, rule: Rule, rng: np.random.Generator) -> dict:
    f = s.frame().with_row_index("_i")
    complete = monthly.filter(pl.col("n_hours") >= MIN_MONTH_HOURS)["month"]
    last = rule.last_month or complete.max()
    end_excl = date(last.year + (last.month == 12), last.month % 12 + 1, 1)
    last12_start = date(end_excl.year - 1, end_excl.month, 1)
    start = date(rule.start_year, 1, 1)
    w0 = window(f, start, date(rule.start_year + 1, 1, 1))
    w1 = window(f, last12_start, end_excl)
    a = f["actual"].to_numpy()

    def g(w, pred):
        return _ratio(a[w["_i"].to_numpy()], pred[w["_i"].to_numpy()])

    def growth(pred):
        return 100 * (g(w1, pred) / g(w0, pred) - 1)

    pg = f["pred_gam"].to_numpy()
    est = growth(pg)
    jk = np.array([growth(s.jk[:, k]) for k in range(s.jk.shape[1])])
    boot = []
    for w in (w0, w1):
        d = w.group_by("day").agg(pl.col("actual").sum(), pl.col("pred_gam").sum())
        boot.append(block_bootstrap(d["actual"].to_numpy(), d["pred_gam"].to_numpy(), rng))
    bs = 100 * (boot[1] / boot[0] - 1)
    se = float(np.sqrt(jackknife_se(jk) ** 2 + np.nanvar(bs, ddof=1)))

    # D11: the baseline's own residual trend, extended forward.
    base = monthly.filter(pl.col("month").dt.year().is_between(FIRST_YEAR, INDEX_YEAR))
    base = base.filter(pl.col("covid_share") == 0)
    b, b_se, b0 = slope(base)
    t0 = rule.start_year + 0.5
    t1 = last12_start.year + (last12_start.month - 1) / 12 + 0.5
    expected = 100 * ((1 + (b0 + b * t1) / 100) / (1 + (b0 + b * t0) / 100) - 1)
    beyond = est - expected
    beyond_se = float(np.sqrt(se**2 + ((t1 - t0) * b_se) ** 2))
    pace, pace_se, _ = slope(window(monthly, start, end_excl))

    mw0 = (a[w0["_i"].to_numpy()] - pg[w0["_i"].to_numpy()]).mean()
    mw1 = (a[w1["_i"].to_numpy()] - pg[w1["_i"].to_numpy()]).mean()
    return {
        "ba": s.name,
        "from_year": rule.start_year,
        "last12_from": last12_start,
        "last12_to": last,
        "growth_pct": est,
        "growth_lo": est - Z95 * se,
        "growth_hi": est + Z95 * se,
        "growth_gbm_pct": growth(f["pred_gbm"].to_numpy()),
        "growth_ddr_pct": growth(f["pred_ddr"].to_numpy()),
        "mw_added": float(mw1 - mw0),
        "twh_per_yr_added": float((mw1 - mw0) * HOURS_PER_YEAR / 1e6),
        "baseline_trend_pct_yr": b,
        "baseline_trend_se": b_se,
        "trend_expected_pct": expected,
        "beyond_trend_pct": beyond,
        "beyond_trend_lo": beyond - Z95 * beyond_se,
        "beyond_trend_hi": beyond + Z95 * beyond_se,
        "pace_pct_yr": pace,
        "pace_se": pace_se,
        "acceleration_pct_yr": pace - b,
        "note": rule.note,
    }


# --------------------------------------------------------------------------- main


def _months_back(m: date, n: int) -> list[date]:
    return [date(m.year + (m.month - 1 - k) // 12, (m.month - 1 - k) % 12 + 1, 1) for k in range(n)]


def rolling_index(s: Series, monthly: pl.DataFrame) -> pl.DataFrame:
    """Trailing 12-month weather-adjusted index (INDEX_YEAR = 100) with a jackknife band.

    A window counts when at least ROLL_MIN_SHARE of its hours have data. The reference is
    all of calendar INDEX_YEAR, the same base as the annual index.
    """
    f = s.frame()
    a, p, months = f["actual"].to_numpy(), f["pred_gam"].to_numpy(), f["month"].to_numpy()
    years = f["year"].to_numpy()
    present = sorted(set(monthly["month"].to_list()))
    vals = {}
    for m in present:
        need = _months_back(m, 12)
        sel = np.isin(months, np.array(need, dtype="datetime64[D]"))
        if sel.sum() >= ROLL_MIN_SHARE * HOURS_PER_YEAR:
            vals[m] = (a[sel].sum() / p[sel].sum(), a[sel].sum() / s.jk[sel].sum(0))
    ref_sel = years == INDEX_YEAR
    if ref_sel.sum() < ROLL_MIN_SHARE * HOURS_PER_YEAR:
        return pl.DataFrame(schema={"ba": pl.Utf8, "month": pl.Date})
    ref = (a[ref_sel].sum() / p[ref_sel].sum(), a[ref_sel].sum() / s.jk[ref_sel].sum(0))
    rows = []
    for m, (r, rjk) in vals.items():
        idx, se = 100 * r / ref[0], jackknife_se(100 * rjk / ref[1])
        rows.append(
            {
                "ba": s.name,
                "month": m,
                "index_12m": idx,
                "index_12m_lo": idx - Z95 * se,
                "index_12m_hi": idx + Z95 * se,
            }
        )
    return pl.DataFrame(rows)


def run(bas: list[str] | None = None) -> pl.DataFrame:
    bas = bas or feat.modelled_bas()
    table = load_table(bas)
    rng = np.random.default_rng(SEED)
    preds = {}
    for ba in bas:
        preds[ba] = predict_ba(ba, table.filter(pl.col("ba") == ba))

    series = [Series(ba, h, jk) for ba, (h, jk, _) in preds.items()]
    for name, parts in COMBINED.items():
        if all(p in preds for p in parts):
            series.append(combine(name, [preds[p] for p in parts]))
    for alias, src in ALIASES.items():
        if src in preds:
            series.append(Series(alias, preds[src][0], preds[src][1]))

    monthly, annual, ranking = [], [], []
    for s in series:
        m = aggregate(s, "month", rng)
        y = aggregate(s, "year", rng)
        roll = rolling_index(s, m)
        m = m.drop("_r_jk", "_se_bs")
        monthly.append(m.join(roll, on=["ba", "month"], how="left") if roll.height else m)
        annual.append(y.drop("_r_jk", "_se_bs"))
        ranking.append(growth_since(s, m, RULES.get(s.name, Rule()), rng))
        log.info("%s aggregated", s.name)

    hourly = pl.concat([h.drop("in_baseline", "is_covid") for h, _, _ in preds.values()])
    monthly_df = pl.concat(monthly, how="diagonal_relaxed")
    annual_df = pl.concat(annual, how="diagonal_relaxed").with_columns(
        (pl.col("n_hours") < 8000).alias("partial_year")
    )
    shortlist = pl.read_csv(REFERENCE_DIR / "ba_shortlist.csv").select(
        pl.col("series").alias("ba"), "role"
    )
    ranking_df = (
        pl.DataFrame(ranking)
        .join(shortlist, on="ba", how="left")
        .with_columns(
            pl.when(pl.col("ba").is_in(list(COMBINED)))
            .then(pl.lit("focus"))
            .when(pl.col("ba") == "AZPS_full")
            .then(pl.lit("sensitivity"))
            .otherwise(pl.col("role"))
            .alias("role"),
            (~pl.col("ba").is_in([*NOT_HEADLINE, "AZPS_full"])).alias("headline"),
            pl.col("ba").is_in(SHORT_BASELINE).alias("short_baseline"),
        )
        .sort("growth_pct", descending=True)
    )
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    hourly.write_parquet(HOURLY_PATH, compression="zstd")
    monthly_df.write_parquet(MONTHLY_PATH, compression="zstd")
    annual_df.write_csv(ANNUAL_PATH, float_precision=4)
    ranking_df.write_csv(RANKING_PATH, float_precision=3)
    log.info(
        "Wrote %s, %s, %s, %s",
        HOURLY_PATH.name,
        MONTHLY_PATH.name,
        ANNUAL_PATH.name,
        RANKING_PATH.name,
    )
    with pl.Config(tbl_rows=40, tbl_cols=12, tbl_width_chars=220):
        print(
            ranking_df.select(
                "ba",
                "role",
                "headline",
                "from_year",
                "growth_pct",
                "growth_lo",
                "growth_hi",
                "mw_added",
                "baseline_trend_pct_yr",
                "beyond_trend_pct",
                "growth_gbm_pct",
            ).with_columns(pl.col(pl.Float64).round(2))
        )
    return ranking_df


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()


if __name__ == "__main__":
    main()
