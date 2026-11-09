"""Balancing authority -> county mapping with weights, plus county population by year.

Method (see DATA.md, "BA -> county mapping"):

1. EIA-861 "Sales to Ultimate Customers" gives each utility's residential customers per
   state and balancing authority (BA). Energy-only retailers (Texas REPs, California CCAs)
   have no county list; their customers are already counted by the wires utility, so they
   drop out when joined to territories.
2. EIA-861 "Service Territory" lists the counties each utility serves in each state. A
   utility's customers are spread over its counties in proportion to county population.
3. A county's weight for a BA = that BA's share of the county's allocated customers.
   Weights over all BAs sum to 1 for every county with a known BA.
4. Overrides for areas EIA-861 can't separate: the PJM Dominion zone (DOM), and Duke
   Energy Progress West (CPLW), which EIA-861 reports under CPLE.

The map is built per year (2015-2025), so real boundary changes are followed (e.g. Gulf
Power moved from SOCO to FPL in 2022). Weather features should weight counties by
weight x population.

Run: python -m glg.build.ba_weather_map
"""

import logging

import geopandas as gpd
import numpy as np
import polars as pl

from glg.config import OUTPUTS_DIR, REFERENCE_DIR
from glg.ingest import census, eia861

log = logging.getLogger(__name__)

YEARS = range(2015, 2026)  # EIA-861 years mapped; later years reuse the last one
SOURCE_861 = "EIA-861 service territory + sales (via PUDL), customer-weighted"

# County FIPS that changed before the 2020 boundaries.
FIPS_FIXES = {
    "51515": "51019",  # Bedford City, VA merged into Bedford County (2013)
    "46113": "46102",  # Shannon County, SD renamed Oglala Lakota County (2015)
}

# DOM (PJM Dominion zone): anchor utilities whose PJM transmission zone is unambiguous.
VEPCO = 19876  # Virginia Electric & Power Co (Dominion Energy Virginia) -> DOM
ZONE_ANCHORS = {VEPCO: "DOM", 733: "AEP", 84: "DPL"}  # Appalachian Power; A&N Electric Coop
DOM_STATES = ("VA", "NC")

# CPLW: Duke Energy Progress's western BA (Asheville). EIA-861 reports it as CPLE.
CPLW_LON = -81.5  # CPLE's westernmost counties sit near -80; CPLW's near -82.5
CPLW_STATE = "NC"

MAP_PATH = REFERENCE_DIR / "ba_county_map.csv"
POP_PATH = REFERENCE_DIR / "county_population.csv"
SUMMARY_PATH = OUTPUTS_DIR / "ba_mapping_summary.csv"


# --------------------------------------------------------------------------- population


def _popest(path, years) -> pl.DataFrame:
    d = pl.read_csv(path, encoding="latin1", infer_schema_length=0)
    d = d.filter(pl.col("SUMLEV") == "050")
    cols = [f"POPESTIMATE{y}" for y in years]
    return (
        d.select(
            (pl.col("STATE") + pl.col("COUNTY")).alias("county_fips"),
            pl.col("STNAME").alias("state_name"),
            pl.col("CTYNAME").alias("county_name"),
            *cols,
        )
        .unpivot(index=["county_fips", "state_name", "county_name"], value_name="population")
        .with_columns(
            pl.col("variable").str.slice(-4).cast(pl.Int32).alias("year"),
            pl.col("population").cast(pl.Int64),
        )
        .drop("variable")
    )


def connecticut_to_counties(pop: pl.DataFrame, shares: pl.DataFrame) -> pl.DataFrame:
    """Replace CT planning regions (09110-09190, used from Vintage 2022) with the 8 counties.

    Each year's state total is split by the counties' shares in the last county-based year.
    """
    regions = pop.filter(pl.col("county_fips").str.starts_with("09"))
    totals = regions.group_by("year").agg(pl.col("population").sum().alias("ct_total"))
    counties = (
        shares.join(totals, how="cross")
        .with_columns(
            (pl.col("share") * pl.col("ct_total")).round().cast(pl.Int64).alias("population")
        )
        .select(pop.columns)
    )
    return pl.concat([pop.filter(~pl.col("county_fips").str.starts_with("09")), counties])


