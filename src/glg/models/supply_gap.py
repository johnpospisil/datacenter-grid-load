"""Weather-adjusted demand growth vs new generation supply, in energy terms (Prompt 11).

Supply side: EIA-860M generators (ingest/eia860m.py) assigned to our series by their
balancing-authority code. Two exceptions:
- DOM (Dominion zone) is not a BA in 860M: its plants are PJM plants in the counties
  where Dominion serves any customers (data/reference/ba_county_map.csv, latest year).
  PJM's DOM zone is Dominion's transmission zone and includes co-op territory inside it,
  so a majority-share rule would miss plants such as Bear Garden (Buckingham County).
  This picks up 134 counties and ~31 GW of operating capacity; it leaves out Dominion's
  Mt Storm (West Virginia) and plants in the AEP/FirstEnergy zones;
- combined series (AZPS+SRP, FPL+SOCO) sum their parts.
860M carries each plant's *current* BA (Gulf Power's plants are FPL), consistent with the
FPL/SOCO windows starting in 2023.

Energy: summer capacity x the technology's national capacity factor (EIA Electric Power
Monthly 6.07.A/B, 2023-2025 mean; data/reference/capacity_factors.csv) x 8,766 h. Storage
counts as zero energy: it shifts energy and adds peak capacity, which is reported apart.

Since-2022 comparison, symmetric with Prompt 9's demand measure: each unit counts in a
window in proportion to the months it was operating there, so

    new supply (TWh/yr) = sum over units of MW x CF x 8.766 x (share of the latest 12
                          months online - share of the start year online)

against weather-adjusted demand growth (growth_ranking.csv: twh_per_yr_added). 860M ends at
its release month (September 2026); units are assumed unchanged in later months.

Descriptive only. Grids import and export: a region whose demand outgrows local supply
leans on its neighbours, on running existing plants harder, or on both. Capacity factors
are national; local solar and wind output differ (e.g. Arizona solar runs above it).

Outputs (data/):
- processed/capacity_changes.parquet: one row per generator event (added/retired) per series
- outputs/supply_vs_demand.csv: series x year: MW added/retired by group, expected TWh of
  net additions, change in weather-adjusted demand (TWh/yr)
- outputs/supply_vs_demand_since2022.csv: series: demand growth vs new supply since 2022

Run: python -m glg.models.supply_gap
"""

import logging
from datetime import date

import polars as pl

from glg.config import OUTPUTS_DIR, PROCESSED_DIR, REFERENCE_DIR
from glg.ingest import eia860m
from glg.models import growth as g

log = logging.getLogger(__name__)

CHANGES_PATH = PROCESSED_DIR / "capacity_changes.parquet"
YEARLY_PATH = OUTPUTS_DIR / "supply_vs_demand.csv"
SINCE_PATH = OUTPUTS_DIR / "supply_vs_demand_since2022.csv"
SINCE_REGIONAL_PATH = OUTPUTS_DIR / "supply_vs_demand_since2022_regional_cf.csv"
REGIONAL_CF_PATH = OUTPUTS_DIR / "regional_capacity_factors.csv"

HOURS = g.HOURS_PER_YEAR
FIRST_YEAR = g.FIRST_YEAR
GROUPS = ["Solar", "Wind", "Gas", "Nuclear", "Coal", "Other", "Storage"]


# --------------------------------------------------------------------------- series


def dom_counties() -> pl.DataFrame:
    m = pl.read_csv(REFERENCE_DIR / "ba_county_map.csv", schema_overrides={"county_fips": pl.Utf8})
    latest = m.filter(pl.col("ba") == "DOM")["year"].max()
    return (
        m.filter((pl.col("ba") == "DOM") & (pl.col("year") == latest) & (pl.col("weight") > 0))
        .select("state", pl.col("county").str.to_lowercase().alias("county_key"))
        .unique()
    )


def _county_key(c: str) -> pl.Expr:
    """860M county names, normalised to the map's (drop ' City'/' County', lower case)."""
    return (
        pl.col(c)
        .str.to_lowercase()
        .str.replace(r"\s+(county|city)$", "")
        .str.replace_all(r"[.']", "")
        .str.strip_chars()
    )


def assign_series(gen: pl.DataFrame, series: list[str]) -> pl.DataFrame:
    """One row per generator x series it belongs to."""
    direct = gen.filter(pl.col("ba_code").is_in(series)).with_columns(
        pl.col("ba_code").alias("series")
    )
    dom = dom_counties().with_columns(_county_key("county_key").alias("county_key"))
    pjm = gen.filter(pl.col("ba_code") == "PJM").with_columns(
        _county_key("county").alias("county_key")
    )
    dom_gen = pjm.join(dom, on=["state", "county_key"], how="semi").with_columns(
        pl.lit("DOM").alias("series")
    )
    out = [direct, dom_gen.drop("county_key")]
    for name, parts in g.COMBINED.items():
        if name in series:
            out.append(
                gen.filter(pl.col("ba_code").is_in(parts)).with_columns(
                    pl.lit(name).alias("series")
                )
            )
    return pl.concat(out, how="diagonal_relaxed")


