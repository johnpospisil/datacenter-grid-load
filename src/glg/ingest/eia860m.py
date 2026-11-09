"""EIA-860M monthly generator inventory, and EIA capacity factors (Prompt 11).

EIA-860M ("Preliminary Monthly Electric Generator Inventory") lists every utility-scale
generator (>= 1 MW) in the U.S. with its balancing authority, technology, net summer
capacity, and operating and retirement month. One release is enough: the Operating sheet
carries each unit's first month of operation and the Retired sheet each retired unit's
operating and retirement months, so additions and retirements for every year can be
rebuilt from the latest file. Uprates, derates and repowers are not captured.

The newest release is found by trying month files from the current month backwards
(EIA publishes placeholder links for future months that return 503). Downloads are cached
in data/raw/eia860m/; reruns use the newest cached file unless --refresh.

Capacity factors: EIA Electric Power Monthly tables 6.07.A (fossil) and 6.07.B (non-fossil),
annual national capacity factors by technology, cached in data/raw/eia_epm/.

Outputs:
- data/interim/eia860m_generators.parquet: one row per generator (operating + retired, CONUS)
- data/reference/capacity_factors.csv: 2023-2025 mean capacity factor per technology group

Run: python -m glg.ingest.eia860m [--refresh]
"""

import argparse
import logging
from datetime import date
from pathlib import Path

import pandas as pd
import polars as pl
import requests

from glg.config import INTERIM_DIR, RAW_DIR, REFERENCE_DIR
from glg.ingest.http import ZIP_MAGIC, download

log = logging.getLogger(__name__)

BASE = "https://www.eia.gov/electricity/data/eia860m"
EPM = "https://www.eia.gov/electricity/monthly/xls/table_6_07_{}.xlsx"
RAW = RAW_DIR / "eia860m"
EPM_RAW = RAW_DIR / "eia_epm"
GENERATORS_PATH = INTERIM_DIR / "eia860m_generators.parquet"
CF_PATH = REFERENCE_DIR / "capacity_factors.csv"
MONTHS = [
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
]  # fmt: skip
CF_YEARS = (2023, 2024, 2025)

# EPM table column groups, in the order they appear (each a capacity, capacity-factor pair).
EPM_A = ["coal", "ngcc", "ng_gt", "ng_st", "ng_ic", "pet_st", "pet_gt", "pet_ic"]
EPM_B = [
    "geothermal", "hydro", "nuclear", "other_biomass", "other_gas",
    "solar_pv", "solar_thermal", "wind", "wood",
]  # fmt: skip

# 860M technology -> capacity-factor group. Storage produces no net energy (it shifts it).
TECH_CF = {
    "Conventional Steam Coal": "coal",
    "Coal Integrated Gasification Combined Cycle": "coal",
    "Natural Gas Fired Combined Cycle": "ngcc",
    "Natural Gas Fired Combustion Turbine": "ng_gt",
    "Natural Gas Steam Turbine": "ng_st",
    "Natural Gas Internal Combustion Engine": "ng_ic",
    "Other Natural Gas": "other_gas",
    "Other Gases": "other_gas",
    "All Other": "other_gas",
    "Petroleum Coke": "pet_st",
    "Petroleum Liquids": "pet",  # split by prime mover below
    "Nuclear": "nuclear",
    "Conventional Hydroelectric": "hydro",
    "Geothermal": "geothermal",
    "Onshore Wind Turbine": "wind",
    "Offshore Wind Turbine": "wind",
    "Solar Photovoltaic": "solar_pv",
    "Solar Thermal with Energy Storage": "solar_thermal",
    "Solar Thermal without Energy Storage": "solar_thermal",
    "Wood/Wood Waste Biomass": "wood",
    "Landfill Gas": "other_biomass",
    "Municipal Solid Waste": "other_biomass",
    "Other Waste Biomass": "other_biomass",
    "Batteries": "storage",
    "Flywheels": "storage",
    "Hydroelectric Pumped Storage": "storage",
    "Natural Gas with Compressed Air Storage": "storage",
}
# Capacity-factor group -> chart group.
GROUP = {
    "solar_pv": "Solar", "solar_thermal": "Solar", "wind": "Wind",
    "ngcc": "Gas", "ng_gt": "Gas", "ng_st": "Gas", "ng_ic": "Gas", "other_gas": "Gas",
    "nuclear": "Nuclear", "coal": "Coal", "storage": "Storage",
    "hydro": "Other", "geothermal": "Other", "wood": "Other", "other_biomass": "Other",
    "pet_st": "Other", "pet_gt": "Other", "pet_ic": "Other",
}  # fmt: skip


# --------------------------------------------------------------------------- download


def candidates(today: date) -> list[tuple[str, str]]:
    """(file name, url) for each month from today back two years, newest first."""
    out = []
    y, m = today.year, today.month
    for _ in range(24):
        name = f"{MONTHS[m - 1]}_generator{y}.xlsx"
        out += [(name, f"{BASE}/xls/{name}"), (name, f"{BASE}/archive/xls/{name}")]
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out


def _available(url: str) -> bool:
    try:
        r = requests.head(url, allow_redirects=True, timeout=30)
        return r.status_code == 200 and int(r.headers.get("Content-Length", 0)) > 1_000_000
    except requests.RequestException:
        return False


