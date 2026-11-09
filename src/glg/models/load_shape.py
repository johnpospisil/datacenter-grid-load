"""Load-shape fingerprint and 24/7 vs variable growth decomposition (Prompt 10).

Works from the Prompt 9 counterfactual (data/outputs/counterfactual_hourly.parquet: actual
and baseline-GAM prediction per BA-hour) joined with local hour and degree-hours from the
modelling table. Nothing is refitted.

Hour bands (local time, by the hour's start, as in the models):
- night: 01:00-05:00 (hours starting 1-4), the overnight floor;
- afternoon: 14:00-19:00 (hours starting 14-18), the usual daily peak;
- peak hours: the 100 hours of each period with the highest *predicted* load, i.e. the
  hours the weather and calendar make the peak. Choosing them by prediction, not by actual
  load, keeps the selection independent of the residual being measured.

Weather adjustment is the Prompt 9 ratio of sums: in a band, r = sum(actual) / sum(pred),
and weather-adjusted load = r x the band's mean baseline (2016-2019) load.

Decomposition of the residual (actual - predicted, MW) per period, by OLS:

    resid_t = a + sum_h b_h [hour_t = h] + c CDH65_t + d HDH65_t,   sum_h b_h = 0

- flat (24/7) = a + mean(b_h, night hours): the residual at night at 65 F, present in every
  hour of the day and in any weather;
- weather-sensitive = c mean(CDH65) + d mean(HDH65): extra load that grows with heat or
  cold (more air-conditioning, heat pumps);
- daytime shape = the rest (mean residual - flat - weather): extra load concentrated in
  non-night hours.

The three add up to the mean residual. Growth since 2022 is the change in each part between
the start year (2022; FPL/SOCO 2023) and the latest 12 months, using the Prompt 9 windows.
95% intervals come from a 14-day moving-block bootstrap of days within each window. The
same decomposition on the GBM residuals is the model-family check.

Growth % is Prompt 9's (growth_ranking.csv); this module adds where in the day it sits.

Verdict per series against the brief's "wrong if" test (growth only at daytime/peak hours):
- "24/7 floor": night MW added >= 0.8 x afternoon MW added;
- "daytime/peak": night MW added < 0.5 x afternoon MW added;
- "mixed" in between; "no growth" if the total change's interval includes zero.

Run: python -m glg.models.load_shape
"""

import logging
from dataclasses import dataclass
from datetime import date

import numpy as np
import polars as pl

from glg.config import OUTPUTS_DIR, PROCESSED_DIR
from glg.models import growth as g

log = logging.getLogger(__name__)

METRICS_PATH = OUTPUTS_DIR / "load_shape_metrics.csv"
DECOMP_PATH = OUTPUTS_DIR / "growth_decomposition.csv"
MONTHLY_PATH = OUTPUTS_DIR / "load_shape_monthly.parquet"
PAIRS_PATH = OUTPUTS_DIR / "comparison_pairs.csv"

NIGHT = (1, 2, 3, 4)
AFTERNOON = (14, 15, 16, 17, 18)
PEAK_HOURS = 100
FLOOR_RATIO = 0.8
PEAK_RATIO = 0.5
N_BOOT = 500

# Data-center region -> comparison region of similar climate without a big build-out.
# Reasons are in DATA.md ("Comparison pairings").
PAIRS = {
    "DOM": (
        "CPLE",
        "Neighbouring Carolinas utility; same humid-subtropical climate and "
        "mixed heating/cooling load; no hyperscale cluster on Loudoun's scale",
    ),
    "ERCO": (
        "FPC",
        "Hot, humid, cooling-dominated, fast-growing Sun Belt population: "
        "controls for migration-driven growth",
    ),
    "AZPS+SRP": (
        "CISO",
        "Hot-summer Southwest neighbour with strong rooftop-solar growth, "
        "which tests whether falling midday load alone makes a 'floor' signal",
    ),
    "NEVP": ("CISO", "Desert/hot-summer West neighbour; same rooftop-solar caveat"),
    "BPAT": (
        "PACE",
        "Interior West, winter-heating climate, PacifiCorp territory "
        "neighbouring BPA; small data-center presence",
    ),
    "GCPD": ("PACE", "Interior West, cold-winter climate; no Quincy-scale cluster"),
    "PJM": ("MISO", "Neighbouring multi-state ISO, similar climate range and industrial mix"),
}


