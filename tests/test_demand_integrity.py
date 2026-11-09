"""Integrity of the built demand tables (skipped if the data hasn't been built)."""

import polars as pl
import pytest

from glg.build.clean import DEMAND_CLEAN_PARQUET, SUBREGION_CLEAN_PARQUET
from glg.config import INTERIM_DIR, REFERENCE_DIR
from glg.ingest.eia930 import BALANCE_PARQUET, SUBREGION_PARQUET

TABLES = {
    "ba": (DEMAND_CLEAN_PARQUET, BALANCE_PARQUET, ["ba"]),
    "subregion": (SUBREGION_CLEAN_PARQUET, SUBREGION_PARQUET, ["ba", "subregion"]),
}


def _load(path, cols):
    if not path.exists():
        pytest.skip(f"{path.name} not built")
    return pl.read_parquet(path, columns=cols)


@pytest.mark.parametrize("name", TABLES)
def test_no_duplicate_keys(name):
    path, _, keys = TABLES[name]
    df = _load(path, [*keys, "period_end_utc"])
    assert df.select(pl.struct(*keys, "period_end_utc").is_duplicated().sum()).item() == 0


@pytest.mark.parametrize("name", TABLES)
def test_one_row_per_utc_hour(name):
    path, _, keys = TABLES[name]
    df = _load(path, [*keys, "period_end_utc"])
    spans = df.group_by(keys).agg(
        n=pl.len(),
        hours=(pl.col("period_end_utc").max() - pl.col("period_end_utc").min()).dt.total_hours()
        + 1,
        steps=pl.col("period_end_utc").sort().diff().dt.total_hours().drop_nulls().unique(),
    )
    assert (spans["n"] == spans["hours"]).all(), spans.filter(pl.col("n") != pl.col("hours"))
    assert spans["steps"].list.eval(pl.element() == 1).list.all().all()


@pytest.mark.parametrize("name", TABLES)
def test_raw_demand_is_unchanged_from_ingest(name):
    """Cleaning must never overwrite what the BA reported."""
    path, raw_path, keys = TABLES[name]
    clean_df = _load(path, [*keys, "period_end_utc", "demand_raw"])
    raw = _load(raw_path, [*keys, "period_end_utc", "demand"])
    j = raw.join(clean_df, on=[*keys, "period_end_utc"], how="inner")
    assert j.height == raw.filter(pl.col("ba").is_in(clean_df["ba"].unique().implode())).height
    differ = j.filter(~pl.col("demand").eq_missing(pl.col("demand_raw")))
    assert differ.height == 0, differ.head()


@pytest.mark.parametrize("name", TABLES)
def test_clean_value_is_raw_or_interpolated(name):
    path, _, keys = TABLES[name]
    df = _load(path, ["demand_raw", "demand_clean", "flag_any", "flag_interpolated"])
    # Unflagged hours keep the raw value exactly.
    kept = df.filter(~pl.col("flag_any"))
    assert (kept["demand_clean"] == kept["demand_raw"]).all()
    # Flagged hours are either null or interpolated, never the raw value silently kept.
    flagged = df.filter(pl.col("flag_any"))
    bad = flagged.filter(pl.col("demand_clean").is_not_null() & ~pl.col("flag_interpolated"))
    assert bad.height == 0
    # Interpolated hours were flagged.
    assert df.filter(pl.col("flag_interpolated"))["flag_any"].all()


def test_clean_demand_is_positive():
    df = _load(DEMAND_CLEAN_PARQUET, ["ba", "demand_clean"])
    assert (df["demand_clean"].drop_nulls() > 0).all()


def test_shortlisted_bas_have_enough_clean_data():
    """Every 'yes' top-level BA keeps >= 90% clean hours in every year 2016-2025."""
    sl = pl.read_csv(REFERENCE_DIR / "ba_shortlist.csv")
    yes = sl.filter(pl.col("include") == "yes", pl.col("parent").is_null())["series"]
    df = _load(DEMAND_CLEAN_PARQUET, ["ba", "period_end_utc", "demand_clean"])
    year = pl.col("period_end_utc").dt.year()
    cov = (
        df.filter(pl.col("ba").is_in(yes.implode()), year.is_between(2016, 2025))
        .group_by("ba", year.alias("year"))
        .agg(pct=pl.col("demand_clean").is_not_null().mean() * 100)
    )
    low = cov.filter(pl.col("pct") < 90)
    assert low.height == 0, low.sort("pct")


PJM_DOM = INTERIM_DIR / "pjm_dom_hourly.parquet"


@pytest.mark.skipif(not PJM_DOM.exists(), reason="PJM DOM export not present")
def test_pjm_dom_complete_and_unique():
    df = pl.read_parquet(PJM_DOM)
    assert df["period_end_utc"].is_unique().all()
    steps = df["period_end_utc"].diff().drop_nulls().dt.total_hours().unique().to_list()
    assert steps == [1]
    assert (df["mw"] > 0).all()