def capacity_changes(gs: pl.DataFrame, cf: pl.DataFrame) -> pl.DataFrame:
    """Generator events: 'added' at op_month, 'retired' at ret_month (from 2016)."""
    base = gs.join(cf.select("cf_group", "cf"), on="cf_group", how="left")
    cols = ["series", "plant_id", "plant_name", "generator_id", "ba_code", "technology",
            "cf_group", "group", "summer_mw", "cf"]  # fmt: skip
    added = base.select(*cols, pl.col("op_month").alias("month"), pl.lit("added").alias("event"))
    retired = base.filter(pl.col("ret_month").is_not_null()).select(
        *cols, pl.col("ret_month").alias("month"), pl.lit("retired").alias("event")
    )
    ev = pl.concat([added, retired]).filter(pl.col("month") >= date(FIRST_YEAR, 1, 1))
    sign = pl.when(pl.col("event") == "added").then(1.0).otherwise(-1.0)
    return ev.with_columns(
        pl.col("month").dt.year().cast(pl.Int64).alias("year"),
        (sign * pl.col("summer_mw")).alias("mw"),
        (sign * pl.col("summer_mw") * pl.col("cf") * HOURS / 1e6).alias("twh_per_yr"),
    ).sort("series", "month")


# --------------------------------------------------------------------------- comparisons


def yearly(changes: pl.DataFrame, annual: pl.DataFrame) -> pl.DataFrame:
    by_group = (
        changes.group_by("series", "year", "event", "group")
        .agg(pl.col("mw").sum())
        .with_columns((pl.col("event") + "_" + pl.col("group").str.to_lowercase()).alias("k"))
        .pivot("k", index=["series", "year"], values="mw")
        .with_columns(pl.exclude("series", "year").fill_null(0.0))
    )
    totals = changes.group_by("series", "year").agg(
        pl.col("mw").filter(pl.col("event") == "added").sum().alias("added_mw"),
        (-pl.col("mw").filter(pl.col("event") == "retired")).sum().alias("retired_mw"),
        pl.col("mw").filter(pl.col("group") == "Storage").sum().alias("net_storage_mw"),
        pl.col("twh_per_yr").sum().alias("net_new_supply_twh"),
    )
    demand = (
        annual.select(pl.col("ba").alias("series"), "year", "resid_twh_per_yr", "partial_year")
        .sort("series", "year")
        .with_columns(pl.col("resid_twh_per_yr").diff().over("series").alias("demand_change_twh"))
    )
    return (
        totals.join(by_group, on=["series", "year"], how="left")
        .join(demand, on=["series", "year"], how="full", coalesce=True)
        .filter(pl.col("year") >= FIRST_YEAR)
        .sort("series", "year")
    )


def months_online(op: pl.Expr, ret: pl.Expr, start: date, end_incl: date) -> pl.Expr:
    """Months of [start, end_incl] the unit was operating (online from op, off from ret)."""

    def idx(d):
        return d.dt.year().cast(pl.Int32) * 12 + d.dt.month().cast(pl.Int32)

    s, e = start.year * 12 + start.month, end_incl.year * 12 + end_incl.month + 1
    a = pl.max_horizontal(idx(op), pl.lit(s))
    b = pl.min_horizontal(pl.when(ret.is_null()).then(pl.lit(e)).otherwise(idx(ret)), pl.lit(e))
    return pl.max_horizontal(b - a, pl.lit(0))


def regional_cf(gen: pl.DataFrame, years=(2023, 2024, 2025), min_mw: float = 50.0) -> pl.DataFrame:
    """State capacity factors for solar PV and wind (Prompt 15 robustness).

    State utility-scale generation (EIA API electric-power-operational-data: SPV, WND)
    divided by the state's time-weighted 860M summer capacity in the same year, mean of
    2023-2025. States with under 50 MW of a technology fall back to the national factor.
    """
    from glg.ingest import eia_generation

    gen_state = eia_generation.state_generation()
    rows = []
    for y in years:
        cap = gen.filter(pl.col("cf_group").is_in(["solar_pv", "wind"])).with_columns(
            (
                months_online(
                    pl.col("op_month"), pl.col("ret_month"), date(y, 1, 1), date(y, 12, 1)
                )
                / 12
                * pl.col("summer_mw")
            ).alias("mw_tw")
        )
        c = cap.group_by("state", "cf_group").agg(pl.col("mw_tw").sum())
        rows.append(c.with_columns(pl.lit(y).alias("year")))
    cap = pl.concat(rows)
    j = cap.join(gen_state, on=["state", "cf_group", "year"], how="inner").filter(
        pl.col("mw_tw") >= min_mw
    )
    return (
        j.with_columns((pl.col("gen_mwh") / (pl.col("mw_tw") * 8760)).alias("cf_state"))
        .group_by("state", "cf_group")
        .agg(pl.col("cf_state").mean(), pl.col("mw_tw").last().alias("mw_latest"))
        .filter(pl.col("cf_state").is_between(0.05, 0.6))
        .sort("cf_group", "state")
    )