@dataclass
class Shape:
    """Hourly residual frame for one series."""

    name: str
    df: pl.DataFrame  # period_end_utc, actual, pred_gam, pred_gbm, local_hour, cdh, hdh, ...


# --------------------------------------------------------------------------- data


def load_hourly() -> pl.DataFrame:
    cf = pl.read_parquet(g.HOURLY_PATH)
    mt = pl.scan_parquet(PROCESSED_DIR / "model_table.parquet").select(
        "ba", "period_end_utc", "local_hour", "cdh65f", "hdh65f", "in_baseline", "is_covid"
    )
    return cf.join(mt.collect(), on=["ba", "period_end_utc"], how="inner")


def _with_time(df: pl.DataFrame) -> pl.DataFrame:
    t = pl.col("period_end_utc") - pl.duration(hours=1)
    return df.with_columns(
        t.dt.year().alias("year"),
        t.dt.truncate("1mo").dt.date().alias("month"),
        t.dt.date().alias("day"),
        (pl.col("actual") - pl.col("pred_gam")).alias("resid"),
        (pl.col("actual") - pl.col("pred_gbm")).alias("resid_gbm"),
    )


def build_series(hourly: pl.DataFrame) -> list[Shape]:
    cols = ["actual", "pred_gam", "pred_gbm"]
    out = {ba: d.sort("period_end_utc") for (ba,), d in hourly.group_by("ba")}
    shapes = [Shape(ba, _with_time(d)) for ba, d in sorted(out.items())]
    for name, parts in g.COMBINED.items():
        if not all(p in out for p in parts):
            continue
        frames = [out[p] for p in parts]
        keys = frames[0].select("period_end_utc")
        for f in frames[1:]:
            keys = keys.join(f.select("period_end_utc"), on="period_end_utc")
        frames = [f.join(keys, on="period_end_utc").sort("period_end_utc") for f in frames]
        # degree-hours weighted by each part's mean baseline load; local hour of the first
        w = np.array([f.filter(pl.col("in_baseline"))["actual"].mean() for f in frames])
        w = w / w.sum()
        base = frames[0].select("period_end_utc", "local_hour", "in_baseline", "is_covid")
        comb = base.with_columns(
            *(pl.Series(c, sum(f[c].to_numpy() for f in frames)) for c in cols),
            *(
                pl.Series(c, sum(wi * f[c].to_numpy() for wi, f in zip(w, frames, strict=True)))
                for c in ["cdh65f", "hdh65f"]
            ),
        )
        shapes.append(Shape(name, _with_time(comb)))
    for alias, src in g.ALIASES.items():
        if src in out:
            shapes.append(Shape(alias, _with_time(out[src])))
    return shapes


# --------------------------------------------------------------------------- decomposition


def design(df: pl.DataFrame) -> np.ndarray:
    """Intercept, 23 sum-to-zero hour columns (hour 0 is minus their sum), CDH65, HDH65."""
    h = df["local_hour"].to_numpy()
    eff = (h[:, None] == np.arange(1, 24)[None, :]).astype(float)
    eff[h == 0] = -1.0
    return np.column_stack([np.ones(len(h)), eff, df["cdh65f"], df["hdh65f"]])


def components(beta: np.ndarray, xbar: np.ndarray, resid_mean: float) -> dict:
    b = np.concatenate([[-beta[1:24].sum()], beta[1:24]])  # all 24 hour effects
    flat = beta[0] + b[list(NIGHT)].mean()
    weather = beta[24] * xbar[24] + beta[25] * xbar[25]
    return {
        "total_mw": resid_mean,
        "flat_mw": flat,
        "weather_mw": weather,
        "shape_mw": resid_mean - flat - weather,
    }


