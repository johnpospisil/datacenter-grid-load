"""Calendar features and the per-BA modelling table.

One row per modelled BA x hour (hour-ending UTC, EIA-930 convention), with:
- demand: demand_raw, demand_clean, flag_any (EIA-930; DOM from its PJM subregion, D3)
- weather: every column of data/processed/weather_ba_hourly.parquet (Prompt 5)
- calendar (local civil time of the hour's *start*, so local_hour 0 = 00:00-01:00):
  local_hour, dow, month, doy, is_weekend, holiday flags and day_type, DST flags
- periods: is_covid, in_baseline, trend_years (diagnostics only; never in the baseline model)
- economy (optional control): emp_index, BA-weighted state nonfarm employment, 2019 = 100

Nothing here is fitted or scaled on the data, so no feature can carry post-baseline
information into the baseline rows (tested by rebuilding on a truncated sample).

Run: python -m glg.build.features
"""

import logging
from datetime import date

import holidays
import polars as pl

from glg.build import ba_weather_map as bwm
from glg.build import weather as w
from glg.config import BASELINE_YEARS, COVID_END, COVID_START, PROCESSED_DIR, REFERENCE_DIR
from glg.ingest import employment

log = logging.getLogger(__name__)

OUT_PATH = PROCESSED_DIR / "model_table.parquet"
TREND_ORIGIN = date(2015, 7, 1)  # EIA-930 start
DOM_TZ = "America/New_York"

MAJOR_HOLIDAYS = {
    "New Year's Day",
    "Memorial Day",
    "Independence Day",
    "Labor Day",
    "Thanksgiving Day",
    "Christmas Day",
}
MINOR_HOLIDAYS = {
    "Martin Luther King Jr. Day",
    "Washington's Birthday",
    "Juneteenth National Independence Day",
    "Columbus Day",
    "Veterans Day",
}


# --------------------------------------------------------------------------- calendar


def holiday_table(years: range) -> pl.DataFrame:
    """One row per local date with a holiday or bridge-day label.

    Federal holidays count on the actual date and on the observed weekday. Bridge days:
    the Friday after Thanksgiving, Dec 24, and weekdays Dec 26-31 that aren't holidays.
    """
    rows: dict[date, tuple[str, str]] = {}
    for d, name in holidays.US(years=list(years), observed=True).items():
        base = name.replace(" (observed)", "")
        if base in MAJOR_HOLIDAYS:
            kind = "holiday_major"
        elif base in MINOR_HOLIDAYS:
            kind = "holiday_minor"
        else:
            raise ValueError(f"unclassified holiday: {name}")
        if d not in rows or kind == "holiday_major":
            rows[d] = (kind, base)
    for y in years:
        thanks = next(d for d, (k, n) in rows.items() if n == "Thanksgiving Day" and d.year == y)
        bridges = [date.fromordinal(thanks.toordinal() + 1), date(y, 12, 24)]
        bridges += [date(y, 12, d) for d in range(26, 32)]
        for d in bridges:
            if d not in rows and d.weekday() < 5:
                rows[d] = (
                    "bridge",
                    "Christmas week" if d.month == 12 else "Day after Thanksgiving",
                )
    return pl.DataFrame(
        {
            "local_date": list(rows),
            "holiday_kind": [k for k, _ in rows.values()],
            "holiday_name": [n for _, n in rows.values()],
        }
    ).sort("local_date")


