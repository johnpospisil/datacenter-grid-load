"""Validate the baseline (weather-normalisation) models per BA.

For each modelled BA and model:
- out-of-fold predictions over the baseline: leave one year out (2016-2019), or one month
  out for short baselines (DOM: EIA-930 starts 2018-09);
- metrics per held-out year and pooled: MAPE, "shape" MAPE (MAPE after removing that
  year's mean error), bias, R2, RMSE;
- residual trend: the in-sample monthly residual (% of demand) regressed on time. The
  models have no trend term, so a slope here is underlying growth inside the baseline,
  which Prompt 9 must compare against rather than call new;
- a status: fail if shape MAPE > 5% (the model can't capture the weather/calendar
  response), flag if pooled MAPE > 5% or |trend| > 1%/yr or the baseline is short.

Private check (decision D3): the same GAM on PJM metered DOM load 2016-2019, and how much a
model fitted only on EIA-930's short DOM window would shift the counterfactual. Written to
data/outputs/private/ and never published.

Run: python -m glg.models.validate
"""

import logging

import numpy as np
import polars as pl
import statsmodels.api as sm

from glg.build import features as feat
from glg.build import weather as w
from glg.config import OUTPUTS_DIR
from glg.ingest import pjm_load
from glg.models.common import SHORT_BASELINE, TARGET, baseline_rows, fold_ids, load_table, metrics
from glg.models.normalize_ddr import DdrModel
from glg.models.normalize_gam import GamModel
from glg.models.normalize_gbm import GbmModel

log = logging.getLogger(__name__)

MODELS = {"gam": GamModel, "gbm": GbmModel, "ddr": DdrModel}
METRICS_PATH = OUTPUTS_DIR / "validation_metrics.csv"
TREND_PATH = OUTPUTS_DIR / "baseline_residual_trend.csv"
FLAGS_PATH = OUTPUTS_DIR / "validation_flags.csv"
PRED_PATH = OUTPUTS_DIR / "baseline_predictions.parquet"
COMPARISON_PATH = OUTPUTS_DIR / "model_comparison.csv"
PRIVATE_DIR = OUTPUTS_DIR / "private"

FAIL_SHAPE_MAPE = 5.0
FLAG_MAPE = 5.0
FLAG_TREND = 1.0  # % of demand per year


# --------------------------------------------------------------------------- per BA


def cross_validate(df: pl.DataFrame, ba: str, model_cls) -> pl.DataFrame:
    """Baseline rows with out-of-fold and in-sample predictions for one model."""
    folds = fold_ids(df, ba)
    oof = np.full(df.height, np.nan)
    for k in np.unique(folds):
        test = folds == k
        m = model_cls().fit(df.filter(pl.Series(~test)))
        oof[test] = m.predict(df.filter(pl.Series(test)))
    full = model_cls().fit(df).predict(df)
    return df.select("ba", "period_end_utc", pl.col(TARGET).alias("actual")).with_columns(
        pl.lit(model_cls.name).alias("model"),
        pl.Series("pred_oof", oof),
        pl.Series("pred_insample", full),
    )


def residual_trend(pred: pl.DataFrame) -> dict[str, float]:
    """Slope of the monthly in-sample residual (% of demand) over time, HAC standard error."""
    m = (
        pred.group_by(pl.col("period_end_utc").dt.truncate("1mo").alias("month"))
        .agg(
            (
                100 * (pl.col("actual") - pl.col("pred_insample")).sum() / pl.col("actual").sum()
            ).alias("resid_pct"),
            pl.len().alias("n"),
        )
        .filter(pl.col("n") >= 24 * 20)  # months with at least 20 days of data
        .sort("month")
    )
    t = (m["month"].dt.year() + (m["month"].dt.month() - 0.5) / 12).to_numpy()
    fit = sm.OLS(m["resid_pct"].to_numpy(), sm.add_constant(t)).fit(
        cov_type="HAC", cov_kwds={"maxlags": 3}
    )
    return {
        "trend_pct_per_yr": float(fit.params[1]),
        "trend_se": float(fit.bse[1]),
        "n_months": int(m.height),
    }


