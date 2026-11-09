"""ERA5 grid -> county x hour -> BA x hour weather features.

Steps (DATA.md, "Weather features"):

1. Grid -> county weights: each ERA5 point is the centre of a 0.25 deg cell. A county's
   value is the mean of the cells it overlaps, weighted by overlap area (equal-area CRS).
2. County x hour: per month, t2m and d2m in deg C -> data/interim/weather_county/YYYY-MM.parquet.
3. County-level derived features (non-linear ones must be computed before averaging):
   relative humidity, NWS heat index, cooling/heating degree-hours at 55/65/75 F.
4. BA x hour: population-weighted mean over counties, using the BA -> county map of each
   year (2015-2025; later years reuse 2025) and fixed 2020 population (decision D8).
5. Hour-ending: ERA5 values are instantaneous at HH:00 UTC; EIA-930 hours are labelled by
   their end. The value for the hour ending at t is the mean of the instants t-1h and t.
6. Thermal inertia: trailing 24 h and 72 h mean temperature, and temperature 24 h earlier.

Run: python -m glg.build.weather
"""

import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import polars as pl
import xarray as xr
from scipy import sparse
from shapely.geometry import box

from glg.build import ba_weather_map as bwm
from glg.config import INTERIM_DIR, PROCESSED_DIR, RAW_WEATHER_DIR
from glg.ingest import weather as era5

log = logging.getLogger(__name__)

CELL = 0.25  # ERA5 grid spacing, degrees
EQUAL_AREA = 5070  # NAD83 / CONUS Albers
NON_CONUS_STATES = ("02", "15", "60", "66", "69", "72", "78")
POP_YEAR = 2020  # decision D8
MAP_YEARS = (min(bwm.YEARS), max(bwm.YEARS))
BASES_F = (55, 65, 75)
HI_MIN_F = 60.0

COUNTY_DIR = INTERIM_DIR / "weather_county"
CELL_WEIGHTS_PATH = INTERIM_DIR / "era5_county_cells.parquet"
BA_INSTANT_DIR = INTERIM_DIR / "weather_ba_instant"
OUT_PATH = PROCESSED_DIR / "weather_ba_hourly.parquet"

FEATURES = (
    ["t2m_c", "d2m_c", "rh_pct", "hi_c"]
    + [f"cdh{b}f" for b in BASES_F]
    + [f"hdh{b}f" for b in BASES_F]
)


# --------------------------------------------------------------------------- grid -> county


def grid_coords(path: Path) -> tuple[np.ndarray, np.ndarray]:
    with xr.open_dataset(path) as ds:
        return ds.latitude.values.copy(), ds.longitude.values.copy()


def cell_county_weights(lats: np.ndarray, lons: np.ndarray) -> pl.DataFrame:
    """Overlap area of each ERA5 cell with each CONUS county, as a share of the county.

    Cell index = i_lat * len(lons) + i_lon, matching a (lat, lon) array flattened in C order.
    """
    cells = gpd.GeoDataFrame(
        {
            "cell": np.arange(lats.size * lons.size),
            "geometry": [
                box(lon - CELL / 2, lat - CELL / 2, lon + CELL / 2, lat + CELL / 2)
                for lat in lats
                for lon in lons
            ],
        },
        crs=4269,
    ).to_crs(EQUAL_AREA)
    counties = bwm.county_shapes()
    counties = counties[~counties.county_fips.str[:2].isin(NON_CONUS_STATES)].to_crs(EQUAL_AREA)
    ov = gpd.overlay(counties[["county_fips", "geometry"]], cells, how="intersection")
    ov["area"] = ov.geometry.area
    w = pl.DataFrame(
        {"county_fips": ov.county_fips.values, "cell": ov.cell.values, "area": ov.area.values}
    )
    w = w.with_columns((pl.col("area") / pl.col("area").sum().over("county_fips")).alias("weight"))
    missing = set(counties.county_fips) - set(w["county_fips"])
    if missing:
        raise ValueError(f"{len(missing)} CONUS counties have no ERA5 cells: {sorted(missing)[:5]}")
    return w.select("county_fips", "cell", "weight").sort("county_fips", "cell")