def _harmonise(pop: pl.DataFrame) -> pl.DataFrame:
    """Apply FIPS fixes and sum any counties that merge."""
    return (
        pop.with_columns(pl.col("county_fips").replace(FIPS_FIXES), pl.col("year").cast(pl.Int32))
        .group_by("county_fips", "year")
        .agg(pl.col("state_name").first(), pl.col("county_name").last(), pl.col("population").sum())
    )


def bridge_to_2020(old: pl.DataFrame, new: pl.DataFrame) -> pl.DataFrame:
    """Close the gap between the 2010s estimates and the 2020-census-based series.

    Vintage 2019 estimates drift from the 2020 census (the national count came in higher),
    so stacking the two series puts a step at 2020. Like the Census intercensal method, we
    spread the "error of closure" linearly: 2010 (a census year) is unchanged, and 2019 is
    scaled most. Implied 2020 under the old series = 2019 x (2019 / 2018).
    """
    p = old.pivot(on="year", index="county_fips", values="population")
    ratio = (
        p.select(
            "county_fips",
            (pl.col("2019") * pl.col("2019") / pl.col("2018")).alias("implied_2020"),
        )
        .join(
            new.filter(pl.col("year") == 2020).select(
                "county_fips", pl.col("population").alias("p2020")
            ),
            on="county_fips",
            how="left",
        )
        .with_columns((pl.col("p2020") / pl.col("implied_2020")).fill_null(1.0).alias("ratio"))
    )
    return (
        old.join(ratio.select("county_fips", "ratio"), on="county_fips", how="left")
        .with_columns(
            (
                pl.col("population")
                * (1 + (pl.col("ratio").fill_null(1.0) - 1) * (pl.col("year") - 2010) / 10)
            )
            .round()
            .cast(pl.Int64)
            .alias("population")
        )
        .drop("ratio")
    )


def build_population() -> pl.DataFrame:
    old = _popest(census.path("co-est2019-alldata.csv"), range(2010, 2020))
    new = _popest(census.path("co-est2025-alldata.csv"), range(2020, 2026))
    ct = old.filter(pl.col("county_fips").str.starts_with("09"), pl.col("year") == 2019)
    shares = ct.select(
        "county_fips",
        "state_name",
        "county_name",
        (pl.col("population") / pl.col("population").sum()).alias("share"),
    )
    old, new = _harmonise(old), _harmonise(connecticut_to_counties(new, shares))
    old = bridge_to_2020(old, new).with_columns(
        pl.lit("Vintage 2019, bridged to 2020").alias("source")
    )
    new = new.with_columns(pl.lit("Vintage 2025").alias("source"))
    return pl.concat([old, new.select(old.columns)]).sort("county_fips", "year")


# --------------------------------------------------------------------------- geography


def county_shapes() -> gpd.GeoDataFrame:
    g = gpd.read_file(f"zip://{census.path('cb_2020_us_county_500k.zip')}")
    return g.rename(columns={"GEOID": "county_fips", "STUSPS": "state"})[
        ["county_fips", "state", "NAME", "geometry"]
    ]


def county_centroids() -> pl.DataFrame:
    g = county_shapes().to_crs(5070)  # equal-area CONUS, for sensible centroids
    c = g.geometry.centroid.to_crs(4326)
    return pl.DataFrame({"county_fips": g.county_fips, "lon": c.x.values, "lat": c.y.values})


# --------------------------------------------------------------------------- EIA-861


