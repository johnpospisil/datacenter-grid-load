"""LBNL "Queued Up" interconnection queue data (Prompt 12).

Source: Lawrence Berkeley National Laboratory and GridTracker, *Queued Up: 2026 Edition,
Characteristics of Power Plants Seeking Transmission Interconnection As of the End of
2025* (data file updated May 2026), licensed CC BY 4.0. Project-level data from the
7 ISO/RTOs and ~50 non-ISO balancing areas: one row per interconnection request, with its
status (active / withdrawn / suspended / operational), request, agreement, withdrawal and
online dates, county, transmission provider ("entity"), resource type and MW.

Output: data/processed/queues.parquet, one row per request, plus:
- `series`: our series where the entity maps to one (ISO -> PJM/ERCO/...; APS -> AZPS;
  DOM: PJM requests in the counties Dominion serves, as in Prompt 11);
- `mw_total` = mw_1 + mw_2 + mw_3 (hybrids carry all their components);
- `type_group`: Solar, Wind, Battery, Gas, Hybrid (solar/wind + storage), Nuclear, Other.

Run: python -m glg.ingest.queues
"""

import logging

import pandas as pd
import polars as pl

from glg.config import PROCESSED_DIR, RAW_DIR, REFERENCE_DIR
from glg.ingest.http import ZIP_MAGIC, download

log = logging.getLogger(__name__)

URL = (
    "https://eta-publications.lbl.gov/sites/default/files/2026-05/"
    "lbnl_ix_queue_data_file_thru2025.xlsx"
)
RAW = RAW_DIR / "lbnl_queues" / "lbnl_ix_queue_data_file_thru2025.xlsx"
QUEUES_PATH = PROCESSED_DIR / "queues.parquet"
EDITION = "Queued Up 2026 Edition (data through 2025-12-31)"
ATTRIBUTION = "Lawrence Berkeley National Laboratory and GridTracker, Queued Up (CC BY 4.0)"

# LBNL entity code -> our series (only the regions we model). Special cases in series_expr:
# Duke is split by utility, PacifiCorp East = Utah/Wyoming/Idaho, SRP's sub-entities are SRP.
# "Dominion" in LBNL is Dominion Energy South Carolina, not the Virginia DOM zone (which is
# in PJM and is selected by county).
ENTITY_SERIES = {
    "PJM": "PJM",
    "ERCOT": "ERCO",
    "MISO": "MISO",
    "SPP": "SWPP",
    "CAISO": "CISO",
    "NYISO": "NYIS",
    "ISO-NE": "ISNE",
    "APS": "AZPS",
    "SRP": "SRP",
    "NVE": "NEVP",
    "BPA": "BPAT",
    "GrantPUD": "GCPD",
    "FPL": "FPL",
    "SOCO": "SOCO",
    "TVA": "TVA",
}
DUKE = {
    "Duke Energy Carolinas": "DUK",
    "Duke Energy Progress": "CPLE",  # East and West together
    "Duke Energy Florida": "FPC",
}
PACE_STATES = ["UT", "WY", "ID"]


def series_expr() -> pl.Expr:
    e = pl.col("entity")
    return (
        pl.when(e == "Duke")
        .then(pl.col("utility").replace_strict(DUKE, default=None))
        .when(e == "PacifiCorp")
        .then(pl.when(pl.col("state").is_in(PACE_STATES)).then(pl.lit("PACE")))
        .when(e.str.starts_with("SRP"))
        .then(pl.lit("SRP"))
        .otherwise(e.replace_strict(ENTITY_SERIES, default=None))
    )


def type_group(t: pl.Expr) -> pl.Expr:
    return (
        pl.when(t.str.contains(r"\+"))
        .then(pl.lit("Hybrid"))
        .when(t.is_in(["Solar", "Wind", "Battery", "Gas", "Nuclear"]))
        .then(t)
        .when(t == "Offshore Wind")
        .then(pl.lit("Wind"))
        .when(t == "Other Storage")
        .then(pl.lit("Battery"))
        .otherwise(pl.lit("Other"))
    )


def dom_fips() -> list[str]:
    m = pl.read_csv(REFERENCE_DIR / "ba_county_map.csv", schema_overrides={"county_fips": pl.Utf8})
    latest = m.filter(pl.col("ba") == "DOM")["year"].max()
    sel = m.filter((pl.col("ba") == "DOM") & (pl.col("year") == latest) & (pl.col("weight") > 0))
    return sel["county_fips"].unique().to_list()


DATE_COLS = ["q_date", "prop_date", "on_date", "wd_date", "ia_date"]
NUM_COLS = ["mw_1", "mw_2", "mw_3", "q_year", "prop_year"]


def _clean_frame(df: pd.DataFrame) -> pl.DataFrame:
    """Coerce mixed-type Excel columns: dates, numbers, and everything else as text."""
    df = df.copy()
    for c in df.columns:
        if c in DATE_COLS:
            df[c] = pd.to_datetime(df[c], errors="coerce")
        elif c in NUM_COLS:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        else:
            df[c] = df[c].map(lambda v: None if pd.isna(v) else str(v).strip())
    return pl.from_pandas(df)


def parse(path) -> pl.DataFrame:
    df = pd.read_excel(path, sheet_name="03. Complete Queue Data", header=1, engine="openpyxl")
    q = _clean_frame(df.loc[:, ~df.columns.astype(str).str.startswith("Unnamed")])
    q = q.filter(pl.col("q_status").is_not_null())
    q = q.with_columns(
        *(pl.col(c).dt.date() for c in DATE_COLS),
        *(pl.col(c).fill_null(0.0) for c in ["mw_1", "mw_2", "mw_3"]),
        pl.col("q_year").cast(pl.Int64),
        pl.col("prop_year").cast(pl.Int64),
        pl.col("fips_code").str.replace(r"\.0$", "").str.zfill(5),
        pl.col("q_status").str.to_lowercase(),
    )
    q = q.with_columns(
        (pl.col("mw_1") + pl.col("mw_2") + pl.col("mw_3")).alias("mw_total"),
        type_group(pl.col("type_clean").cast(pl.Utf8)).alias("type_group"),
        series_expr().alias("series"),
    )
    dom = q.filter((pl.col("entity") == "PJM") & pl.col("fips_code").is_in(dom_fips()))
    dom = dom.with_columns(pl.lit("DOM").alias("series"))
    return pl.concat([q, dom])


def run() -> pl.DataFrame:
    path = download(URL, RAW, ZIP_MAGIC)
    q = parse(path)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    q.write_parquet(QUEUES_PATH)
    n = q.filter(pl.col("series").ne_missing("DOM"))
    act = n.filter(pl.col("q_status") == "active")
    log.info(
        "%s: %d requests, %d active (%.0f GW); %d DOM-zone requests",
        EDITION,
        n.height,
        act.height,
        act["mw_total"].sum() / 1000,
        q.filter(pl.col("series") == "DOM").height,
    )
    unmapped = n.filter(pl.col("series").is_null())["entity"].drop_nulls().unique().sort()
    log.info("Entities with no series (not modelled): %s", unmapped.to_list())
    return q


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()


if __name__ == "__main__":
    main()