def add_calendar(df: pl.DataFrame, tz_by_ba: dict[str, str]) -> pl.DataFrame:
    """Local-time calendar columns. Local time is that of the hour's start."""
    parts = []
    df = df.with_columns(pl.col("ba").replace_strict(tz_by_ba).alias("_tz"))
    for (tz,), g in df.group_by("_tz"):
        start = (pl.col("period_end_utc") - pl.duration(hours=1)).dt.convert_time_zone(tz)
        g = g.drop("_tz").with_columns(
            start.dt.date().alias("local_date"),
            start.dt.hour().cast(pl.Int8).alias("local_hour"),
            # DST: the zone is on daylight time (its DST offset is non-zero).
            (start.dt.dst_offset().dt.total_seconds() != 0).alias("is_dst"),
            pl.lit(tz).alias("tz"),
        )
        # DST change days have 23 or 25 local hours: measure midnight to midnight in the zone
        # (not by counting rows, which would also flag partial days at the edges of the data).
        midnight = pl.col("local_date").cast(pl.Datetime("us")).dt.replace_time_zone(tz)
        next_midnight = (pl.col("local_date") + pl.duration(days=1)).cast(pl.Datetime("us"))
        day_hours = (next_midnight.dt.replace_time_zone(tz) - midnight).dt.total_hours()
        g = g.with_columns((day_hours != 24).alias("is_dst_change_day"))
        parts.append(g)
    df = pl.concat(parts)
    years = range(df["local_date"].min().year - 1, df["local_date"].max().year + 2)
    hol = holiday_table(years)
    df = df.join(hol, on="local_date", how="left")
    d = pl.col("local_date")
    return (
        df.with_columns(
            d.dt.weekday().sub(1).cast(pl.Int8).alias("dow"),  # 0 = Monday
            d.dt.month().cast(pl.Int8).alias("month"),
            d.dt.ordinal_day().cast(pl.Int16).alias("doy"),
            (d.dt.weekday() >= 6).alias("is_weekend"),
            pl.col("holiday_kind").is_in(["holiday_major", "holiday_minor"]).alias("is_holiday"),
            (pl.col("holiday_kind") == "bridge").fill_null(False).alias("is_bridge"),
            pl.when(pl.col("holiday_kind").is_not_null())
            .then(pl.col("holiday_kind"))
            .when(d.dt.weekday() == 6)
            .then(pl.lit("saturday"))
            .when(d.dt.weekday() == 7)
            .then(pl.lit("sunday"))
            .otherwise(pl.lit("weekday"))
            .alias("day_type"),
        )
        .with_columns(pl.col("is_holiday").fill_null(False))
        .drop("holiday_kind")
    )


def add_periods(df: pl.DataFrame) -> pl.DataFrame:
    t = pl.col("period_end_utc")
    covid_start = pl.lit(COVID_START).str.to_datetime(time_zone="UTC")
    covid_end = pl.lit(COVID_END).str.to_datetime(time_zone="UTC") + pl.duration(days=1)
    is_covid = (t > covid_start) & (t <= covid_end)
    origin = pl.lit(TREND_ORIGIN).cast(pl.Datetime("us", "UTC"))
    return df.with_columns(
        is_covid.alias("is_covid"),
        (t.dt.year().is_between(*BASELINE_YEARS) & ~is_covid).alias("in_baseline"),
        ((t - origin).dt.total_hours() / (24 * 365.25)).cast(pl.Float32).alias("trend_years"),
    )


# --------------------------------------------------------------------------- economy


def employment_index(bas: list[str]) -> pl.DataFrame:
    """BA x month: population-weighted state nonfarm employment, 2019 average = 100.

    State weights = the BA's 2020 population in each state, from the 2019 BA -> county map
    (a fixed footprint, so the index can't move because the map does).
    """
    m = pl.read_csv(bwm.MAP_PATH, schema_overrides={"county_fips": pl.Utf8}).filter(
        pl.col("year") == 2019, pl.col("ba").is_in(bas)
    )
    pop = pl.read_csv(bwm.POP_PATH, schema_overrides={"county_fips": pl.Utf8}).filter(
        pl.col("year") == w.POP_YEAR
    )
    sw = (
        m.join(pop.select("county_fips", "population"), on="county_fips")
        .group_by("ba", "state")
        .agg((pl.col("weight") * pl.col("population")).sum().alias("p"))
        .with_columns((pl.col("p") / pl.col("p").sum().over("ba")).alias("state_weight"))
    )
    emp = employment.load()
    base = (
        emp.filter(pl.col("month").dt.year() == 2019)
        .group_by("state")
        .agg(pl.col("emp_thousands").mean().alias("base"))
    )
    emp = emp.join(base, on="state").with_columns(
        (100 * pl.col("emp_thousands") / pl.col("base")).alias("idx")
    )
    out = (
        sw.join(emp, on="state")
        .group_by("ba", "month")
        .agg((pl.col("idx") * pl.col("state_weight")).sum().alias("emp_index"))
    )
    # Only keep months with every state of the BA reported.
    full = sw.group_by("ba").agg(pl.len().alias("n_states"))
    have = sw.join(emp, on="state").group_by("ba", "month").agg(pl.len().alias("n"))
    ok = have.join(full, on="ba").filter(pl.col("n") == pl.col("n_states")).select("ba", "month")
    return out.join(ok, on=["ba", "month"]).with_columns(pl.col("emp_index").cast(pl.Float32))