def residential_customers(sales: pl.DataFrame) -> pl.DataFrame:
    """Residential customers per (year, utility, state, BA), excluding energy-only service.

    BA codes are smoothed: a run of years whose code differs from an identical code on
    both sides is a reporting blip and takes the surrounding code (e.g. Colorado Springs
    reports WACM except PSCO in 2019-21). Permanent moves (Gulf Power SOCO -> FPL) are kept.
    """
    c = (
        sales.filter(
            pl.col("customer_class") == "residential",
            pl.col("service_type").cast(pl.Utf8) != "energy",
        )
        .with_columns(
            pl.col("report_date").dt.year().cast(pl.Int32).alias("year"),
            pl.col("balancing_authority_code_eia").fill_null("UNK").alias("ba"),
        )
        .group_by("year", "utility_id_eia", "state", "ba")
        .agg(pl.col("customers").sum())
        .filter(pl.col("customers") > 0)
    )
    return smooth_ba_blips(c)


def smooth_ba_blips(c: pl.DataFrame) -> pl.DataFrame:
    """Replace bracketed runs of a utility-state's BA code (only where it has one BA a year)."""
    single = (
        c.group_by("utility_id_eia", "state", "year")
        .agg(pl.col("ba").first(), pl.len().alias("n"))
        .filter(pl.col("n") == 1)
        .sort("utility_id_eia", "state", "year")
    )
    fixes = []
    for (u, s), g in single.group_by("utility_id_eia", "state"):
        years, bas = g["year"].to_list(), g["ba"].to_list()
        runs = []  # [code, first index, last index]
        for i, b in enumerate(bas):
            if runs and runs[-1][0] == b and years[i] == years[i - 1] + 1:
                runs[-1][2] = i
            else:
                runs.append([b, i, i])
        for k in range(1, len(runs) - 1):
            before, run, after = runs[k - 1], runs[k], runs[k + 1]
            contiguous = (
                years[run[1]] == years[before[2]] + 1 and years[after[1]] == years[run[2]] + 1
            )
            if before[0] == after[0] != run[0] and contiguous:
                fixes += [(u, s, years[i], before[0]) for i in range(run[1], run[2] + 1)]
    if not fixes:
        return c
    f = pl.DataFrame(
        fixes, schema=["utility_id_eia", "state", "year", "ba_fixed"], orient="row"
    ).with_columns(pl.col("year").cast(pl.Int32))
    log.info("Smoothed %d utility-state-years of BA-code blips", f.height)
    return (
        c.join(f, on=["utility_id_eia", "state", "year"], how="left")
        .with_columns(pl.coalesce("ba_fixed", "ba").alias("ba"))
        .drop("ba_fixed")
    )


def county_name_fixes(pop: pl.DataFrame) -> pl.DataFrame:
    """Map (state FIPS, name) of same-named independent cities back to the county.

    EIA-861 lists "Fairfax", "Roanoke", "St. Louis", "Baltimore", ... and PUDL resolves
    them to the independent city (51600, 51770, 29510, 24510, ...), dropping the county
    (Fairfax County, 51059, is the heart of Northern Virginia). EIA spells the cities with
    "City" when they mean only the city, so a bare name is read as both city and county.
    """
    names = pop.select("county_fips", "county_name").unique("county_fips")
    cities = names.filter(pl.col("county_name").str.ends_with(" city")).with_columns(
        pl.col("county_name").str.strip_suffix(" city").alias("base")
    )
    counties = names.filter(pl.col("county_name").str.ends_with(" County")).with_columns(
        pl.col("county_name").str.strip_suffix(" County").alias("base"),
        pl.col("county_fips").str.slice(0, 2).alias("st"),
    )
    return (
        cities.with_columns(pl.col("county_fips").str.slice(0, 2).alias("st"))
        .join(counties, on=["st", "base"], suffix="_county")
        .select(
            pl.col("county_fips").alias("city_fips"),
            pl.col("base").str.to_lowercase().alias("name_lc"),
            "county_fips_county",
        )
    )


