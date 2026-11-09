"""Annual utility-scale generation by state and fuel, EIA API v2 (Prompt 15 robustness).

Dataset `electricity/electric-power-operational-data` (EIA-923 based; U.S. government work),
all sectors (sectorid 99), fuel types SPV (solar photovoltaic) and WND (wind), annual.
Used only to derive state capacity factors for the supply-gap robustness check.

Run: python -m glg.ingest.eia_generation
"""

import json
import logging
import os

import polars as pl
import requests
from dotenv import load_dotenv

from glg.config import RAW_DIR

log = logging.getLogger(__name__)

API = "https://api.eia.gov/v2/electricity/electric-power-operational-data/data/"
RAW = RAW_DIR / "eia_generation" / "state_generation_spv_wnd.json"
FUEL_GROUP = {"SPV": "solar_pv", "WND": "wind"}


def fetch(refresh: bool = False) -> list[dict]:
    if RAW.exists() and not refresh:
        return json.loads(RAW.read_text())
    load_dotenv()
    params = {
        "api_key": os.environ["EIA_API_KEY"],
        "frequency": "annual",
        "data[]": ["generation"],
        "facets[sectorid][]": "99",
        "facets[fueltypeid][]": list(FUEL_GROUP),
        "start": "2020",
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
    RAW.parent.mkdir(parents=True, exist_ok=True)
    RAW.write_text(json.dumps(rows))
    log.info("EIA state generation: %d rows", len(rows))
    return rows


def state_generation(refresh: bool = False) -> pl.DataFrame:
    df = pl.DataFrame(fetch(refresh))
    return df.filter(pl.col("location").str.len_chars() == 2).select(
        pl.col("location").alias("state"),
        pl.col("fueltypeid").replace_strict(FUEL_GROUP).alias("cf_group"),
        pl.col("period").cast(pl.Int32).alias("year"),
        (pl.col("generation").cast(pl.Float64, strict=False) * 1000).alias("gen_mwh"),
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(state_generation(refresh=True).sort("year").tail(5))