def _daily_blocks(df: pl.DataFrame, resid: str) -> tuple[list, np.ndarray, np.ndarray]:
    """Per day: X'X, X'y, X column sums, n, residual sum (for fast bootstrap refits)."""
    x, y = design(df), df[resid].to_numpy()
    day = df["day"].to_numpy()
    _, start = np.unique(day, return_index=True)
    bounds = list(zip(start, [*start[1:], len(day)], strict=True))
    xtx = np.array([x[i:j].T @ x[i:j] for i, j in bounds])
    xty = np.array([x[i:j].T @ y[i:j] for i, j in bounds])
    xs = np.array([x[i:j].sum(0) for i, j in bounds])
    return bounds, (xtx, xty, xs), np.array([y[i:j].sum() for i, j in bounds])


def _solve(xtx, xty, xs, ys, idx) -> dict:
    a, b = xtx[idx].sum(0), xty[idx].sum(0)
    beta = np.linalg.lstsq(a, b, rcond=None)[0]
    n = xs[idx, 0].sum()
    return components(beta, xs[idx].sum(0) / n, ys[idx].sum() / n)


def decompose(df: pl.DataFrame, resid: str = "resid", rng=None, n_boot: int = 0):
    """Point decomposition, and optionally bootstrap draws (14-day moving blocks)."""
    _, (xtx, xty, xs), ys = _daily_blocks(df, resid)
    nd = len(ys)
    est = _solve(xtx, xty, xs, ys, np.arange(nd))
    if not n_boot:
        return est, None
    draws = []
    for _ in range(n_boot):
        idx = _block_index(nd, rng)
        draws.append(_solve(xtx, xty, xs, ys, idx))
    return est, pl.DataFrame(draws)


def _block_index(nd: int, rng: np.random.Generator) -> np.ndarray:
    b = min(g.BLOCK_DAYS, nd)
    starts = rng.integers(0, nd - b + 1, size=int(np.ceil(nd / b)))
    return (starts[:, None] + np.arange(b)[None, :]).ravel()[:nd]


# --------------------------------------------------------------------------- bands


def band_ratio(df: pl.DataFrame, hours) -> float:
    d = df.filter(pl.col("local_hour").is_in(list(hours)))
    return d["actual"].sum() / d["pred_gam"].sum()


def band_mw(df: pl.DataFrame, hours) -> float:
    return df.filter(pl.col("local_hour").is_in(list(hours)))["resid"].mean()


def band_boot(df: pl.DataFrame, rng: np.random.Generator, n_boot: int = N_BOOT) -> np.ndarray:
    """Bootstrap draws (14-day blocks) of the mean night and afternoon residual MW."""
    d = (
        df.with_columns(
            pl.col("local_hour").is_in(list(NIGHT)).alias("n"),
            pl.col("local_hour").is_in(list(AFTERNOON)).alias("a"),
        )
        .group_by("day")
        .agg(
            pl.col("resid").filter(pl.col("n")).sum().alias("rn"),
            pl.col("n").sum().alias("nn"),
            pl.col("resid").filter(pl.col("a")).sum().alias("ra"),
            pl.col("a").sum().alias("na"),
        )
        .sort("day")
    )
    rn, nn, ra, na = (d[c].to_numpy().astype(float) for c in ["rn", "nn", "ra", "na"])
    out = np.empty((n_boot, 2))
    for k in range(n_boot):
        i = _block_index(d.height, rng)
        out[k] = rn[i].sum() / nn[i].sum(), ra[i].sum() / na[i].sum()
    return out


def peak_hours(df: pl.DataFrame, n: int = PEAK_HOURS) -> pl.DataFrame:
    return df.sort("pred_gam", descending=True).head(n)