def utility_counties(
    territory: pl.DataFrame, year: int, fixes: pl.DataFrame | None = None
) -> pl.DataFrame:
    t = territory.filter(pl.col("report_date").dt.year() == year).with_columns(
        pl.col("county_id_fips").replace(FIPS_FIXES).alias("county_fips")
    )
    if fixes is not None:
        t = t.with_columns(pl.col("county").str.to_lowercase().str.strip_chars().alias("name_lc"))
        t = t.join(
            fixes, left_on=["county_fips", "name_lc"], right_on=["city_fips", "name_lc"], how="left"
        )
        # The bare name is ambiguous: keep the city and add the same-named county.
        extra = t.filter(pl.col("county_fips_county").is_not_null()).with_columns(
            pl.col("county_fips_county").alias("county_fips")
        )
        t = pl.concat([t, extra])
    return (
        t.filter(pl.col("county_fips").is_not_null())
        .select("utility_id_eia", "state", "county_fips")
        .unique()
    )


def utility_ba_assn(assn: pl.DataFrame, ba_codes: pl.DataFrame, year: int) -> pl.DataFrame:
    """(utility, state) -> BA code from EIA-861's balancing-authority association table."""
    codes = ba_codes.filter(pl.col("report_date").dt.year() == year).select(
        "balancing_authority_id_eia", pl.col("balancing_authority_code_eia").alias("ba")
    )
    return (
        assn.filter(pl.col("report_date").dt.year() == year)
        .join(codes, on="balancing_authority_id_eia", how="inner")
        .filter(pl.col("ba").is_not_null(), pl.col("ba") != "UNK")
        .select("utility_id_eia", "state", "ba")
        .unique()
    )


def impute_wires_only(
    cust: pl.DataFrame, counties: pl.DataFrame, assn_ba: pl.DataFrame, pop: pl.DataFrame
) -> pl.DataFrame:
    """Give customers to wires-only utilities (Texas TDUs: Oncor, CenterPoint, ...).

    They list counties but report no residential customers; retailers report those
    customers instead, with no county list. The retailers' pool in each (state, BA) is
    shared among that state's wires-only utilities in the BA by their counties' population.
    """
    has_terr = counties.select("utility_id_eia", "state").unique()
    pool = (
        cust.join(has_terr, on=["utility_id_eia", "state"], how="anti")
        .filter(pl.col("ba") != "UNK")
        .group_by("state", "ba")
        .agg(pl.col("customers").sum().alias("pool"))
    )
    wires = (
        has_terr.join(
            cust.select("utility_id_eia", "state").unique(),
            on=["utility_id_eia", "state"],
            how="anti",
        )
        .join(assn_ba, on=["utility_id_eia", "state"], how="inner")
        .join(pool, on=["state", "ba"], how="inner")
    )
    if wires.is_empty():
        return cust.head(0)
    # Reach: each county's population split equally among the utilities that serve it, so
    # a small co-op touching a big county doesn't get a big-county share of the pool.
    reach = (
        counties.join(pop.select("county_fips", "population"), on="county_fips", how="left")
        .with_columns((pl.col("population").fill_null(0) / pl.len().over("county_fips")).alias("p"))
        .group_by("utility_id_eia", "state")
        .agg(pl.col("p").sum().alias("reach"))
    )
    w = wires.join(reach, on=["utility_id_eia", "state"], how="left").with_columns(
        (pl.col("pool") * pl.col("reach") / pl.col("reach").sum().over("state", "ba"))
        .round()
        .cast(pl.Int64)
        .alias("customers")
    )
    return w.select("utility_id_eia", "state", "ba", "customers").filter(pl.col("customers") > 0)


def rake(
    links: np.ndarray, row_totals: np.ndarray, col_totals: np.ndarray, iters: int = 200
) -> np.ndarray:
    """Iterative proportional fitting on a utility x county matrix.

    links is the starting matrix (zero where a utility doesn't serve a county). Rows are
    matched to utility customer totals, columns to county targets; ends on a row step so
    every utility's customers are kept exactly.
    """
    x = links.astype(float).copy()
    for _ in range(iters):
        cs = x.sum(axis=0)
        x *= np.divide(col_totals, cs, out=np.ones_like(cs), where=cs > 0)[None, :]
        rs = x.sum(axis=1)
        prev = x.copy()
        x *= np.divide(row_totals, rs, out=np.ones_like(rs), where=rs > 0)[:, None]
        if np.abs(x - prev).max() < 1e-6 * max(row_totals.max(), 1):
            break
    return x


