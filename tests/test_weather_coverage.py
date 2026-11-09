"""Weather features: formulas, time alignment, coverage and plausibility of the built data."""

from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from glg.build import weather as w
from glg.build import weather_check as wc

# --------------------------------------------------------------------------- formulas


def test_relative_humidity():
    assert w.relative_humidity(np.array(20.0), np.array(20.0)) == pytest.approx(100)
    # 30 C air with a 20 C dewpoint is ~55% RH.
    assert w.relative_humidity(np.array(30.0), np.array(20.0)) == pytest.approx(55, abs=1)


@pytest.mark.parametrize(
    "t_f, rh, expected",
    [(90, 50, 95), (100, 50, 118), (96, 65, 121), (80, 40, 80), (50, 90, 50), (-10, 50, -10)],
)
def test_heat_index_matches_nws_table(t_f, rh, expected):
    # NWS heat index chart values (rounded); below 60 F the heat index is the air temperature.
    assert w.heat_index_f(np.array(float(t_f)), np.array(float(rh))) == pytest.approx(
        expected, abs=1.5
    )


def test_degree_hours():
    d = w.derive(np.array([w.f_to_c(75.0), w.f_to_c(50.0)]), np.array([10.0, 0.0]))
    assert d["cdh65f"] == pytest.approx([10, 0])
    assert d["hdh65f"] == pytest.approx([0, 15])
    assert d["cdh75f"] == pytest.approx([0, 0])
    assert d["hdh55f"] == pytest.approx([0, 5])


def _inst(values, ba="X", start=datetime(2020, 1, 1, tzinfo=UTC)):
    n = len(values)
    return pl.DataFrame(
        {
            "ba": [ba] * n,
            "valid_time_utc": [start + timedelta(hours=i) for i in range(n)],
            **{f: [float(v) for v in values] for f in w.FEATURES},
        }
    )


def test_hour_ending_averages_the_two_instants():
    he = w.hour_ending(_inst([0, 2, 4, 10]))
    assert he["t2m_c"].to_list() == [1, 3, 7]
    # The value for 01:00-02:00 is labelled by its end, 02:00 UTC.
    assert he["period_end_utc"][1] == datetime(2020, 1, 1, 2, tzinfo=UTC)


def test_inertia_features_and_gaps():
    a = _inst(range(100))
    gap_start = datetime(2020, 1, 1, tzinfo=UTC) + timedelta(hours=200)
    df = pl.concat([a, _inst(range(30), start=gap_start)])  # a BA absent for 100 hours
    df = w.add_inertia(df.rename({"valid_time_utc": "period_end_utc"}))
    first = df.filter(pl.col("period_end_utc") < gap_start)
    assert first["t2m_c_mean24"][22] is None and first["t2m_c_mean24"][23] == pytest.approx(11.5)
    assert first["t2m_c_mean72"][71] == pytest.approx(35.5)
    assert first["t2m_c_lag24"][30] == 6
    after = df.filter(pl.col("period_end_utc") >= gap_start)
    # No window or lag reaches back across the gap.
    assert after["t2m_c_mean24"][:23].null_count() == 23
    assert after["t2m_c_lag24"][:24].null_count() == 24


# --------------------------------------------------------------------------- built data

needs_weather = pytest.mark.skipif(
    not w.OUT_PATH.exists(), reason="run python -m glg.build.weather first"
)


@pytest.fixture(scope="module")
def weather():
    return pl.read_parquet(w.OUT_PATH)


@pytest.fixture(scope="module")
def shortlist():
    s = pl.read_csv(w.bwm.REFERENCE_DIR / "ba_shortlist.csv")
    return s.filter(pl.col("include") != "no")["series"].to_list()


@needs_weather
def test_grid_weights_sum_to_one_per_county():
    cw = pl.read_parquet(w.CELL_WEIGHTS_PATH)
    s = cw.group_by("county_fips").agg(pl.col("weight").sum())
    assert s.height > 3100
    assert s["weight"].min() == pytest.approx(1) and s["weight"].max() == pytest.approx(1)


@needs_weather
def test_one_row_per_ba_hour(weather):
    assert weather.select("ba", "period_end_utc").is_duplicated().sum() == 0