def fetch_latest(refresh: bool = False, today: date | None = None) -> Path:
    """Path to the newest 860M release (cached unless refresh)."""
    cands = candidates(today or date.today())
    if not refresh:
        for name, _ in cands:
            if (RAW / name).exists():
                log.info("Using cached %s", name)
                return RAW / name
    for name, url in cands:
        if _available(url):
            return download(url, RAW / name, ZIP_MAGIC, refresh=refresh)
    raise RuntimeError("no EIA-860M release found")


# --------------------------------------------------------------------------- parse


def _num(c: str) -> pl.Expr:
    return pl.col(c).str.strip_chars().replace("", None).cast(pl.Float64, strict=False)


def _ym(y: str, m: str) -> pl.Expr:
    """First of the month; unknown months (coded 0, 88, 99 for some old units) -> January."""
    month = _num(m).cast(pl.Int32)
    month = pl.when(month.is_between(1, 12)).then(month).otherwise(1)
    year = _num(y).cast(pl.Int32)
    return pl.when(year.is_not_null()).then(pl.date(year, month, 1))


def parse(path) -> pl.DataFrame:
    sheets = pd.read_excel(
        path, sheet_name=["Operating", "Retired"], header=2, engine="openpyxl", dtype=str
    )
    frames = []
    for sheet, df in sheets.items():
        d = pl.from_pandas(df).filter(pl.col("Plant ID").str.strip_chars().str.contains(r"^\d+$"))
        retired = sheet == "Retired"
        frames.append(
            d.select(
                pl.col("Plant ID").cast(pl.Int64).alias("plant_id"),
                pl.col("Plant Name").alias("plant_name"),
                pl.col("Generator ID").alias("generator_id"),
                pl.col("Plant State").alias("state"),
                pl.col("County").alias("county"),
                pl.col("Balancing Authority Code").alias("ba_code"),
                pl.col("Technology").alias("technology"),
                pl.col("Energy Source Code").alias("energy_source"),
                pl.col("Prime Mover Code").alias("prime_mover"),
                _num("Nameplate Capacity (MW)").alias("nameplate_mw"),
                _num("Net Summer Capacity (MW)").alias("summer_mw"),
                _ym("Operating Year", "Operating Month").alias("op_month"),
                (
                    _ym("Retirement Year", "Retirement Month") if retired else pl.lit(None, pl.Date)
                ).alias("ret_month"),
                pl.lit(sheet.lower()).alias("sheet"),
            )
        )
    g = pl.concat(frames)
    cf = pl.col("technology").replace_strict(TECH_CF, default=None)
    pet = (
        pl.when(pl.col("prime_mover") == "ST")
        .then(pl.lit("pet_st"))
        .when(pl.col("prime_mover") == "GT")
        .then(pl.lit("pet_gt"))
        .otherwise(pl.lit("pet_ic"))
    )
    g = g.with_columns(pl.when(cf == "pet").then(pet).otherwise(cf).alias("cf_group"))
    g = g.with_columns(pl.col("cf_group").replace_strict(GROUP, default=None).alias("group"))
    unknown = g.filter(pl.col("cf_group").is_null())["technology"].unique().to_list()
    if unknown:
        raise ValueError(f"unmapped 860M technologies: {unknown}")
    # summer capacity is missing for a few units: fall back to nameplate
    return g.with_columns(pl.col("summer_mw").fill_null(pl.col("nameplate_mw")))


def _epm_table(path, groups: list[str]) -> pl.DataFrame:
    raw = pd.read_excel(path, header=None, engine="openpyxl")
    rows = []
    for _, r in raw.iterrows():
        v = r.tolist()
        if isinstance(v[0], (int, float)) and not pd.isna(v[0]) and 2000 < v[0] < 2100:
            vals = [x for x in v[1:] if not pd.isna(x)]
            for k, grp in enumerate(groups):
                rows.append({"year": int(v[0]), "cf_group": grp, "cf": float(vals[2 * k + 1])})
    return pl.DataFrame(rows)


def capacity_factors(refresh: bool = False) -> pl.DataFrame:
    tables = []
    for t, groups in [("a", EPM_A), ("b", EPM_B)]:
        p = download(EPM.format(t), EPM_RAW / f"table_6_07_{t}.xlsx", ZIP_MAGIC, refresh=refresh)
        tables.append(_epm_table(p, groups))
    cf = (
        pl.concat(tables)
        .filter(pl.col("year").is_in(CF_YEARS))
        .group_by("cf_group")
        .agg(pl.col("cf").mean().round(3))
    )
    cf = pl.concat([cf, pl.DataFrame({"cf_group": ["storage"], "cf": [0.0]})]).with_columns(
        pl.col("cf_group").replace_strict(GROUP).alias("group"),
        pl.lit(
            f"EIA Electric Power Monthly Tables 6.07.A/B, annual U.S. capacity factor, "
            f"mean of {CF_YEARS[0]}-{CF_YEARS[-1]}; storage = 0 (shifts energy, net consumer)"
        ).alias("source"),
    )
    return cf.sort("cf_group")


def run(refresh: bool = False) -> pl.DataFrame:
    path = fetch_latest(refresh)
    g = parse(path).with_columns(pl.lit(path.name).alias("release"))
    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    g.write_parquet(GENERATORS_PATH)
    cf = capacity_factors(refresh)
    cf.write_csv(CF_PATH)
    log.info(
        "%s: %d generators (%d retired), %.0f GW operating; %d capacity-factor groups",
        path.name,
        g.height,
        g.filter(pl.col("sheet") == "retired").height,
        g.filter(pl.col("sheet") == "operating")["summer_mw"].sum() / 1000,
        cf.height,
    )
    return g


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    run(ap.parse_args().refresh)


if __name__ == "__main__":
    main()