def year_metrics(s: Shape) -> pl.DataFrame:
    """Per BA x year: actual shape metrics and weather-adjusted floor, peak and mean."""
    df = s.df
    base = df.filter(pl.col("in_baseline"))
    base_night = base.filter(pl.col("local_hour").is_in(list(NIGHT)))["actual"].mean()
    base_after = base.filter(pl.col("local_hour").is_in(list(AFTERNOON)))["actual"].mean()
    base_mean = base["actual"].mean()
    rows = []
    for (yr,), d in df.group_by("year", maintain_order=True):
        daily_min = d.group_by("day").agg(pl.col("actual").min())["actual"].mean()
        r_all = d["actual"].sum() / d["pred_gam"].sum()
        r_night, r_after = band_ratio(d, NIGHT), band_ratio(d, AFTERNOON)
        pk = peak_hours(d)
        dec, _ = decompose(d)
        rows.append(
            {
                "ba": s.name,
                "year": yr,
                "n_hours": d.height,
                "mean_mw": d["actual"].mean(),
                "peak_mw": d["actual"].max(),
                "load_factor": d["actual"].mean() / d["actual"].max(),
                "daily_min_mw": daily_min,
                "min_mean_ratio": daily_min / d["actual"].mean(),
                "night_mw": d.filter(pl.col("local_hour").is_in(list(NIGHT)))["actual"].mean(),
                "night_adj_mw": r_night * base_night,
                "afternoon_adj_mw": r_after * base_after,
                "mean_adj_mw": r_all * base_mean,
                "night_adj_ratio": (r_night * base_night) / (r_all * base_mean),
                "resid_pct": 100 * (r_all - 1),
                "night_resid_pct": 100 * (r_night - 1),
                "afternoon_resid_pct": 100 * (r_after - 1),
                "peak_resid_pct": 100 * (pk["actual"].sum() / pk["pred_gam"].sum() - 1),
                "night_resid_mw": band_mw(d, NIGHT),
                "afternoon_resid_mw": band_mw(d, AFTERNOON),
                "peak_resid_mw": pk["resid"].mean(),
                **dec,
            }
        )
    return pl.DataFrame(rows).with_columns((pl.col("n_hours") < 8000).alias("partial_year"))


# --------------------------------------------------------------------------- since 2022


def windows(s: Shape, rule: g.Rule) -> tuple[pl.DataFrame, pl.DataFrame, date, date]:
    """Start year and latest-12-month windows, as in Prompt 9's growth_since."""
    df = s.df
    counts = df.group_by("month").len()
    complete = counts.filter(pl.col("len") >= g.MIN_MONTH_HOURS)["month"]
    last = rule.last_month or complete.max()
    end_excl = date(last.year + (last.month == 12), last.month % 12 + 1, 1)
    last12_start = date(end_excl.year - 1, end_excl.month, 1)
    w0 = df.filter(pl.col("year") == rule.start_year)
    w1 = df.filter((pl.col("month") >= last12_start) & (pl.col("month") < end_excl))
    return w0, w1, last12_start, last


def _pctile(x: np.ndarray) -> tuple[float, float]:
    return float(np.percentile(x, 2.5)), float(np.percentile(x, 97.5))