@needs_weather
def test_coverage_of_demand_hours(weather, shortlist):
    """>= 95% of each shortlisted BA's clean demand hours have weather, every full year."""
    last = weather["period_end_utc"].max()
    d = pl.read_parquet(
        w.PROCESSED_DIR / "demand_clean.parquet", columns=["ba", "period_end_utc", "demand_clean"]
    ).filter(
        pl.col("ba").is_in(shortlist),
        pl.col("demand_clean").is_not_null(),
        pl.col("period_end_utc") <= last,
    )
    dom = (
        pl.read_parquet(
            w.PROCESSED_DIR / "subregion_demand_clean.parquet",
            columns=["subregion", "period_end_utc", "demand_clean"],
        )
        .filter(pl.col("subregion") == "DOM", pl.col("demand_clean").is_not_null())
        .filter(pl.col("period_end_utc") <= last)
        .rename({"subregion": "ba"})
    )  # DOM's demand is a PJM subregion in EIA-930
    d = pl.concat([d, dom]).with_columns(pl.col("period_end_utc").dt.cast_time_unit("us"))
    assert "DOM" in d["ba"].unique()
    cov = (
        d.join(
            weather.select("ba", "period_end_utc", "t2m_c"), on=["ba", "period_end_utc"], how="left"
        )
        .group_by("ba", pl.col("period_end_utc").dt.year().alias("year"))
        .agg(pl.col("t2m_c").is_not_null().mean().alias("cov"))
    )
    bad = cov.filter(pl.col("cov") < 0.95)
    assert bad.is_empty(), bad
    assert set(shortlist) <= set(weather["ba"].unique())


@needs_weather
def test_plausible_ranges(weather):
    assert weather["t2m_c"].min() > -45 and weather["t2m_c"].max() < 50
    assert weather.filter(pl.col("d2m_c") > pl.col("t2m_c") + 0.5).is_empty()
    assert weather["rh_pct"].min() >= 0 and weather["rh_pct"].max() <= 100
    for c in [f"cdh{b}f" for b in w.BASES_F] + [f"hdh{b}f" for b in w.BASES_F]:
        assert weather[c].min() >= 0
    # In very dry desert heat the NWS heat index sits several degrees below the air
    # temperature (110 F at 5% RH -> ~101 F); never more than that.
    assert (weather["hi_c"] - weather["t2m_c"]).min() > -8
    assert weather.select(w.FEATURES).null_count().sum_horizontal().item() == 0


@needs_weather
def test_climate_sanity(weather):
    """Hot BAs are hot, cold ones cold (annual mean deg C, any full year)."""
    m = (
        weather.filter(pl.col("period_end_utc").dt.year() == 2019)
        .group_by("ba")
        .agg(pl.col("t2m_c").mean())
    )
    t = dict(m.iter_rows())
    assert t["SRP"] > t["FPL"] > t["ERCO"] > t["DOM"] > t["ISNE"] or t["FPL"] > t["SRP"]
    assert 20 < t["FPL"] < 27 and 5 < t["ISNE"] < 12 and 18 < t["ERCO"] < 23


needs_checks = pytest.mark.skipif(
    not wc.STATION_CHECK_PATH.exists(), reason="run python -m glg.build.weather_check first"
)


@needs_checks
def test_era5_tracks_airport_stations():
    s = pl.read_csv(wc.STATION_CHECK_PATH, schema_overrides={"station": pl.Utf8})
    main = s.filter(pl.col("station").is_in(list(w_stations())))
    assert main["r"].min() > 0.95
    assert main["bias_c"].abs().max() < 3
    assert main["rmse_c"].max() < 3


def w_stations():
    from glg.ingest import isd

    return isd.STATIONS


@needs_checks
def test_no_era5_drift_outside_phoenix():
    """ERA5-minus-station bias stays put between the baseline and 2023-25 (DATA.md)."""
    s = pl.read_csv(wc.STATION_CHECK_PATH, schema_overrides={"station": pl.Utf8})
    shift = (
        s.with_columns(
            pl.when(pl.col("year") <= 2019)
            .then(pl.lit("base"))
            .when(pl.col("year") >= 2023)
            .then(pl.lit("late"))
            .alias("p")
        )
        .drop_nulls("p")
        .group_by("airport", "ba", "p")
        .agg(pl.col("bias_c").mean())
        .pivot(on="p", index=["airport", "ba"], values="bias_c")
        .drop_nulls()
        .with_columns((pl.col("late") - pl.col("base")).alias("shift"))
    )
    outside = shift.filter(pl.col("ba") != "SRP")
    assert outside["shift"].abs().max() < 0.5, outside
    # Phoenix: Sky Harbor's shift is mostly local, but the area median is about -0.3 C:
    # a known caveat for AZPS/SRP growth (robustness check in Prompt 15).
    phx = shift.filter(pl.col("ba") == "SRP")
    assert -0.5 < phx["shift"].median() < 0.1


@needs_checks
def test_mapping_choice_does_not_move_the_weather_series():
    m = pl.read_csv(wc.SENSITIVITY_PATH)
    assert m["r"].min() > 0.995
    # A level offset is absorbed by a per-BA model; it must not change over time.
    drift = m.group_by("ba").agg(
        (pl.col("mean_diff_c").max() - pl.col("mean_diff_c").min()).alias("d")
    )
    assert drift["d"].max() < 0.35, drift.sort("d").tail(3)
