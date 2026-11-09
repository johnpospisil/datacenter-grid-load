"""Census county population estimates and county boundaries (public domain).

- Population: Vintage 2019 (2010-2019) and Vintage 2025 (2020-2025) county totals.
- Boundaries: 2020 cartographic boundary file, 1:500k. We use 2020 boundaries for every
  year so the county list is fixed (Connecticut's 8 counties, not its 2022 planning regions).

Run: python -m glg.ingest.census
"""

import logging
from pathlib import Path

from glg.config import RAW_DIR
from glg.ingest.http import ZIP_MAGIC
from glg.ingest.http import download as fetch

log = logging.getLogger(__name__)

RAW_CENSUS_DIR = RAW_DIR / "census"
POPEST = "https://www2.census.gov/programs-surveys/popest/datasets"
FILES = {
    "co-est2019-alldata.csv": f"{POPEST}/2010-2019/counties/totals/co-est2019-alldata.csv",
    "co-est2025-alldata.csv": f"{POPEST}/2020-2025/counties/totals/co-est2025-alldata.csv",
    "cb_2020_us_county_500k.zip": (
        "https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_us_county_500k.zip"
    ),
}


def path(name: str) -> Path:
    return RAW_CENSUS_DIR / name


def download(refresh: bool = False) -> list[Path]:
    return [
        fetch(url, path(name), ZIP_MAGIC if name.endswith(".zip") else None, refresh)
        for name, url in FILES.items()
    ]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    download()


if __name__ == "__main__":
    main()