def validate_ba(ba: str, table: pl.DataFrame) -> tuple[pl.DataFrame, list[dict], list[dict]]:
    preds, rows, trends = [], [], []
    for name, cls in MODELS.items():
        df = baseline_rows(table, cls.features)
        p = cross_validate(df, ba, cls)
        preds.append(p)
        yr = p["period_end_utc"].dt.year().to_numpy()
        a, o = p["actual"].to_numpy(), p["pred_oof"].to_numpy()
        for y in np.unique(yr):
            rows.append(
                {"ba": ba, "model": name, "holdout": str(y), **metrics(a[yr == y], o[yr == y])}
            )
        pooled = metrics(a, o)
        # Pooled shape MAPE removes each year's own mean error, not the pooled one.
        e = o - a
        for y in np.unique(yr):
            e[yr == y] -= e[yr == y].mean()
        pooled["shape_mape_pct"] = float(100 * np.mean(np.abs(e) / a))
        rows.append({"ba": ba, "model": name, "holdout": "all", **pooled})
        trends.append({"ba": ba, "model": name, **residual_trend(p)})
    log.info("%s done", ba)
    return pl.concat(preds), rows, trends


# --------------------------------------------------------------------------- flags


def flag(metrics_df: pl.DataFrame, trend_df: pl.DataFrame) -> pl.DataFrame:
    pooled = metrics_df.filter(pl.col("holdout") == "all").drop("holdout")
    worst = (
        metrics_df.filter(pl.col("holdout") != "all")
        .group_by("ba", "model")
        .agg(pl.col("bias_pct").abs().max().alias("worst_year_abs_bias_pct"))
    )
    df = pooled.join(worst, on=["ba", "model"]).join(trend_df, on=["ba", "model"])
    out = []
    for r in df.iter_rows(named=True):
        reasons = []
        if r["shape_mape_pct"] > FAIL_SHAPE_MAPE:
            reasons.append(f"shape MAPE {r['shape_mape_pct']:.1f}% > {FAIL_SHAPE_MAPE:.0f}%")
        status = "fail" if reasons else "pass"
        if r["mape_pct"] > FLAG_MAPE:
            reasons.append(f"held-out MAPE {r['mape_pct']:.1f}% > {FLAG_MAPE:.0f}%")
        if abs(r["trend_pct_per_yr"]) > FLAG_TREND:
            reasons.append(f"baseline residual trend {r['trend_pct_per_yr']:+.1f}%/yr")
        if r["ba"] in SHORT_BASELINE:
            reasons.append("short baseline (EIA-930 from 2018-09); month-out validation")
        if status == "pass" and reasons:
            status = "flag"
        out.append({**r, "status": status, "reasons": "; ".join(reasons)})
    cols = [
        "ba",
        "model",
        "status",
        "mape_pct",
        "shape_mape_pct",
        "bias_pct",
        "worst_year_abs_bias_pct",
        "r2",
        "trend_pct_per_yr",
        "trend_se",
        "reasons",
    ]
    return pl.DataFrame(out).select(cols).sort("model", "ba")


# --------------------------------------------------------------------------- model comparison


def temperature_range(table: pl.DataFrame) -> pl.DataFrame:
    """Per BA: share of hours since 2022 hotter or colder than anything in the baseline.

    Trees predict flat outside the training range, so these hours are where the GBM's
    counterfactual is least trustworthy (Prompt 9).
    """
    base = (
        table.filter(pl.col("in_baseline"), pl.col(TARGET).is_not_null())
        .group_by("ba")
        .agg(pl.col("t2m_c").max().alias("tmax"), pl.col("t2m_c").min().alias("tmin"))
    )
    late = table.filter(pl.col("period_end_utc").dt.year() >= 2022).join(base, on="ba")
    return late.group_by("ba").agg(
        (100 * (pl.col("t2m_c") > pl.col("tmax")).mean()).alias("pct_hours_2022on_above_base_max"),
        (100 * (pl.col("t2m_c") < pl.col("tmin")).mean()).alias("pct_hours_2022on_below_base_min"),
    )


def compare(flags: pl.DataFrame, table: pl.DataFrame) -> pl.DataFrame:
    """BA x model: held-out MAPE, shape MAPE and in-sample residual trend side by side.

    `gbm_trend_absorbed` = GAM trend - GBM trend. Both models lack a trend feature, so if
    the GBM's in-sample residual trend is much flatter than the GAM's, it is fitting the
    years' levels through weather proxies (memorising), which would also absorb growth.
    """
    wide = flags.pivot(
        on="model", index="ba", values=["mape_pct", "shape_mape_pct", "trend_pct_per_yr"]
    )
    shape = [f"shape_mape_pct_{m}" for m in MODELS]
    return (
        wide.with_columns(
            pl.concat_list(shape)
            .list.arg_min()
            .replace_strict(list(range(len(MODELS))), list(MODELS))
            .alias("best_shape_model"),
            (pl.col("trend_pct_per_yr_gam") - pl.col("trend_pct_per_yr_gbm")).alias(
                "gbm_trend_absorbed"
            ),
        )
        .join(temperature_range(table), on="ba", how="left")
        .sort("ba")
    )