def allocate(customers: pl.DataFrame, counties: pl.DataFrame, pop: pl.DataFrame) -> pl.DataFrame:
    """Place each (utility, state, BA)'s customers in its counties.

    Per state, a utility's customers start spread by county population, then are raked so
    each county's total customers match its share of the state's population. A utility
    that lists a big county it only partly serves keeps most customers where it dominates.
    Returns one row per (utility, state, ba, county_fips) with allocated `cust`.
    """
    us = customers.group_by("utility_id_eia", "state").agg(pl.col("customers").sum().alias("tot"))
    links = (
        counties.join(us, on=["utility_id_eia", "state"], how="inner")
        .join(pop.select("county_fips", "population"), on="county_fips", how="left")
        .with_columns(pl.col("population").fill_null(1).clip(lower_bound=1))
    )
    out = []
    for (state,), g in links.group_by("state"):
        utils = g.select("utility_id_eia", "tot").unique().sort("utility_id_eia")
        cnty = g.select("county_fips", "population").unique().sort("county_fips")
        ui = {u: i for i, u in enumerate(utils["utility_id_eia"])}
        ci = {c: i for i, c in enumerate(cnty["county_fips"])}
        pop_c = cnty["population"].to_numpy().astype(float)
        m = np.zeros((len(ui), len(ci)))
        m[[ui[u] for u in g["utility_id_eia"]], [ci[c] for c in g["county_fips"]]] = pop_c[
            [ci[c] for c in g["county_fips"]]
        ]
        rows = utils["tot"].to_numpy().astype(float)
        cols = pop_c * rows.sum() / pop_c.sum()
        x = rake(m, rows, cols)
        r, k = np.nonzero(x)
        out.append(
            pl.DataFrame(
                {
                    "utility_id_eia": utils["utility_id_eia"].to_numpy()[r],
                    "state": state,
                    "county_fips": cnty["county_fips"].to_numpy()[k],
                    "ucust": x[r, k],
                }
            )
        )
    placed = pl.concat(out)
    split = customers.with_columns(
        (pl.col("customers") / pl.col("customers").sum().over("utility_id_eia", "state")).alias(
            "ba_share"
        )
    )
    return (
        split.join(placed, on=["utility_id_eia", "state"], how="inner")
        .with_columns((pl.col("ucust") * pl.col("ba_share")).alias("cust"))
        .select("utility_id_eia", "state", "ba", "county_fips", "cust")
    )


def ba_weights(alloc: pl.DataFrame) -> pl.DataFrame:
    """County weight per BA: BA's share of the county's allocated customers (UNK excluded)."""
    known = alloc.filter(pl.col("ba") != "UNK")
    by_ba = known.group_by("ba", "county_fips").agg(pl.col("cust").sum())
    return by_ba.with_columns(
        (pl.col("cust") / pl.col("cust").sum().over("county_fips")).alias("weight")
    )


