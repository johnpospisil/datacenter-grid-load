"""Project paths and settings, read from the environment (.env)."""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def _path(var: str, default: Path) -> Path:
    value = os.getenv(var)
    return Path(value).expanduser() if value else default


DATA_DIR = _path("GLG_DATA_DIR", PROJECT_ROOT / "data")
RAW_DIR = DATA_DIR / "raw"
INTERIM_DIR = DATA_DIR / "interim"
PROCESSED_DIR = DATA_DIR / "processed"
OUTPUTS_DIR = DATA_DIR / "outputs"
# Reference tables are small, hand-curated and committed, so they live in the repo.
REFERENCE_DIR = PROJECT_ROOT / "data" / "reference"
# Bulk ERA5 NetCDF can live elsewhere (e.g. an external drive); read once, then aggregated.
RAW_WEATHER_DIR = _path("GLG_RAW_WEATHER_DIR", RAW_DIR / "era5")

FIGURES_DIR = PROJECT_ROOT / "report" / "figures"

# Baseline and exclusion windows (UTC). See CLAUDE.md.
BASELINE_YEARS = (2016, 2019)
COVID_START = "2020-03-01"
COVID_END = "2021-12-31"


def eia_api_key() -> str:
    key = os.getenv("EIA_API_KEY")
    if not key:
        raise RuntimeError("EIA_API_KEY is not set. Add it to .env (see .env.example).")
    return key
