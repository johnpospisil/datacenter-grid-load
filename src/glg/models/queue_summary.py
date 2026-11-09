"""Interconnection-queue summaries (Prompt 12), from data/processed/queues.parquet.

- Active backlog: MW of requests still active at the end of 2025 (suspended ones excluded),
  by region x resource type, and by queue-entry year.
- Completion: for requests that entered the queue in 2000-2020 (old enough to have
  resolved; LBNL's convention), the share of requests and of MW that reached commercial
  operation, were withdrawn, or are still active or suspended.
- Time to operation: median years from interconnection request to commercial operation
  (and to a signed interconnection agreement), for projects that came online in 2018-2025.

Regions are LBNL's (7 ISO/RTOs, West and Southeast non-ISO) and our series (ingest/queues.py;
DOM = PJM requests in the counties Dominion serves). Hybrid requests count all components.
Queue MW is a ceiling on future supply, not a forecast: most requests are withdrawn.

Run: python -m glg.models.queue_summary
"""

import logging

import polars as pl

from glg.config import OUTPUTS_DIR
from glg.ingest import queues as qi

log = logging.getLogger(__name__)

ACTIVE_PATH = OUTPUTS_DIR / "queue_active.csv"
ACTIVE_YEAR_PATH = OUTPUTS_DIR / "queue_active_by_year.csv"
COMPLETION_PATH = OUTPUTS_DIR / "queue_completion.csv"
TIMING_PATH = OUTPUTS_DIR / "queue_timing.csv"
PIPELINE_PATH = OUTPUTS_DIR / "queue_pipeline.csv"

# Rough energy of the active queue: capacity factors by LBNL resource type (EIA Electric
# Power Monthly 6.07.A/B, 2023-25, as in Prompt 11). Gas at the combined-cycle factor, an
# upper bound; storage zero; "Other" (type unknown) left out of the energy.
QUEUE_CF = {
    "Solar": 0.235, "Wind": 0.338, "Offshore Wind": 0.338, "Gas": 0.596, "Nuclear": 0.916,
    "Hydro": 0.347, "Geothermal": 0.666, "Coal": 0.446, "Battery": 0.0, "Other Storage": 0.0,
}  # fmt: skip

COHORT = (2000, 2020)
ONLINE = (2018, 2025)
TYPES = ["Solar", "Wind", "Battery", "Hybrid", "Gas", "Nuclear", "Other"]


def _units(q: pl.DataFrame) -> pl.DataFrame:
    """Queue rows twice: once under the LBNL region, once under our series (if any)."""
    a = q.filter(pl.col("series").ne_missing("DOM")).with_columns(
        pl.lit("region").alias("level"), pl.col("region").alias("unit")
    )
    b = q.filter(pl.col("series").is_not_null()).with_columns(
        pl.lit("series").alias("level"), pl.col("series").alias("unit")
    )
    return pl.concat([a, b])


def active(q: pl.DataFrame) -> pl.DataFrame:
    u = _units(q).filter(pl.col("q_status") == "active")
    wide = (
        u.group_by("level", "unit", "type_group")
        .agg((pl.col("mw_total").sum() / 1000).alias("gw"))
        .pivot("type_group", index=["level", "unit"], values="gw")
        .fill_null(0.0)
    )
    for t in TYPES:
        if t not in wide.columns:
            wide = wide.with_columns(pl.lit(0.0).alias(t))
    n = u.group_by("level", "unit").agg(pl.len().alias("n_active"))
    return (
        wide.select("level", "unit", *TYPES)
        .with_columns(pl.sum_horizontal(TYPES).alias("total_gw"))
        .join(n, on=["level", "unit"])
        .rename({t: f"{t.lower()}_gw" for t in TYPES})
        .sort("level", "total_gw", descending=[False, True])
    )


def active_by_year(q: pl.DataFrame) -> pl.DataFrame:
    u = _units(q).filter(pl.col("q_status") == "active")
    return (
        u.group_by("level", "unit", "q_year", "type_group")
        .agg((pl.col("mw_total").sum() / 1000).alias("gw"), pl.len().alias("n"))
        .sort("level", "unit", "q_year", "type_group")
    )


