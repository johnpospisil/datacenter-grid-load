"""EIA Form 861 tables, via Catalyst Cooperative's PUDL data release (CC-BY-4.0).

Prompt 4 needs the utility service-territory tables: which counties each utility serves,
and which balancing authority each utility belongs to in each state.

Prompt 13 adds retail prices by state and sector. These come from the EIA API v2
`electricity/retail-sales` dataset (EIA-861 annual, completed by EIA-861M monthly through the
latest month; U.S. government work), because it runs to the current month, unlike PUDL.
Prices are revenue / sales (cents per kWh), annual 2010 onwards plus the latest 12 months,
in nominal terms and in constant dollars of the last complete year (CPI-U, all items,
CPIAUCSL, from FRED).

Run: python -m glg.ingest.eia861
"""

import json
import logging
import os
from pathlib import Path

import polars as pl
import requests
from dotenv import load_dotenv

from glg.config import PROCESSED_DIR, RAW_DIR
from glg.ingest.http import PARQUET_MAGIC, PUDL_BASE
from glg.ingest.http import download as fetch

log = logging.getLogger(__name__)

RAW_EIA861_DIR = RAW_DIR / "eia861"
TERRITORY_TABLES = (
    "core_eia861__yearly_service_territory",  # utility x state x county x year
    "core_eia861__assn_balancing_authority",  # utility x state -> BA id, by year
    "core_eia861__yearly_balancing_authority",  # BA id -> BA code and name
    "core_eia861__yearly_sales",  # utility x state x BA: customers and MWh by sector
)


def table_path(table: str) -> Path:
    return RAW_EIA861_DIR / f"{table}.parquet"


def download(refresh: bool = False) -> list[Path]:
    return [
        fetch(f"{PUDL_BASE}/{t}.parquet", table_path(t), PARQUET_MAGIC, refresh)
        for t in TERRITORY_TABLES
    ]


# --------------------------------------------------------------------------- retail prices

API = "https://api.eia.gov/v2/electricity/retail-sales/data/"
CPI_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=CPIAUCSL"
RETAIL_PATH = PROCESSED_DIR / "retail_prices.parquet"
STATES = ["US", "VA", "TX", "GA", "AZ", "OR", "WA", "NV"]
SECTORS = {"RES": "residential", "COM": "commercial", "IND": "industrial", "ALL": "all sectors"}
FIRST_YEAR = 2010


def _api(frequency: str, start: str, refresh: bool) -> list[dict]:
    path = RAW_EIA861_DIR / f"retail_sales_{frequency}.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text())
    load_dotenv()
    params = {
        "api_key": os.environ["EIA_API_KEY"],
        "frequency": frequency,
        "data[]": ["revenue", "sales", "price"],
        "facets[stateid][]": STATES,
        "facets[sectorid][]": list(SECTORS),
        "start": start,
        "length": 5000,
    }
    rows, offset = [], 0
    while True:
        r = requests.get(API, params={**params, "offset": offset}, timeout=120)
        r.raise_for_status()
        batch = r.json()["response"]["data"]
        rows += batch
        if len(batch) < params["length"]:
            break
        offset += params["length"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows))
    log.info("EIA retail-sales %s: %d rows", frequency, len(rows))
    return rows


def _frame(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows).select(
        pl.col("period"),
        pl.col("stateid").alias("state"),
        pl.col("sectorid").alias("sector"),
        pl.col("revenue").cast(pl.Float64, strict=False).alias("revenue_musd"),
        pl.col("sales").cast(pl.Float64, strict=False).alias("sales_gwh"),
    )


def cpi(refresh: bool = False) -> pl.DataFrame:
    path = RAW_DIR / "fred" / "CPIAUCSL.csv"
    fetch(CPI_URL, path, None, refresh)
    c = pl.read_csv(path)
    return c.select(
        pl.col(c.columns[0]).str.to_date().alias("month"), pl.col(c.columns[1]).alias("cpi")
    )


def retail_prices(refresh: bool = False) -> pl.DataFrame:
    """Annual prices (cents/kWh) by state x sector, plus the latest 12 months."""
    annual = _frame(_api("annual", str(FIRST_YEAR), refresh)).with_columns(
        pl.col("period").cast(pl.Int32).alias("year"), pl.lit(False).alias("trailing")
    )
    last_year = annual["year"].max()
    monthly = _frame(_api("monthly", f"{last_year}-01", refresh)).with_columns(
        pl.col("period").str.to_date("%Y-%m").alias("month")
    )
    end = monthly["month"].max()
    if end.year > last_year:
        start = (
            end.replace(year=end.year - 1, month=end.month % 12 + 1)
            if end.month < 12
            else (end.replace(month=1))
        )
        t = (
            monthly.filter(pl.col("month") >= start)
            .group_by("state", "sector")
            .agg(pl.col("revenue_musd").sum(), pl.col("sales_gwh").sum(), pl.len().alias("n"))
            .filter(pl.col("n") == 12)
            .with_columns(
                pl.lit(f"12 months to {end:%Y-%m}").alias("period"),
                pl.lit(end.year).cast(pl.Int32).alias("year"),
                pl.lit(True).alias("trailing"),
            )
            .drop("n")
        )
        annual = pl.concat([annual, t.select(annual.columns)])
    c = cpi(refresh)
    cpi_year = c.group_by(pl.col("month").dt.year().cast(pl.Int32).alias("year")).agg(
        pl.col("cpi").mean()
    )
    base = cpi_year.filter(pl.col("year") == last_year)["cpi"][0]
    trailing_cpi = c.filter(pl.col("month") > end.replace(year=end.year - 1))["cpi"].mean()
    out = annual.join(cpi_year, on="year", how="left").with_columns(
        pl.when(pl.col("trailing")).then(pl.lit(trailing_cpi)).otherwise(pl.col("cpi")).alias("cpi")
    )
    return (
        out.with_columns(
            (100 * pl.col("revenue_musd") / pl.col("sales_gwh")).alias("price_cents_kwh"),
            pl.col("sector").replace(SECTORS).alias("sector_name"),
        )
        .with_columns(
            (pl.col("price_cents_kwh") * base / pl.col("cpi")).alias(f"price_real_{last_year}"),
        )
        .sort("state", "sector", "year", "trailing")
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    download()
    r = retail_prices()
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    r.write_parquet(RETAIL_PATH)
    log.info("Retail prices: %d rows, %s to %s", r.height, r["period"].min(), r["period"].max())


if __name__ == "__main__":
    main()