def load_cell_weights(sample: Path) -> pl.DataFrame:
    if CELL_WEIGHTS_PATH.exists():
        return pl.read_parquet(CELL_WEIGHTS_PATH)
    lats, lons = grid_coords(sample)
    w = cell_county_weights(lats, lons)
    CELL_WEIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    w.write_parquet(CELL_WEIGHTS_PATH)
    log.info(
        "Grid -> county weights: %d counties, %d county-cell pairs",
        w["county_fips"].n_unique(),
        w.height,
    )
    return w


def weight_matrix(w: pl.DataFrame, n_cells: int) -> tuple[sparse.csr_matrix, list[str]]:
    fips = w["county_fips"].unique().sort().to_list()
    idx = {f: i for i, f in enumerate(fips)}
    rows = np.array([idx[f] for f in w["county_fips"]])
    m = sparse.csr_matrix(
        (w["weight"].to_numpy(), (rows, w["cell"].to_numpy())), shape=(len(fips), n_cells)
    )
    return m, fips


def county_month(path: Path, wmat: sparse.csr_matrix, fips: list[str]) -> pl.DataFrame:
    """One month of hourly county t2m/d2m (deg C), long format."""
    with xr.open_dataset(path) as ds:
        times = ds.valid_time.values
        out = {}
        for var in ("t2m", "d2m"):
            a = ds[var].values.reshape(len(times), -1).astype(np.float64) - 273.15
            if np.isnan(a).any():
                raise ValueError(f"{path.name}: NaN in {var}")
            out[var] = (wmat @ a.T).astype(np.float32)  # counties x hours
    n_c, n_t = len(fips), len(times)
    return pl.DataFrame(
        {
            "county_fips": np.repeat(np.array(fips), n_t),
            "valid_time_utc": np.tile(times, n_c),
            "t2m_c": out["t2m"].ravel(),
            "d2m_c": out["d2m"].ravel(),
        }
    ).with_columns(pl.col("valid_time_utc").dt.cast_time_unit("us").dt.replace_time_zone("UTC"))


# --------------------------------------------------------------------------- derived features


def relative_humidity(t_c: np.ndarray, td_c: np.ndarray) -> np.ndarray:
    """Relative humidity (%) from temperature and dewpoint (Magnus, Alduchov & Eskridge 1996)."""
    a, b = 17.625, 243.04
    rh = 100 * np.exp(a * td_c / (b + td_c) - a * t_c / (b + t_c))
    return np.clip(rh, 0, 100)


def heat_index_f(t_f: np.ndarray, rh: np.ndarray) -> np.ndarray:
    """NWS heat index (deg F): Steadman's simple form, Rothfusz regression when >= 80 F.

    Follows the NWS algorithm (wpc.ncep.noaa.gov/html/heatindex_equation.shtml), including
    its low- and high-humidity adjustments. Below HI_MIN_F it is the air temperature.
    """
    simple = 0.5 * (t_f + 61.0 + (t_f - 68.0) * 1.2 + rh * 0.094)
    hi = (simple + t_f) / 2
    t, r = t_f, rh
    full = (
        -42.379
        + 2.04901523 * t
        + 10.14333127 * r
        - 0.22475541 * t * r
        - 0.00683783 * t * t
        - 0.05481717 * r * r
        + 0.00122874 * t * t * r
        + 0.00085282 * t * r * r
        - 0.00000199 * t * t * r * r
    )
    with np.errstate(invalid="ignore"):
        low = (r < 13) & (t >= 80) & (t <= 112)
        full = np.where(low, full - ((13 - r) / 4) * np.sqrt((17 - np.abs(t - 95)) / 17), full)
    high = (r > 85) & (t >= 80) & (t <= 87)
    full = np.where(high, full + ((r - 85) / 10) * ((87 - t) / 5), full)
    nws = np.where(hi >= 80, full, simple)
    # The heat index is a warm-weather measure; Steadman's simple form goes wrong in the cold
    # (it gives -44 C at -38 C). Below HI_MIN_F use the air temperature (a step of <= ~4 F).
    return np.where(t_f >= HI_MIN_F, nws, t_f)