def completion(q: pl.DataFrame, by: list[str]) -> pl.DataFrame:
    c = _units(q).filter(pl.col("q_year").is_between(*COHORT))
    status = ["operational", "withdrawn", "active", "suspended"]
    aggs = [pl.len().alias("n"), pl.col("mw_total").sum().alias("mw")]
    for s in status:
        m = pl.col("q_status") == s
        aggs += [
            (100 * m.mean()).alias(f"pct_n_{s}"),
            (100 * pl.col("mw_total").filter(m).sum() / pl.col("mw_total").sum()).alias(
                f"pct_mw_{s}"
            ),
        ]
    return c.group_by("level", "unit", *by).agg(aggs).sort("level", "unit", *by)


def timing(q: pl.DataFrame) -> pl.DataFrame:
    yrs = 365.25
    c = _units(q).filter(
        (pl.col("q_status") == "operational")
        & pl.col("on_date").dt.year().is_between(*ONLINE)
        & pl.col("q_date").is_not_null()
    )
    c = c.with_columns(
        ((pl.col("on_date") - pl.col("q_date")).dt.total_days() / yrs).alias("ir_to_cod"),
        ((pl.col("ia_date") - pl.col("q_date")).dt.total_days() / yrs).alias("ir_to_ia"),
    ).filter(pl.col("ir_to_cod") >= 0)
    return (
        c.group_by("level", "unit")
        .agg(
            pl.len().alias("n_online"),
            pl.col("ir_to_cod").median().alias("median_years_ir_to_cod"),
            pl.col("ir_to_ia")
            .filter(pl.col("ir_to_ia") >= 0)
            .median()
            .alias("median_years_ir_to_ia"),
            pl.col("ir_to_ia").filter(pl.col("ir_to_ia") >= 0).len().alias("n_with_ia_date"),
        )
        .sort("level", "unit")
    )


def pipeline(q: pl.DataFrame, comp: pl.DataFrame) -> pl.DataFrame:
    """Active queue energy (TWh/yr) and that energy x the series' historical MW completion rate.

    A rough, descriptive ceiling-times-history figure, not a forecast (DATA.md).
    """
    a = q.filter(pl.col("series").is_not_null() & (pl.col("q_status") == "active"))
    parts = []
    for k in ("1", "2", "3"):
        parts.append(
            a.select("series", pl.col(f"type_{k}").alias("type"), pl.col(f"mw_{k}").alias("mw"))
        )
    p = pl.concat(parts).filter(pl.col("type").is_not_null() & (pl.col("mw") > 0))
    p = p.with_columns(pl.col("type").replace_strict(QUEUE_CF, default=None).alias("cf"))
    rate = comp.filter(pl.col("level") == "series").select(
        pl.col("unit").alias("series"), (pl.col("pct_mw_operational") / 100).alias("completion")
    )
    return (
        p.group_by("series")
        .agg(
            (pl.col("mw").sum() / 1000).alias("active_gw"),
            (pl.col("mw").filter(pl.col("cf").is_null()).sum() / 1000).alias("unknown_type_gw"),
            (pl.col("mw") * pl.col("cf").fill_null(0) * 8766 / 1e6).sum().alias("queue_twh"),
        )
        .join(rate, on="series", how="left")
        .with_columns((pl.col("queue_twh") * pl.col("completion")).alias("expected_twh"))
        .sort("queue_twh", descending=True)
    )


def run() -> dict[str, pl.DataFrame]:
    q = pl.read_parquet(qi.QUEUES_PATH)
    out = {
        "active": active(q),
        "by_year": active_by_year(q),
        "completion": completion(q, []),
        "completion_type": completion(q, ["type_group"]),
        "timing": timing(q),
    }
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    out["active"].write_csv(ACTIVE_PATH, float_precision=2)
    out["by_year"].write_csv(ACTIVE_YEAR_PATH, float_precision=3)
    pl.concat([out["completion"], out["completion_type"]], how="diagonal").write_csv(
        COMPLETION_PATH, float_precision=2
    )
    out["timing"].write_csv(TIMING_PATH, float_precision=2)
    out["pipeline"] = pipeline(q, out["completion"])
    out["pipeline"].write_csv(PIPELINE_PATH, float_precision=2)
    with pl.Config(tbl_rows=60, tbl_cols=14, tbl_width_chars=220):
        print(out["active"].with_columns(pl.col(pl.Float64).round(1)))
        print(
            out["completion"]
            .select("level", "unit", "n", "pct_n_operational", "pct_mw_operational",
                    "pct_mw_withdrawn", "pct_mw_active")
            .join(out["timing"], on=["level", "unit"], how="left")
            .with_columns(pl.col(pl.Float64).round(1))
        )  # fmt: skip
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()


if __name__ == "__main__":
    main()