# --------------------------------------------------------------------------- demand


def modelled_bas() -> list[str]:
    s = pl.read_csv(REFERENCE_DIR / "ba_shortlist.csv").filter(pl.col("include") != "no")
    return sorted(s["series"].to_list())


def load_demand(bas: list[str]) -> pl.DataFrame:
    cols = ["ba", "period_end_utc", "demand_raw", "demand_clean", "flag_any"]
    d = pl.read_parquet(PROCESSED_DIR / "demand_clean.parquet", columns=cols).filter(
        pl.col("ba").is_in(bas)
    )
    parts = [d]
    if "DOM" in bas:  # EIA-930 publishes Dominion as a PJM subregion (decision D3)
        sub = pl.read_parquet(
            PROCESSED_DIR / "subregion_demand_clean.parquet",
            columns=["subregion", "period_end_utc", "demand_raw", "demand_clean", "flag_any"],
        )
        parts.append(sub.filter(pl.col("subregion") == "DOM").rename({"subregion": "ba"}))
    return pl.concat(
        [p.with_columns(pl.col("period_end_utc").dt.cast_time_unit("us")) for p in parts]
    )


def time_zones(bas: list[str]) -> dict[str, str]:
    meta = pl.read_csv(REFERENCE_DIR / "ba_metadata.csv")
    tz = dict(meta.select("ba", "tz_civil").iter_rows())
    out = {b: tz.get(b) for b in bas}
    out["DOM"] = DOM_TZ
    missing = [b for b, z in out.items() if not z]
    if missing:
        raise ValueError(f"no time zone for {missing}")
    return out


# --------------------------------------------------------------------------- build


def build_table(
    demand: pl.DataFrame, weather: pl.DataFrame, emp: pl.DataFrame, tz_by_ba: dict[str, str]
) -> pl.DataFrame:
    df = demand.join(weather, on=["ba", "period_end_utc"], how="left")
    df = add_calendar(df, tz_by_ba)
    df = add_periods(df)
    df = df.with_columns(pl.col("period_end_utc").dt.truncate("1mo").dt.date().alias("_month"))
    df = df.join(emp.rename({"month": "_month"}), on=["ba", "_month"], how="left").drop("_month")
    first = ["ba", "period_end_utc", "local_date", "local_hour", "tz"]
    return df.select(first + [c for c in df.columns if c not in first]).sort("ba", "period_end_utc")


def build() -> pl.DataFrame:
    bas = modelled_bas()
    demand = load_demand(bas)
    weather = pl.read_parquet(w.OUT_PATH).filter(pl.col("ba").is_in(bas))
    emp = employment_index(bas)
    df = build_table(demand, weather, emp, time_zones(bas))
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    df.write_parquet(OUT_PATH, compression="zstd")
    log.info(
        "Wrote %s: %d rows, %d BAs, %d columns; %.1f%% of rows have weather",
        OUT_PATH.name,
        df.height,
        df["ba"].n_unique(),
        df.width,
        100 * df["t2m_c"].is_not_null().mean(),
    )
    return df


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    build()


if __name__ == "__main__":
    main()