def change_since(s: Shape, rule: g.Rule, rng: np.random.Generator) -> dict:
    w0, w1, l_from, l_to = windows(s, rule)
    e0, b0 = decompose(w0, rng=rng, n_boot=N_BOOT)
    e1, b1 = decompose(w1, rng=rng, n_boot=N_BOOT)
    gbm0, _ = decompose(w0, "resid_gbm")
    gbm1, _ = decompose(w1, "resid_gbm")
    d = {k: e1[k] - e0[k] for k in e0}
    db = b1 - b0
    pk0, pk1 = peak_hours(w0), peak_hours(w1)
    night = band_mw(w1, NIGHT) - band_mw(w0, NIGHT)
    after = band_mw(w1, AFTERNOON) - band_mw(w0, AFTERNOON)
    out = {
        "ba": s.name,
        "from_year": rule.start_year,
        "last12_from": l_from,
        "last12_to": l_to,
    }
    for k in ["total_mw", "flat_mw", "weather_mw", "shape_mw"]:
        lo, hi = _pctile(db[k].to_numpy())
        out |= {f"d_{k}": d[k], f"d_{k}_lo": lo, f"d_{k}_hi": hi}
    share = (db["flat_mw"] / db["total_mw"]).to_numpy()
    out["flat_share"] = d["flat_mw"] / d["total_mw"] if d["total_mw"] else np.nan
    out["flat_share_lo"], out["flat_share_hi"] = _pctile(share)
    out["flat_share_gbm"] = (gbm1["flat_mw"] - gbm0["flat_mw"]) / (
        gbm1["total_mw"] - gbm0["total_mw"]
    )
    out |= {
        "d_night_mw": night,
        "d_afternoon_mw": after,
        "d_peak_mw": pk1["resid"].mean() - pk0["resid"].mean(),
        "night_growth_pct": 100 * (band_ratio(w1, NIGHT) / band_ratio(w0, NIGHT) - 1),
        "afternoon_growth_pct": 100 * (band_ratio(w1, AFTERNOON) / band_ratio(w0, AFTERNOON) - 1),
        "peak_growth_pct": 100
        * (
            (pk1["actual"].sum() / pk1["pred_gam"].sum())
            / (pk0["actual"].sum() / pk0["pred_gam"].sum())
            - 1
        ),
        "night_to_afternoon": night / after if after else np.nan,
    }
    bb = band_boot(w1, rng) - band_boot(w0, rng)
    ratio = bb[:, 0] / bb[:, 1]
    out["night_to_afternoon_lo"], out["night_to_afternoon_hi"] = _pctile(ratio)
    out["p_floor"] = float(np.mean((bb[:, 1] <= 0) | (ratio >= FLOOR_RATIO)))
    out["verdict"] = verdict(out)
    return out


def verdict(r: dict) -> str:
    if r["d_total_mw_lo"] <= 0 <= r["d_total_mw_hi"] or r["d_total_mw"] <= 0:
        return "no growth"
    night, after = r["d_night_mw"], r["d_afternoon_mw"]
    if after <= 0 or night >= FLOOR_RATIO * after:
        return "24/7 floor"
    if night < PEAK_RATIO * after:
        return "daytime/peak"
    return "mixed"


# --------------------------------------------------------------------------- monthly bands


def monthly_bands(s: Shape) -> pl.DataFrame:
    """Per month: band sums, and trailing-12-month weather-adjusted index (2019 = 100)."""
    df = s.df.with_columns(
        pl.when(pl.col("local_hour").is_in(list(NIGHT)))
        .then(pl.lit("night"))
        .when(pl.col("local_hour").is_in(list(AFTERNOON)))
        .then(pl.lit("afternoon"))
        .otherwise(pl.lit("other"))
        .alias("band")
    )
    allb = df.with_columns(pl.lit("all").alias("band"))
    m = (
        pl.concat([df, allb])
        .group_by("band", "month")
        .agg(
            pl.len().alias("n_hours"),
            pl.col("actual").sum(),
            pl.col("pred_gam").sum(),
            pl.col("resid").mean().alias("resid_mw"),
        )
        .filter(pl.col("band") != "other")
        .sort("band", "month")
    )
    out = []
    for (band,), d in m.group_by("band", maintain_order=True):
        full = pl.DataFrame(
            {"month": pl.date_range(d["month"].min(), d["month"].max(), "1mo", eager=True)}
        )
        d = full.join(d.drop("band"), on="month", how="left").fill_null(0)
        hours_per_month = d["n_hours"].to_numpy()
        expected = {"night": 4, "afternoon": 5, "all": 24}[band] * 30.44
        a = d["actual"].rolling_sum(12).to_numpy()
        p = d["pred_gam"].rolling_sum(12).to_numpy()
        n = pl.Series(hours_per_month).rolling_sum(12).to_numpy()
        r = np.where(n >= g.ROLL_MIN_SHARE * 12 * expected, a / np.where(p > 0, p, np.nan), np.nan)
        y19 = d.filter(pl.col("month").dt.year() == g.INDEX_YEAR)
        r19 = y19["actual"].sum() / y19["pred_gam"].sum() if y19.height else np.nan
        out.append(
            d.with_columns(
                pl.lit(s.name).alias("ba"),
                pl.lit(band).alias("band"),
                pl.Series("index_12m", 100 * r / r19),
                pl.Series("ratio_12m", r),
            )
        )
    return pl.concat(out).select(
        "ba", "band", "month", "n_hours", "resid_mw", "ratio_12m", "index_12m"
    )