def dom_zone_fraction(alloc: pl.DataFrame, centroids: pl.DataFrame) -> pl.DataFrame:
    """Fraction of each VA/NC county's PJM customers that sit in the Dominion (DOM) zone.

    Counties served by an anchor utility take VEPCO's share of anchor customers. Counties
    served only by co-ops/municipals take the zone of the nearest anchored county.
    """
    pjm = alloc.filter(pl.col("ba") == "PJM", pl.col("state").is_in(DOM_STATES))
    pjm_counties = pjm.select("county_fips").unique()
    anchors = (
        pjm.filter(pl.col("utility_id_eia").is_in(list(ZONE_ANCHORS)))
        .group_by("county_fips")
        .agg(
            (
                pl.col("cust").filter(pl.col("utility_id_eia") == VEPCO).sum()
                / pl.col("cust").sum()
            ).alias("dom_frac")
        )
    )
    frac = pjm_counties.join(anchors, on="county_fips", how="left").join(
        centroids, on="county_fips", how="left"
    )
    have = frac.filter(pl.col("dom_frac").is_not_null())
    missing = frac.filter(pl.col("dom_frac").is_null())
    if missing.height and have.height:
        a = have.select("lon", "lat").to_numpy()
        m = missing.select("lon", "lat").to_numpy()
        coslat = np.cos(np.radians(m[:, 1:2]))
        d2 = ((m[:, None, 0] - a[None, :, 0]) * coslat) ** 2 + (m[:, None, 1] - a[None, :, 1]) ** 2
        nearest = have["dom_frac"].to_numpy()[d2.argmin(axis=1)]
        missing = missing.with_columns(pl.Series("dom_frac", nearest))
    return pl.concat([have, missing]).select("county_fips", "dom_frac")


def add_dom(weights: pl.DataFrame, alloc: pl.DataFrame, centroids: pl.DataFrame) -> pl.DataFrame:
    frac = dom_zone_fraction(alloc, centroids)
    dom = (
        weights.filter(pl.col("ba") == "PJM")
        .join(frac, on="county_fips", how="inner")
        .with_columns(
            pl.lit("DOM").alias("ba"),
            (pl.col("weight") * pl.col("dom_frac")).alias("weight"),
            (pl.col("cust") * pl.col("dom_frac")).alias("cust"),
        )
        .filter(pl.col("weight") > 0)
        .drop("dom_frac")
    )
    return pl.concat([weights, dom])


def split_cplw(weights: pl.DataFrame, centroids: pl.DataFrame) -> pl.DataFrame:
    """Move CPLE counties in western North Carolina to CPLW."""
    west = centroids.filter(
        pl.col("lon") < CPLW_LON, pl.col("county_fips").str.starts_with("37")
    ).select("county_fips")
    is_west = (pl.col("ba") == "CPLE") & pl.col("county_fips").is_in(west["county_fips"].implode())
    return weights.with_columns(pl.when(is_west).then(pl.lit("CPLW")).otherwise("ba").alias("ba"))


def build_year(
    year, cust_all, territory, assn, ba_codes, pop, centroids
) -> tuple[pl.DataFrame, dict]:
    pop_y = pop.filter(pl.col("year") == year)
    cust = cust_all.filter(pl.col("year") == year).drop("year")
    counties = utility_counties(territory, year, county_name_fixes(pop))
    wires = impute_wires_only(cust, counties, utility_ba_assn(assn, ba_codes, year), pop_y)
    cust = pl.concat([cust, wires])
    alloc = allocate(cust, counties, pop_y)
    w = ba_weights(alloc)
    w = add_dom(w, alloc, centroids)
    w = split_cplw(w, centroids)
    w = w.group_by("ba", "county_fips").agg(pl.col("cust").sum(), pl.col("weight").sum())
    diag = {
        "year": year,
        "customers_total": cust["customers"].sum() - wires["customers"].sum(),
        "customers_imputed": wires["customers"].sum(),
        "customers_mapped": alloc.filter(pl.col("ba") != "UNK")["cust"].sum(),
        "customers_unk": alloc.filter(pl.col("ba") == "UNK")["cust"].sum(),
    }
    return w.with_columns(pl.lit(year, pl.Int32).alias("year")), diag


