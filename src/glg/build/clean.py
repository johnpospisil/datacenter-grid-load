"""Cleaning rules and flags for EIA-930 demand.

Every rule adds a boolean `flag_*` column; nothing is overwritten. `demand_raw` is the value the
BA reported, `demand_clean` is `demand_raw` with flagged hours removed and short gaps (<= 3 h)
linearly interpolated. EIA's own cleaned series is kept as `demand_eia_adjusted` for comparison.
The rules are documented in DATA.md ("Cleaning rules").

Usage:
    python -m glg.build.clean
"""

import logging

import polars as pl

from glg.config import OUTPUTS_DIR, PROCESSED_DIR
from glg.ingest.eia930 import BA_METADATA_CSV, BALANCE_PARQUET, SUBREGION_PARQUET

log = logging.getLogger(__name__)

DEMAND_CLEAN_PARQUET = PROCESSED_DIR / "demand_clean.parquet"
SUBREGION_CLEAN_PARQUET = PROCESSED_DIR / "subregion_demand_clean.parquet"
BA_QUALITY_CSV = OUTPUTS_DIR / "ba_quality.csv"

FLAT_RUN_HOURS = 6  # identical values for this many hours in a row = stuck telemetry
LEVEL_WINDOW_HOURS = 168  # centred rolling median used as the "normal level"
LEVEL_HIGH = 2.0  # demand above 2x the rolling median
LEVEL_LOW = 0.4  # demand below 0.4x the rolling median
SPIKE_SIGMA = 5.0  # single-hour spike: jump in and jump out both exceed 5 robust sigma
MAX_INTERPOLATE_HOURS = 3

FLAG_COLUMNS = [
    "flag_missing",
    "flag_negative",
    "flag_zero",
    "flag_flat_run",
    "flag_level_outlier",
    "flag_spike",
    "flag_eia_imputed",
]


def add_flags(df: pl.DataFrame, group: list[str]) -> pl.DataFrame:
    """Add flag_* columns for `demand_raw`. `df` must hold complete hourly series per group."""
    d = pl.col("demand_raw")
    df = df.sort([*group, "period_end_utc"])

    # Flat runs: consecutive identical values. The first hour of a run is kept.
    same_as_prev = (d == d.shift(1)).over(group).fill_null(False)
    df = df.with_columns(_run=(~same_as_prev).cum_sum().over(group))
    df = df.with_columns(_run_len=pl.len().over([*group, "_run"]), _same=same_as_prev)

    # Robust sigma of hour-over-hour changes, per group and calendar year.
    df = df.with_columns(
        _d_in=d.diff().over(group),
        _d_out=(d.shift(-1) - d).over(group),
        _year=pl.col("period_end_utc").dt.year(),
    )
    mad = (pl.col("_d_in") - pl.col("_d_in").median()).abs().median()
    df = df.with_columns(_sigma=(1.4826 * mad).over([*group, "_year"]))
    df = df.with_columns(
        _median=d.rolling_median(
            LEVEL_WINDOW_HOURS, min_samples=LEVEL_WINDOW_HOURS // 4, center=True
        ).over(group)
    )

    spike = (
        (pl.col("_d_in").abs() > SPIKE_SIGMA * pl.col("_sigma"))
        & (pl.col("_d_out").abs() > SPIKE_SIGMA * pl.col("_sigma"))
        & (pl.col("_d_in").sign() != pl.col("_d_out").sign())
    )
    level_outlier = (d > LEVEL_HIGH * pl.col("_median")) | (d < LEVEL_LOW * pl.col("_median"))
    eia_imputed = (
        pl.col("demand_eia_imputed").is_not_null()
        if "demand_eia_imputed" in df.columns
        else pl.lit(False)
    )

    return df.with_columns(
        flag_missing=d.is_null(),
        flag_negative=(d < 0).fill_null(False),
        flag_zero=(d == 0).fill_null(False),
        flag_flat_run=(pl.col("_same") & (pl.col("_run_len") >= FLAT_RUN_HOURS)).fill_null(False),
        flag_level_outlier=level_outlier.fill_null(False),
        flag_spike=spike.fill_null(False),
        flag_eia_imputed=eia_imputed,
    ).drop("_run", "_run_len", "_same", "_d_in", "_d_out", "_year", "_sigma", "_median")