# --------------------------------------------------------------------------- run


# Pooled comparison set (decision D10), reported next to each pair (decided 2026-11-08).
COMPARISONS = ["MISO", "NYIS", "ISNE", "FPC", "CPLE", "CISO", "PACE", "FPL"]


def pairs_table(decomp: pl.DataFrame) -> pl.DataFrame:
    """Each data-center region vs its paired comparison AND the pooled comparison median."""
    d = {r["ba"]: r for r in decomp.iter_rows(named=True)}
    pool = [d[b] for b in COMPARISONS if b in d]
    pooled_growth = float(np.median([r["growth_pct"] for r in pool]))
    pooled_night = float(np.median([r["night_growth_pct"] for r in pool]))
    rows = []
    for dc, (comp, reason) in PAIRS.items():
        if dc not in d or comp not in d:
            continue
        a, b = d[dc], d[comp]
        rows.append(
            {
                "dc_region": dc,
                "comparison": comp,
                "reason": reason,
                "dc_growth_pct": a["growth_pct"],
                "comp_growth_pct": b["growth_pct"],
                "dc_night_growth_pct": a["night_growth_pct"],
                "comp_night_growth_pct": b["night_growth_pct"],
                "pooled_growth_pct": pooled_growth,
                "pooled_night_growth_pct": pooled_night,
                "dc_flat_share": a["flat_share"],
                "comp_flat_share": b["flat_share"],
                "dc_verdict": a["verdict"],
                "comp_verdict": b["verdict"],
            }
        )
    return pl.DataFrame(rows)


def run() -> tuple[pl.DataFrame, pl.DataFrame]:
    rng = np.random.default_rng(g.SEED)
    shapes = build_series(load_hourly())
    metrics, decomp, monthly = [], [], []
    for s in shapes:
        metrics.append(year_metrics(s))
        decomp.append(change_since(s, g.RULES.get(s.name, g.Rule()), rng))
        monthly.append(monthly_bands(s))
        log.info("%s: %s", s.name, decomp[-1]["verdict"])
    metrics_df = pl.concat(metrics)
    ranking = pl.read_csv(g.RANKING_PATH).select("ba", "growth_pct", "headline")
    decomp_df = (
        pl.DataFrame(decomp).join(ranking, on="ba", how="left").sort("growth_pct", descending=True)
    )
    pairs = pairs_table(decomp_df)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    metrics_df.write_csv(METRICS_PATH, float_precision=4)
    decomp_df.write_csv(DECOMP_PATH, float_precision=3)
    pl.concat(monthly).write_parquet(MONTHLY_PATH, compression="zstd")
    pairs.write_csv(PAIRS_PATH, float_precision=3)
    with pl.Config(tbl_rows=40, tbl_cols=14, tbl_width_chars=240):
        print(
            decomp_df.select(
                "ba",
                "growth_pct",
                "d_total_mw",
                "d_flat_mw",
                "d_weather_mw",
                "d_shape_mw",
                "flat_share",
                "flat_share_gbm",
                "d_night_mw",
                "d_afternoon_mw",
                "night_to_afternoon_lo",
                "night_to_afternoon_hi",
                "verdict",
            ).with_columns(pl.col(pl.Float64).round(2))
        )
        print(pairs.drop("reason").with_columns(pl.col(pl.Float64).round(2)))
    return decomp_df, pairs


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()


if __name__ == "__main__":
    main()
