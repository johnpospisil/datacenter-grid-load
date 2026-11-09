"""Daylight-saving transitions: one row per UTC hour, so local days have 23 / 25 hours."""

from datetime import date, datetime

import polars as pl
import pytest

from glg.build import clean
from glg.build.clean import DEMAND_CLEAN_PARQUET, SUBREGION_CLEAN_PARQUET
from glg.config import INTERIM_DIR, REFERENCE_DIR


def local_day_counts(df: pl.DataFrame, tz: str) -> pl.DataFrame:
    """Rows per local calendar day (hour-ending stamps, so each hour is dated by its start)."""
    local = (pl.col("period_end_utc") - pl.duration(hours=1)).dt.convert_time_zone(tz)
    return df.group_by(local.dt.date().alias("day")).agg(n=pl.len())


def test_complete_hours_across_dst_gives_23_and_25_hour_local_days():
    # 2019: spring forward 10 March, fall back 3 November (America/New_York)
    rows = []
    for start, end in [
        (datetime(2019, 3, 9, 5), datetime(2019, 3, 12, 4)),
        (datetime(2019, 11, 2, 4), datetime(2019, 11, 5, 5)),
    ]:
        ts = pl.datetime_range(start, end, "1h", eager=True, time_zone="UTC")
        rows.append(pl.DataFrame({"ba": "X", "period_end_utc": ts, "demand_raw": 1.0}))
    df = pl.concat(rows)
    # Drop the hours around each transition, as some BAs do, then rebuild the grid.
    df = df.filter(pl.col("period_end_utc").dt.hour().is_in([6, 7, 8]).not_())
    out = clean.complete_hours(df, ["ba"])
    assert out["period_end_utc"].is_unique().all()
    assert (out["period_end_utc"].diff().drop_nulls().dt.total_hours() == 1).all()
    counts = dict(local_day_counts(out, "America/New_York").iter_rows())
    assert counts[date(2019, 3, 10)] == 23
    assert counts[date(2019, 11, 3)] == 25


@pytest.fixture(scope="module")
def shortlist_clean():
    if not DEMAND_CLEAN_PARQUET.exists():
        pytest.skip("processed demand not built")
    sl = pl.read_csv(REFERENCE_DIR / "ba_shortlist.csv").filter(pl.col("include") != "no")
    meta = pl.read_csv(REFERENCE_DIR / "ba_metadata.csv").select("ba", "tz_civil")
    df = pl.read_parquet(
        DEMAND_CLEAN_PARQUET,
        columns=["ba", "period_end_utc", "demand_clean", "flag_missing", "flag_interpolated"],
    ).filter(pl.col("ba").is_in(sl["series"].implode()))
    return df, dict(meta.iter_rows())


def test_real_data_local_days_have_23_24_or_25_hours(shortlist_clean):
    df, tz = shortlist_clean
    for ba in df["ba"].unique():
        x = df.filter(pl.col("ba") == ba)
        counts = local_day_counts(x, tz[ba])
        # Ignore the partial first and last day of each series.
        inner = counts.sort("day")[1:-1]
        assert inner["n"].is_in([23, 24, 25]).all(), (ba, inner.filter(pl.col("n") != 24).head())
        observes_dst = tz[ba] != "America/Phoenix"
        if observes_dst:
            # One spring-forward and one fall-back day per year.
            assert abs((inner["n"] == 23).sum() - (inner["n"] == 25).sum()) <= 1, ba
        else:
            assert (inner["n"] == 24).all(), ba


def test_whole_missing_dst_days_are_not_filled(shortlist_clean):
    """Several BAs drop whole DST days (see DATA.md); those hours must stay null."""
    df, tz = shortlist_clean
    for ba in df["ba"].unique():
        x = df.filter(pl.col("ba") == ba)
        local = (pl.col("period_end_utc") - pl.duration(hours=1)).dt.convert_time_zone(tz[ba])
        x = x.with_columns(day=local.dt.date())
        dst_days = local_day_counts(x, tz[ba]).filter(pl.col("n") != 24)["day"]
        d = x.filter(pl.col("day").is_in(dst_days.implode()))
        filled_long_gaps = d.filter(pl.col("flag_missing"), pl.col("demand_clean").is_not_null())
        assert filled_long_gaps["flag_interpolated"].all(), ba
        assert filled_long_gaps.height <= 3 * dst_days.len(), ba


PJM_DOM = INTERIM_DIR / "pjm_dom_hourly.parquet"


@pytest.mark.skipif(not PJM_DOM.exists(), reason="PJM DOM export not present")
def test_pjm_dom_has_23_and_25_hour_days():
    counts = local_day_counts(pl.read_parquet(PJM_DOM), "America/New_York").sort("day")[1:-1]
    assert counts["n"].is_in([23, 24, 25]).all()
    assert (counts["n"] == 23).sum() >= 11 and (counts["n"] == 25).sum() >= 11


def test_utc_stamps_are_timezone_aware():
    if not DEMAND_CLEAN_PARQUET.exists():
        pytest.skip("processed demand not built")
    for path in (DEMAND_CLEAN_PARQUET, SUBREGION_CLEAN_PARQUET):
        dtype = pl.read_parquet_schema(path)["period_end_utc"]
        assert dtype == pl.Datetime("us", "UTC"), (path, dtype)
