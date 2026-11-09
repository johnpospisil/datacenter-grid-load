"""Sanity checks on the ERA5 weather features (Prompt 5).

1. station_check: ERA5 county temperature vs NOAA ISD airport observations, per station-year
   (bias, RMSE, correlation), to catch errors and drift.
2. mapping_sensitivity: BA temperature under the raked BA -> county weights vs a crude
   "whole county" alternative (weight 1 for every county a BA touches), to bound how much
   the mapping's known weaknesses (slivers) move the weather series.

Run: python -m glg.build.weather_check  -> data/outputs/weather_station_check.csv,
                                           data/outputs/weather_mapping_sensitivity.csv
"""

import logging

import numpy as np
import polars as pl

from glg.build import ba_weather_map as bwm
from glg.build import weather as w
from glg.config import OUTPUTS_DIR
from glg.ingest import isd

log = logging.getLogger(__name__)

STATION_CHECK_PATH = OUTPUTS_DIR / "weather_station_check.csv"
SENSITIVITY_PATH = OUTPUTS_DIR / "weather_mapping_sensitivity.csv"
SENSITIVITY_YEARS = (2019, 2024)


def county_series(fips: str) -> pl.DataFrame:
    return pl.read_parquet(w.COUNTY_DIR / "*.parquet").filter(pl.col("county_fips") == fips)


def station_check() -> pl.DataFrame:
    """ERA5 (county containing the airport) minus station, per station-year."""
    stations = {s: (name, ba, fips) for s, (name, ba, fips) in isd.STATIONS.items()}
    stations |= {s: (name, "SRP", "04013") for s, name in isd.PHOENIX_CHECK.items()}
    rows = []
    county_cache: dict[str, pl.DataFrame] = {}
    for s, (name, ba, fips) in stations.items():
        c = county_cache.setdefault(fips, county_series(fips))
        for y in isd.YEARS:
            if not isd.path(s, y).exists():
                continue
            st = isd.read_hourly(s, y)
            j = st.join(c, left_on="time_utc", right_on="valid_time_utc").drop_nulls("tmp_c")
            if j.height < 5000:  # too sparse a year to compare
                continue
            e = j["t2m_c"] - j["tmp_c"]
            summer = j.filter(pl.col("time_utc").dt.month().is_in([6, 7, 8]))
            rows.append(
                {
                    "station": s,
                    "airport": name,
                    "ba": ba,
                    "county_fips": fips,
                    "year": y,
                    "n_hours": j.height,
                    "bias_c": e.mean(),
                    "summer_bias_c": (summer["t2m_c"] - summer["tmp_c"]).mean(),
                    "rmse_c": float(np.sqrt((e**2).mean())),
                    "r": j.select(pl.corr("t2m_c", "tmp_c")).item(),
                    "dew_bias_c": (j["d2m_c"] - j["dew_c"]).mean(),
                }
            )
    return pl.DataFrame(rows).with_columns(pl.col(pl.Float64).round(3))


def _ba_temp(weights: pl.DataFrame, county: pl.DataFrame, ba: str) -> pl.DataFrame:
    wb = weights.filter(pl.col("ba") == ba).select("county_fips", "pop_weight")
    return (
        county.join(wb, on="county_fips")
        .group_by("valid_time_utc")
        .agg(
            (pl.col("t2m_c") * pl.col("pop_weight")).sum().alias("t"),
            (
                pl.col("t2m_c").map_batches(lambda t: np.maximum(t * 9 / 5 + 32 - 65, 0))
                * pl.col("pop_weight")
            )
            .sum()
            .alias("cdh65f"),
        )
        .sort("valid_time_utc")
    )


def mapping_sensitivity(bas: list[str]) -> pl.DataFrame:
    """Raked weights vs whole-county weights: difference in BA temperature and CDH."""
    m = pl.read_csv(bwm.MAP_PATH, schema_overrides={"county_fips": pl.Utf8})
    pop = pl.read_csv(bwm.POP_PATH, schema_overrides={"county_fips": pl.Utf8})
    rows = []
    for y in SENSITIVITY_YEARS:
        my = m.filter(pl.col("year") == y)
        main = bwm.population_weights(my, pop, pop_year=w.POP_YEAR)
        crude = bwm.population_weights(
            my.with_columns(pl.lit(1.0).alias("weight")), pop, w.POP_YEAR
        )
        county = pl.read_parquet(w.COUNTY_DIR / f"{y}-*.parquet")
        for ba in bas:
            a, b = _ba_temp(main, county, ba), _ba_temp(crude, county, ba)
            j = a.join(b, on="valid_time_utc", suffix="_crude")
            d = j["t_crude"] - j["t"]
            rows.append(
                {
                    "ba": ba,
                    "year": y,
                    "mean_diff_c": d.mean(),
                    "rms_diff_c": float(np.sqrt((d**2).mean())),
                    "r": j.select(pl.corr("t", "t_crude")).item(),
                    "cdh65f_main": j["cdh65f"].sum(),
                    "cdh65f_crude": j["cdh65f_crude"].sum(),
                }
            )
    return pl.DataFrame(rows).with_columns(
        ((pl.col("cdh65f_crude") / pl.col("cdh65f_main") - 1) * 100).alias("cdh65f_diff_pct"),
        pl.col(pl.Float64).round(3),
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    sc = station_check()
    sc.write_csv(STATION_CHECK_PATH)
    shortlist = pl.read_csv(bwm.REFERENCE_DIR / "ba_shortlist.csv").filter(
        pl.col("include") != "no"
    )
    ms = mapping_sensitivity(sorted(shortlist["series"].to_list()))
    ms.write_csv(SENSITIVITY_PATH)
    log.info(
        "Wrote %s (%d rows) and %s (%d rows)",
        STATION_CHECK_PATH.name,
        sc.height,
        SENSITIVITY_PATH.name,
        ms.height,
    )


if __name__ == "__main__":
    main()
