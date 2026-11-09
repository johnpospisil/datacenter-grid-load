"""Annual EIA-930 totals vs FERC Form 714, the independent benchmark.

FERC-714 is the annual hourly planning-area demand each BA files with FERC: the same
utilities' metering, but a separate, later and revised regulatory filing, not EIA's
real-time collection. Where a BA and a planning area cover the same territory, annual
energy should agree closely. We compare on the hours present in both series, so gaps
don't count as disagreement, and check:

- relation "same": |EIA / FERC - 1| <= 3%
- relation "definition_difference": the two cover different territories, so we test that
  the *ratio* is stable: |error - median error| <= 3 pp. Growth estimates depend on that,
  not on the level.

Output: data/reference/annual_benchmarks.csv (committed; read by
tests/test_annual_reconciliation.py). FERC-714 data via Catalyst Cooperative PUDL, CC-BY-4.0.

Run: python -m glg.build.benchmarks
"""

import logging

import polars as pl

from glg.build.clean import DEMAND_CLEAN_PARQUET
from glg.config import REFERENCE_DIR
from glg.ingest import ferc714

log = logging.getLogger(__name__)

MAP_CSV = REFERENCE_DIR / "ferc714_ba_map.csv"
BENCHMARK_CSV = REFERENCE_DIR / "annual_benchmarks.csv"
YEARS = (2016, 2025)
TOLERANCE_PCT = 3.0
MIN_COVERAGE = 0.9  # share of the year's hours present in both series
SOURCE = "FERC Form 714 Part III Sch. 2 (hourly planning-area demand), via PUDL"


def load_map() -> pl.DataFrame:
    m = pl.read_csv(MAP_CSV, schema_overrides={"ferc714_respondent_ids": pl.String})
    return m.with_columns(
        pl.col("eia930_series").str.split(";"),
        pl.col("ferc714_respondent_ids").str.split(";").cast(pl.List(pl.Int64)),
    )


def hourly_pairs(mapping: pl.DataFrame) -> pl.DataFrame:
    """Hourly (ba, period_end_utc, eia, ferc) for every mapped BA with a FERC respondent."""
    m = mapping.filter(pl.col("relation") != "none")
    resp = m.select("ba", "ferc714_respondent_ids").explode("ferc714_respondent_ids")
    resp = resp.rename({"ferc714_respondent_ids": "respondent_id_ferc714"})
    series = m.select("ba", "eia930_series").explode("eia930_series")

    h = pl.read_parquet(
        ferc714.table_path("out_ferc714__hourly_planning_area_demand"),
        columns=["respondent_id_ferc714", "datetime_utc", "demand_reported_mwh"],
    )
    ferc = (
        h.join(resp, on="respondent_id_ferc714")
        .group_by("ba", "datetime_utc")
        .agg(ferc=pl.col("demand_reported_mwh").sum())
        .select(
            "ba", pl.col("datetime_utc").dt.replace_time_zone("UTC").alias("period_end_utc"), "ferc"
        )
    )
    e = pl.read_parquet(DEMAND_CLEAN_PARQUET, columns=["ba", "period_end_utc", "demand_clean"])
    eia = (
        e.join(series, left_on="ba", right_on="eia930_series")
        .group_by("ba_right", "period_end_utc")
        # Multi-series BAs only count hours where every component is present.
        .agg(eia=pl.col("demand_clean").sum(), n=pl.col("demand_clean").count())
        .join(series.group_by("ba").agg(need=pl.len()), left_on="ba_right", right_on="ba")
        .filter(pl.col("n") == pl.col("need"))
        .select(pl.col("ba_right").alias("ba"), "period_end_utc", "eia")
    )
    return eia.join(ferc, on=["ba", "period_end_utc"]).filter(pl.col("ferc") > 0)


def annual_comparison(pairs: pl.DataFrame) -> pl.DataFrame:
    year = pl.col("period_end_utc").dt.year()
    return (
        pairs.filter(year.is_between(*YEARS))
        .group_by("ba", year.alias("year"))
        .agg(
            hours_compared=pl.len(),
            eia930_twh=(pl.col("eia").sum() / 1e6).round(3),
            benchmark_twh=(pl.col("ferc").sum() / 1e6).round(3),
        )
        .with_columns(
            error_pct=((pl.col("eia930_twh") / pl.col("benchmark_twh") - 1) * 100).round(2)
        )
    )


def reconcile(annual: pl.DataFrame, mapping: pl.DataFrame) -> pl.DataFrame:
    """Add pass/fail status per BA x year (see module docstring for the rules)."""
    hours_in_year = pl.when(pl.col("year") % 4 == 0).then(8784).otherwise(8760)
    df = annual.join(mapping.select("ba", "relation"), on="ba")
    df = df.with_columns(median_error=pl.col("error_pct").median().over("ba"))
    level_ok = pl.col("error_pct").abs() <= TOLERANCE_PCT
    ratio_ok = (pl.col("error_pct") - pl.col("median_error")).abs() <= TOLERANCE_PCT
    ok = pl.when(pl.col("relation") == "same").then(level_ok).otherwise(ratio_ok)
    status = (
        pl.when(pl.col("hours_compared") < MIN_COVERAGE * hours_in_year)
        .then(pl.lit("insufficient_overlap"))
        .when(ok)
        .then(pl.lit("pass"))
        .otherwise(pl.lit("fail"))
    )
    return df.with_columns(status=status, median_error=pl.col("median_error").round(2)).sort(
        "ba", "year"
    )


def build() -> pl.DataFrame:
    mapping = load_map()
    result = reconcile(annual_comparison(hourly_pairs(mapping)), mapping)
    ids = mapping.select(
        "ba",
        benchmark_respondents=pl.col("ferc714_respondent_ids")
        .cast(pl.List(pl.String))
        .list.join(";"),
        eia930_series=pl.col("eia930_series").list.join(";"),
    )
    return result.join(ids, on="ba").select(
        "ba",
        "eia930_series",
        "year",
        pl.lit(SOURCE).alias("benchmark_source"),
        "benchmark_respondents",
        "relation",
        "hours_compared",
        "eia930_twh",
        "benchmark_twh",
        "error_pct",
        "median_error",
        "status",
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ferc714.download()
    df = build()
    df.write_csv(BENCHMARK_CSV)
    log.info("Wrote %s (%d rows)", BENCHMARK_CSV, df.height)
    with pl.Config(tbl_rows=-1, tbl_cols=-1, tbl_width_chars=200):
        log.info("Not passing:\n%s", df.filter(pl.col("status") != "pass"))


if __name__ == "__main__":
    main()
