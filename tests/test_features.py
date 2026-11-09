"""Calendar features and the modelling table: holidays, DST, periods, no leakage."""

from datetime import UTC, date, datetime, timedelta

import polars as pl
import pytest

from glg.build import features as f

# --------------------------------------------------------------------------- holidays


@pytest.fixture(scope="module")
def hol():
    t = f.holiday_table(range(2018, 2024))
    return {d: (k, n) for d, k, n in t.iter_rows()}


def test_major_and_minor_holidays(hol):
    assert hol[date(2019, 11, 28)] == ("holiday_major", "Thanksgiving Day")
    assert hol[date(2019, 12, 25)][0] == "holiday_major"
    assert hol[date(2019, 1, 21)] == ("holiday_minor", "Martin Luther King Jr. Day")
    assert hol[date(2019, 10, 14)][0] == "holiday_minor"  # Columbus Day


def test_observed_and_actual_dates_both_count(hol):
    # July 4 2021 was a Sunday, observed Monday July 5.
    assert hol[date(2021, 7, 4)][0] == "holiday_major"
    assert hol[date(2021, 7, 5)][0] == "holiday_major"


def test_juneteenth_only_from_2021(hol):
    assert date(2019, 6, 19) not in hol
    assert hol[date(2021, 6, 18)][1] == "Juneteenth National Independence Day"  # observed (Fri)


def test_bridge_days(hol):
    assert hol[date(2019, 11, 29)] == ("bridge", "Day after Thanksgiving")
    assert hol[date(2019, 12, 24)][0] == "bridge"  # a Tuesday
    assert hol[date(2019, 12, 27)][0] == "bridge"
    assert date(2022, 12, 24) not in hol  # a Saturday: stays a weekend
    assert hol[date(2022, 12, 26)][0] == "holiday_major"  # Christmas observed beats bridge


# --------------------------------------------------------------------------- calendar


def _hours(ba, start, n):
    return pl.DataFrame(
        {
            "ba": [ba] * n,
            "period_end_utc": [start + timedelta(hours=i) for i in range(n)],
        }
    ).with_columns(pl.col("period_end_utc").dt.cast_time_unit("us"))


TZ = {"NY": "America/New_York", "AZ": "America/Phoenix"}


def test_local_hour_is_the_hour_start():
    # 05:00 UTC on 2019-07-01 ends the hour 00:00-01:00 EDT.
    df = f.add_calendar(_hours("NY", datetime(2019, 7, 1, 5, tzinfo=UTC), 1), TZ)
    r = df.row(0, named=True)
    assert r["local_date"] == date(2019, 7, 1) and r["local_hour"] == 0 and r["is_dst"]
    assert r["dow"] == 0 and r["day_type"] == "weekday"  # a Monday


@pytest.mark.parametrize("day, n", [(date(2019, 3, 10), 23), (date(2019, 11, 3), 25)])
def test_dst_change_days(day, n):
    df = f.add_calendar(_hours("NY", datetime(2019, 1, 1, 6, tzinfo=UTC), 24 * 366), TZ)
    d = df.filter(pl.col("local_date") == day)
    assert d.height == n and d["is_dst_change_day"].all()
    assert df.filter(pl.col("is_dst_change_day"))["local_date"].n_unique() == 2
    # The repeated hour on the fall-back day still gives one row per UTC hour.
    assert df["period_end_utc"].is_unique().all()


def test_arizona_has_no_dst():
    df = f.add_calendar(_hours("AZ", datetime(2019, 1, 1, 8, tzinfo=UTC), 24 * 365), TZ)
    assert not df["is_dst"].any() and not df["is_dst_change_day"].any()
    assert df.group_by("local_date").len()["len"].unique().to_list() == [24]


def test_holiday_day_type():
    df = f.add_calendar(_hours("NY", datetime(2019, 11, 28, 6, tzinfo=UTC), 72), TZ)
    types = dict(df.group_by("local_date").agg(pl.col("day_type").first()).iter_rows())
    assert types[date(2019, 11, 28)] == "holiday_major"
    assert types[date(2019, 11, 29)] == "bridge"
    assert types[date(2019, 11, 30)] == "saturday"


def test_periods():
    df = pl.DataFrame(
        {
            "period_end_utc": [
                datetime(2015, 7, 1, tzinfo=UTC),
                datetime(2019, 12, 31, 23, tzinfo=UTC),
                datetime(2020, 3, 1, 0, tzinfo=UTC),  # ends the last February hour
                datetime(2020, 3, 1, 1, tzinfo=UTC),
                datetime(2022, 1, 1, 0, tzinfo=UTC),  # ends the last COVID hour
                datetime(2022, 1, 1, 1, tzinfo=UTC),
            ]
        }
    ).with_columns(pl.col("period_end_utc").dt.cast_time_unit("us"))
    p = f.add_periods(df)
    assert p["is_covid"].to_list() == [False, False, False, True, True, False]
    assert p["in_baseline"].to_list() == [False, True, False, False, False, False]
    assert p["trend_years"][0] == 0


# --------------------------------------------------------------------------- built table

needs_table = pytest.mark.skipif(not f.OUT_PATH.exists(), reason="run python -m glg.build.features")


@pytest.fixture(scope="module")
def table():
    return pl.read_parquet(f.OUT_PATH)


@needs_table
def test_one_row_per_ba_hour_and_every_modelled_ba(table):
    assert table.select("ba", "period_end_utc").is_duplicated().sum() == 0
    assert set(table["ba"].unique()) == set(f.modelled_bas())


@needs_table
def test_calendar_columns_complete(table):
    cols = ["local_date", "local_hour", "dow", "month", "day_type", "is_dst", "in_baseline"]
    assert table.select(cols).null_count().sum_horizontal().item() == 0
    assert set(table["local_hour"].unique()) == set(range(24))


@needs_table
def test_baseline_excludes_covid_and_post_2019(table):
    b = table.filter(pl.col("in_baseline"))
    assert not b["is_covid"].any()
    years = b["period_end_utc"].dt.year()
    assert years.min() >= 2016 and years.max() <= 2019


@needs_table
def test_no_leakage_of_later_data_into_baseline_rows():
    """Rebuilding the table from data cut at the end of 2019 gives identical baseline rows."""
    bas = ["DOM", "ERCO", "AZPS"]
    demand = f.load_demand(bas)
    weather = pl.read_parquet(f.w.OUT_PATH).filter(pl.col("ba").is_in(bas))
    emp = f.employment_index(bas)
    tz = f.time_zones(bas)
    cut = datetime(2020, 1, 1, tzinfo=UTC)
    full = f.build_table(demand, weather, emp, tz).filter(pl.col("in_baseline"))
    short = f.build_table(
        demand.filter(pl.col("period_end_utc") < cut),
        weather.filter(pl.col("period_end_utc") < cut),
        emp.filter(pl.col("month") < cut.date()),
        tz,
    ).filter(pl.col("in_baseline"))
    assert full.height == short.height > 0
    assert full.equals(short)