def summarise(m: pl.DataFrame, pop: pl.DataFrame) -> pl.DataFrame:
    """Per BA-year: counties, weighted population, customers, and people per customer."""
    j = m.join(
        pop.select("county_fips", "year", "population"), on=["county_fips", "year"], how="left"
    )
    return (
        j.group_by("ba", "year")
        .agg(
            pl.len().alias("n_counties"),
            (pl.col("weight") == 1).sum().alias("n_whole_counties"),
            (pl.col("weight") * pl.col("population"))
            .sum()
            .round()
            .cast(pl.Int64)
            .alias("population"),
            pl.col("res_customers_alloc").sum().alias("res_customers"),
        )
        .with_columns(
            (pl.col("population") / pl.col("res_customers")).round(2).alias("people_per_customer")
        )
        .sort("ba", "year")
    )


def build() -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    pop = build_population()
    cust_all = residential_customers(
        pl.read_parquet(eia861.table_path("core_eia861__yearly_sales"))
    )
    assn = pl.read_parquet(eia861.table_path("core_eia861__assn_balancing_authority"))
    ba_codes = pl.read_parquet(eia861.table_path("core_eia861__yearly_balancing_authority"))
    territory = pl.read_parquet(eia861.table_path("core_eia861__yearly_service_territory"))
    shapes = county_shapes()
    centroids = county_centroids()
    names = pl.DataFrame(
        {"county_fips": shapes.county_fips, "state": shapes.state, "county": shapes.NAME}
    )

    parts, diags = [], []
    for y in YEARS:
        w, d = build_year(y, cust_all, territory, assn, ba_codes, pop, centroids)
        parts.append(w)
        diags.append(d)
        log.info(
            "%d: %d BA-county rows; %.1f%% of residential customers placed"
            " (+%.1fM imputed to wires-only utilities), %.1f%% UNK BA",
            y,
            w.height,
            100 * (d["customers_mapped"] - d["customers_imputed"]) / d["customers_total"],
            d["customers_imputed"] / 1e6,
            100 * d["customers_unk"] / d["customers_total"],
        )
    m = pl.concat(parts).join(names, on="county_fips", how="left")
    m = m.with_columns(
        pl.when(pl.col("ba") == "DOM")
        .then(pl.lit(SOURCE_861 + "; DOM zone by Dominion's share of anchor utilities"))
        .when(pl.col("ba") == "CPLW")
        .then(pl.lit(SOURCE_861 + f"; CPLE counties west of {CPLW_LON} deg moved to CPLW"))
        .otherwise(pl.lit(SOURCE_861))
        .alias("source")
    )
    m = m.select(
        "ba",
        "year",
        "county_fips",
        "state",
        "county",
        pl.col("weight").round(4),
        pl.col("cust").round().cast(pl.Int64).alias("res_customers_alloc"),
        "source",
    ).filter(pl.col("weight") >= 1e-4)
    m = m.sort("ba", "year", "county_fips")
    return m, pop, summarise(m, pop)


def population_weights(
    ba_map: pl.DataFrame, pop: pl.DataFrame, pop_year: int | None = None
) -> pl.DataFrame:
    """County weights for weather aggregation: weight x population, normalised per BA-year.

    pop_year fixes the population year (so population shifts don't move the weather series);
    None uses each map year's own population. Map years after the last EIA-861 year reuse it.
    """
    if pop_year is None:
        p = pop.select("county_fips", "year", "population")
        on = ["county_fips", "year"]
    else:
        p = pop.filter(pl.col("year") == pop_year).select("county_fips", "population")
        on = ["county_fips"]
    j = ba_map.join(p, on=on, how="left").with_columns(
        (pl.col("weight") * pl.col("population").fill_null(0)).alias("w")
    )
    return j.with_columns(
        (pl.col("w") / pl.col("w").sum().over("ba", "year")).alias("pop_weight")
    ).select("ba", "year", "county_fips", "pop_weight")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    eia861.download()
    census.download()
    m, pop, summary = build()
    REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    m.write_csv(MAP_PATH)
    pop.write_csv(POP_PATH)
    summary.write_csv(SUMMARY_PATH)
    log.info(
        "Wrote %s (%d rows), %s, %s", MAP_PATH.name, m.height, POP_PATH.name, SUMMARY_PATH.name
    )


if __name__ == "__main__":
    main()