# --------------------------------------------------------------------------- private DOM check


def pjm_dom_check() -> pl.DataFrame | None:
    """GAM on PJM metered DOM load (2016-2019) vs the EIA-930 window. Internal only (D3)."""
    if not pjm_load.OUT_PATH.exists():
        log.warning("No PJM DOM data; skipping the private DOM check")
        return None
    pjm = pl.read_parquet(pjm_load.OUT_PATH).select(
        pl.lit("DOM").alias("ba"),
        "period_end_utc",
        pl.col("mw").alias("demand_raw"),
        pl.col("mw").alias("demand_clean"),
        pl.lit(False).alias("flag_any"),
    )
    weather = pl.read_parquet(w.OUT_PATH).filter(pl.col("ba") == "DOM")
    table = feat.build_table(pjm, weather, feat.employment_index(["DOM"]), {"DOM": feat.DOM_TZ})
    df = baseline_rows(table, GamModel.features)
    out = []
    p = cross_validate(df, "PJM_DOM_LONG", GamModel)  # not in SHORT_BASELINE: year folds
    yr = p["period_end_utc"].dt.year().to_numpy()
    a, o = p["actual"].to_numpy(), p["pred_oof"].to_numpy()
    for y in [*np.unique(yr), "all"]:
        sel = np.ones_like(yr, bool) if y == "all" else yr == y
        out.append({"check": "pjm_loyo", "period": str(y), **metrics(a[sel], o[sel])})
    tr = residual_trend(p)
    out.append(
        {
            "check": "pjm_residual_trend_pct_per_yr",
            "period": "2016-2019",
            "value": tr["trend_pct_per_yr"],
        }
    )

    # Short vs long baseline: same PJM series, fitted on EIA-930's DOM window or on 2016-2019,
    # each predicting recent years' weather. The gap is what the short window alone changes.
    eia_start = pl.datetime(2018, 9, 18, time_zone="UTC")
    short = GamModel().fit(df.filter(pl.col("period_end_utc") >= eia_start))
    long_ = GamModel().fit(df)
    later = table.drop_nulls(GamModel.features).filter(pl.col("period_end_utc").dt.year() >= 2022)
    ly = later["period_end_utc"].dt.year().to_numpy()
    ps, pl_ = short.predict(later), long_.predict(later)
    act = later["demand_clean"].to_numpy()
    for y in np.unique(ly):
        sel = ly == y
        out.append(
            {
                "check": "short_minus_long_baseline_pct",
                "period": str(y),
                "value": float(100 * (ps[sel].mean() - pl_[sel].mean()) / pl_[sel].mean()),
                "actual_over_long_pct": float(100 * (act[sel].mean() / pl_[sel].mean() - 1)),
                "actual_over_short_pct": float(100 * (act[sel].mean() / ps[sel].mean() - 1)),
            }
        )
    res = pl.DataFrame(out, infer_schema_length=None)
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    res.write_csv(PRIVATE_DIR / "pjm_dom_check.csv")
    return res


# --------------------------------------------------------------------------- main


def run(bas: list[str] | None = None) -> pl.DataFrame:
    """Serial on purpose: a process pool stalled on macOS, and one BA takes ~30 s."""
    bas = bas or feat.modelled_bas()
    table = load_table(bas)
    results = [validate_ba(ba, table.filter(pl.col("ba") == ba)) for ba in bas]
    preds = pl.concat([r[0] for r in results])
    metrics_df = pl.DataFrame([row for r in results for row in r[1]])
    trend_df = pl.DataFrame([row for r in results for row in r[2]])
    flags = flag(metrics_df, trend_df)

    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    metrics_df.sort("ba", "model", "holdout").write_csv(METRICS_PATH, float_precision=4)
    trend_df.sort("ba", "model").write_csv(TREND_PATH, float_precision=4)
    flags.write_csv(FLAGS_PATH, float_precision=3)
    preds.write_parquet(PRED_PATH, compression="zstd")
    compare(flags, table).write_csv(COMPARISON_PATH, float_precision=3)
    log.info(
        "Wrote %s, %s, %s, %s", METRICS_PATH.name, TREND_PATH.name, FLAGS_PATH.name, PRED_PATH.name
    )
    with pl.Config(tbl_rows=60, tbl_cols=12, fmt_str_lengths=80):
        print(flags.drop("trend_se"))
    if "DOM" in bas:
        chk = pjm_dom_check()
        if chk is not None:
            with pl.Config(tbl_rows=30, tbl_cols=12):
                print("PRIVATE (PJM, do not publish):")
                print(chk)
    return flags


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()


if __name__ == "__main__":
    main()