def add_clean_demand(df: pl.DataFrame, group: list[str]) -> pl.DataFrame:
    """demand_clean = demand_raw minus flagged hours, with gaps <= 3 h interpolated."""
    df = df.with_columns(flag_any=pl.any_horizontal(FLAG_COLUMNS))
    masked = pl.when(pl.col("flag_any")).then(None).otherwise(pl.col("demand_raw"))
    df = df.with_columns(_masked=masked).sort([*group, "period_end_utc"])

    # Length of each null gap, so only short ones get filled.
    is_null = pl.col("_masked").is_null()
    df = df.with_columns(_gap=(is_null != is_null.shift(1)).cum_sum().over(group))
    df = df.with_columns(_gap_len=pl.len().over([*group, "_gap"]))
    interpolated = pl.col("_masked").interpolate().over(group)
    fill = is_null & (pl.col("_gap_len") <= MAX_INTERPOLATE_HOURS) & interpolated.is_not_null()
    return df.with_columns(
        demand_clean=pl.when(fill).then(interpolated).otherwise(pl.col("_masked")),
        flag_interpolated=fill,
    ).drop("_masked", "_gap", "_gap_len")


def complete_hours(df: pl.DataFrame, group: list[str]) -> pl.DataFrame:
    """Insert rows for any missing UTC hours between each group's first and last hour."""
    spans = df.group_by(group).agg(
        start=pl.col("period_end_utc").min(), end=pl.col("period_end_utc").max()
    )
    grid = spans.with_columns(
        period_end_utc=pl.datetime_ranges("start", "end", "1h", time_zone="UTC")
    ).explode("period_end_utc")
    return grid.select([*group, "period_end_utc"]).join(
        df, on=[*group, "period_end_utc"], how="left"
    )


def load_bas() -> list[str]:
    """U.S. BAs that serve load (not generation-only) and report any demand."""
    meta = pl.read_csv(BA_METADATA_CSV)
    return meta.filter(~pl.col("generation_only") & pl.col("us_ba") & (pl.col("n_demand") > 0))[
        "ba"
    ].to_list()


def clean_balance() -> pl.DataFrame:
    raw = pl.read_parquet(BALANCE_PARQUET).filter(pl.col("ba").is_in(load_bas()))
    df = raw.select(
        "ba",
        "period_end_utc",
        demand_raw=pl.col("demand"),
        demand_eia_imputed=pl.col("demand_imputed"),
        demand_eia_adjusted=pl.col("demand_adjusted"),
        demand_forecast=pl.col("demand_forecast"),
    )
    df = add_clean_demand(add_flags(complete_hours(df, ["ba"]), ["ba"]), ["ba"])
    return df.sort("ba", "period_end_utc")


def clean_subregion() -> pl.DataFrame:
    raw = pl.read_parquet(SUBREGION_PARQUET)
    df = raw.select("ba", "subregion", "period_end_utc", demand_raw=pl.col("demand"))
    group = ["ba", "subregion"]
    df = add_clean_demand(add_flags(complete_hours(df, group), group), group)
    return df.sort(*group, "period_end_utc")


def flag_summary(df: pl.DataFrame, group: list[str]) -> pl.DataFrame:
    return (
        df.group_by(group)
        .agg(
            pl.len().alias("hours"),
            *[pl.col(c).sum() for c in [*FLAG_COLUMNS, "flag_interpolated"]],
            clean_pct=(pl.col("demand_clean").is_not_null().mean() * 100).round(2),
        )
        .sort(group)
    )


def quality_metrics(df: pl.DataFrame, group: list[str]) -> pl.DataFrame:
    """Clean-data coverage per series for the baseline (2016-2019) and recent (2022-2025) years.

    Used to pick the modelling shortlist (data/reference/ba_shortlist.csv).
    """
    year = pl.col("period_end_utc").dt.year()
    ok = pl.col("demand_clean").is_not_null()
    by_year = (
        df.filter(year.is_between(2016, 2025))
        .group_by([*group, year.alias("year")])
        .agg(pct=ok.mean() * 100)
        .group_by(group)
        .agg(min_year_pct=pl.col("pct").min().round(1))
    )
    return (
        df.group_by(group)
        .agg(
            mean_mw=pl.col("demand_clean").mean().round(0),
            first_hour_utc=pl.col("period_end_utc").min(),
            baseline_pct=(ok.filter(year.is_between(2016, 2019)).sum() / (4 * 8766) * 100).round(1),
            recent_pct=(ok.filter(year.is_between(2022, 2025)).sum() / (4 * 8766) * 100).round(1),
        )
        .join(by_year, on=group, how="left")
        .sort("mean_mw", descending=True)
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    for name, build, out, group in (
        ("BA", clean_balance, DEMAND_CLEAN_PARQUET, ["ba"]),
        ("subregion", clean_subregion, SUBREGION_CLEAN_PARQUET, ["ba", "subregion"]),
    ):
        df = build()
        df.write_parquet(out)
        log.info("Wrote %s (%d rows)", out, df.height)
        with pl.Config(tbl_rows=-1, tbl_width_chars=220, tbl_cols=-1):
            log.info("%s flag counts:\n%s", name, flag_summary(df, group))
        if name == "BA":
            OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
            quality_metrics(df, group).write_csv(BA_QUALITY_CSV)
            log.info("Wrote %s", BA_QUALITY_CSV)


if __name__ == "__main__":
    main()