def f_to_c(f):
    return (f - 32) * 5 / 9


def c_to_f(c):
    return c * 9 / 5 + 32


def derive(t_c: np.ndarray, td_c: np.ndarray) -> dict[str, np.ndarray]:
    t_f = c_to_f(t_c)
    rh = relative_humidity(t_c, td_c)
    out = {"t2m_c": t_c, "d2m_c": td_c, "rh_pct": rh, "hi_c": f_to_c(heat_index_f(t_f, rh))}
    for b in BASES_F:
        out[f"cdh{b}f"] = np.maximum(t_f - b, 0)
        out[f"hdh{b}f"] = np.maximum(b - t_f, 0)
    return out


# --------------------------------------------------------------------------- county -> BA


def ba_matrices(fips: list[str]) -> dict[int, tuple[np.ndarray, list[str]]]:
    """Per map year: (BA x county) population-weight matrix over the given county order."""
    m = pl.read_csv(bwm.MAP_PATH, schema_overrides={"county_fips": pl.Utf8})
    pop = pl.read_csv(bwm.POP_PATH, schema_overrides={"county_fips": pl.Utf8})
    m = m.filter(pl.col("county_fips").is_in(fips))  # CONUS only
    pw = bwm.population_weights(m, pop, pop_year=POP_YEAR).filter(pl.col("pop_weight") > 0)
    idx = {f: i for i, f in enumerate(fips)}
    out = {}
    for (year,), g in pw.group_by("year"):
        bas = sorted(g["ba"].unique().to_list())
        bi = {b: i for i, b in enumerate(bas)}
        mat = np.zeros((len(bas), len(fips)))
        mat[[bi[b] for b in g["ba"]], [idx[f] for f in g["county_fips"]]] = g[
            "pop_weight"
        ].to_numpy()
        out[year] = (mat, bas)
    return out


def ba_month(
    county: pl.DataFrame, fips: list[str], mat: np.ndarray, bas: list[str]
) -> pl.DataFrame:
    """Population-weighted BA means of every feature, for one month of county data."""
    times = county.filter(pl.col("county_fips") == fips[0])["valid_time_utc"]
    n_t = times.len()
    t = county["t2m_c"].to_numpy().reshape(len(fips), n_t).astype(np.float64)
    td = county["d2m_c"].to_numpy().reshape(len(fips), n_t).astype(np.float64)
    feats = derive(t, td)
    cols = {
        "ba": np.repeat(np.array(bas), n_t),
        "valid_time_utc": np.tile(times.to_numpy(), len(bas)),
    }
    for k, v in feats.items():
        cols[k] = (mat @ v).ravel().astype(np.float32)
    return pl.DataFrame(cols).with_columns(
        pl.col("valid_time_utc").dt.cast_time_unit("us").dt.replace_time_zone("UTC")
    )


# --------------------------------------------------------------------------- hour-ending, lags


def hour_ending(inst: pl.DataFrame) -> pl.DataFrame:
    """Mean of the instants at t-1h and t, labelled period_end_utc = t (EIA-930 convention).

    The first hour of each BA has no earlier instant and is dropped.
    """
    inst = inst.with_columns(pl.col("valid_time_utc").dt.cast_time_unit("us")).sort(
        "ba", "valid_time_utc"
    )
    prev_ok = (
        pl.col("valid_time_utc") - pl.col("valid_time_utc").shift(1).over("ba")
    ) == pl.duration(hours=1)
    return (
        inst.with_columns(
            [((pl.col(f) + pl.col(f).shift(1).over("ba")) / 2).alias(f) for f in FEATURES]
        )
        .filter(prev_ok)
        .rename({"valid_time_utc": "period_end_utc"})
    )


