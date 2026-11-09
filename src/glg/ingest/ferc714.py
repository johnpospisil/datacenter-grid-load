"""FERC Form 714 hourly planning-area demand, via Catalyst Cooperative's PUDL data release.

FERC-714 is an annual filing, separate from EIA-930, in which each planning area reports
its hourly demand. We use it as the independent benchmark for EIA-930 annual totals
(tests/test_annual_reconciliation.py). PUDL publishes it as Parquet under CC-BY-4.0.

Run: python -m glg.ingest.ferc714
"""

import logging
from pathlib import Path

from glg.config import RAW_DIR
from glg.ingest.http import PARQUET_MAGIC, PUDL_BASE
from glg.ingest.http import download as fetch

log = logging.getLogger(__name__)

TABLES = ("out_ferc714__hourly_planning_area_demand", "core_ferc714__respondent_id")
RAW_FERC_DIR = RAW_DIR / "ferc714"


def table_path(table: str) -> Path:
    return RAW_FERC_DIR / f"{table}.parquet"


def download(refresh: bool = False) -> list[Path]:
    return [
        fetch(f"{PUDL_BASE}/{t}.parquet", table_path(t), PARQUET_MAGIC, refresh) for t in TABLES
    ]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    download()


if __name__ == "__main__":
    main()