def since(
    gs: pl.DataFrame,
    cf: pl.DataFrame,
    ranking: pl.DataFrame,
    annual: pl.DataFrame,
    regional: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Demand growth vs new supply since the start year, also as % of start-year demand."""
    gs = gs.join(cf.select("cf_group", "cf"), on="cf_group", how="left")
    if regional is not None:
        gs = gs.join(
            regional.select("state", "cf_group", "cf_state"), on=["state", "cf_group"], how="left"
        )
        gs = gs.with_columns(pl.coalesce("cf_state", "cf").alias("cf")).drop("cf_state")
    base = {
        (r["ba"], r["year"]): r["actual_mw"] * HOURS / 1e6 for r in annual.iter_rows(named=True)
    }
    rows = []
    for r in ranking.iter_rows(named=True):
        name = g.ALIASES.get(r["ba"], r["ba"])
        u = gs.filter(pl.col("series") == name)
        if u.is_empty():
            continue
        l0, l1 = date.fromisoformat(r["last12_from"]), date.fromisoformat(r["last12_to"])
        y0 = r["from_year"]
        u = u.with_columns(
            (
                months_online(pl.col("op_month"), pl.col("ret_month"), l0, l1) / 12
                - months_online(
                    pl.col("op_month"), pl.col("ret_month"), date(y0, 1, 1), date(y0, 12, 1)
                )
                / 12
            ).alias("d_share")
        ).with_columns(
            (pl.col("summer_mw") * pl.col("d_share")).alias("d_mw"),
            (pl.col("summer_mw") * pl.col("d_share") * pl.col("cf") * HOURS / 1e6).alias("d_twh"),
        )
        grp = u.group_by("group").agg(pl.col("d_mw").sum(), pl.col("d_twh").sum())
        twh = dict(zip(grp["group"], grp["d_twh"], strict=True))
        mw = dict(zip(grp["group"], grp["d_mw"], strict=True))
        supply = u["d_twh"].sum()
        row = {
            "series": r["ba"],
            "from_year": y0,
            "last12_from": l0,
            "last12_to": l1,
            "demand_growth_pct": r["growth_pct"],
            "demand_mw_added": r["mw_added"],
            "demand_twh_added": r["twh_per_yr_added"],
            "new_supply_twh": supply,
            "new_supply_mw": u.filter(pl.col("group") != "Storage")["d_mw"].sum(),
            "new_storage_mw": mw.get("Storage", 0.0),
            "supply_minus_demand_twh": supply - r["twh_per_yr_added"],
            "supply_to_demand": supply / r["twh_per_yr_added"] if r["twh_per_yr_added"] else None,
        }
        d0 = base.get((r["ba"], y0)) or base.get((name, y0))
        row |= {
            "demand_from_twh": d0,
            "supply_pct": 100 * supply / d0 if d0 else None,
            "demand_twh_pct": 100 * r["twh_per_yr_added"] / d0 if d0 else None,
            "gap_pct": 100 * (supply - r["twh_per_yr_added"]) / d0 if d0 else None,
        }
        row |= {f"twh_{k.lower()}": twh.get(k, 0.0) for k in GROUPS if k != "Storage"}
        row |= {f"mw_{k.lower()}": mw.get(k, 0.0) for k in GROUPS}
        rows.append(row)
    return pl.DataFrame(rows)


def run() -> pl.DataFrame:
    gen = pl.read_parquet(eia860m.GENERATORS_PATH)
    cf = pl.read_csv(eia860m.CF_PATH)
    ranking = pl.read_csv(g.RANKING_PATH)
    annual = pl.read_csv(g.ANNUAL_PATH)
    series = sorted({g.ALIASES.get(b, b) for b in ranking["ba"]})
    gs = assign_series(gen, series)
    changes = capacity_changes(gs, cf)
    yr = yearly(changes, annual)
    s = since(gs, cf, ranking, annual).join(
        ranking.select(pl.col("ba").alias("series"), "headline", "role"), on="series", how="left"
    )
    reg = regional_cf(gen)
    s_reg = since(gs, cf, ranking, annual, regional=reg)
    reg.write_csv(REGIONAL_CF_PATH, float_precision=4)
    s_reg.sort("demand_twh_added", descending=True).write_csv(
        SINCE_REGIONAL_PATH, float_precision=3
    )
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    changes.write_parquet(CHANGES_PATH)
    yr.write_csv(YEARLY_PATH, float_precision=3)
    s.sort("demand_twh_added", descending=True).write_csv(SINCE_PATH, float_precision=3)
    log.info("DOM: %d generators in %d PJM counties", gs.filter(pl.col("series") == "DOM").height,
             dom_counties().height)  # fmt: skip
    with pl.Config(tbl_rows=40, tbl_cols=14, tbl_width_chars=240):
        print(
            s.sort("demand_twh_added", descending=True)
            .select(
                "series", "demand_growth_pct", "demand_twh_added", "new_supply_twh",
                "supply_to_demand", "twh_solar", "twh_wind", "twh_gas", "twh_coal",
                "new_supply_mw", "new_storage_mw",
            )
            .with_columns(pl.col(pl.Float64).round(2))
        )  # fmt: skip
    return s


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()


if __name__ == "__main__":
    main()