def add_inertia(df: pl.DataFrame) -> pl.DataFrame:
    """Trailing 24 h / 72 h mean temperature and temperature 24 h earlier.

    Time-based, so a BA that is absent for a while (no map rows in some years) never
    averages across the gap: a window must hold all 24 / 72 hours, else it is null.
    """
    df = df.sort("ba", "period_end_utc").with_columns(pl.lit(1, pl.Int32).alias("_one"))
    t, by = pl.col("t2m_c"), "period_end_utc"
    roll = {}
    for h in (24, 72):
        n = pl.col("_one").rolling_sum_by(by, window_size=f"{h}h").over("ba")
        mean = t.rolling_mean_by(by, window_size=f"{h}h").over("ba")
        roll[f"t2m_c_mean{h}"] = pl.when(n == h).then(mean)
    df = df.with_columns(**roll).drop("_one")
    lag = df.select("ba", (pl.col(by) + pl.duration(hours=24)).alias(by), t.alias("t2m_c_lag24"))
    return df.join(lag, on=["ba", by], how="left")


# --------------------------------------------------------------------------- driver


def month_files() -> list[tuple[int, int, Path]]:
    out = []
    for p in sorted(RAW_WEATHER_DIR.glob("era5_t2m_d2m_????-??.nc")):
        y, m = map(int, p.stem.rsplit("_", 1)[1].split("-"))
        out.append((y, m, p))
    return out


def build(refresh: bool = False) -> pl.DataFrame:
    files = month_files()
    if not files:
        raise FileNotFoundError(f"no ERA5 files in {RAW_WEATHER_DIR}")
    expected = era5.months((files[0][0], files[0][1]), (files[-1][0], files[-1][1]))
    if [(y, m) for y, m, _ in files] != expected:
        raise ValueError("ERA5 months are not contiguous; finish the download first")

    cw = load_cell_weights(files[0][2])
    lats, lons = grid_coords(files[0][2])
    wmat, fips = weight_matrix(cw, lats.size * lons.size)
    mats = ba_matrices(fips)
    COUNTY_DIR.mkdir(parents=True, exist_ok=True)
    BA_INSTANT_DIR.mkdir(parents=True, exist_ok=True)

    for y, m, path in files:
        cpath = COUNTY_DIR / f"{y}-{m:02d}.parquet"
        bpath = BA_INSTANT_DIR / f"{y}-{m:02d}.parquet"
        if bpath.exists() and cpath.exists() and not refresh:
            continue
        county = county_month(path, wmat, fips)
        county.write_parquet(cpath, compression="zstd")
        mat, bas = mats[min(max(y, MAP_YEARS[0]), MAP_YEARS[1])]
        ba_month(county, fips, mat, bas).write_parquet(bpath)
        log.info("%d-%02d: %d county-hours, %d BAs", y, m, county.height, len(bas))

    inst = pl.read_parquet(BA_INSTANT_DIR / "*.parquet")
    out = add_inertia(hour_ending(inst))
    out = out.with_columns(
        [pl.col(c).cast(pl.Float32) for c in out.columns if c not in ("ba", "period_end_utc")]
    )
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out.write_parquet(OUT_PATH, compression="zstd")
    log.info(
        "Wrote %s: %d rows, %d BAs, %s -> %s",
        OUT_PATH.name,
        out.height,
        out["ba"].n_unique(),
        out["period_end_utc"].min(),
        out["period_end_utc"].max(),
    )
    return out


def main() -> None:
    import argparse

    p = argparse.ArgumentParser(prog="python -m glg.build.weather")
    p.add_argument("--refresh", action="store_true", help="recompute every month")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    build(refresh=a.refresh)


if __name__ == "__main__":
    main()
