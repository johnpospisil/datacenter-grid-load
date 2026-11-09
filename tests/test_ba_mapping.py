"""BA -> county mapping: unit tests of each rule, and checks of the built map."""

from datetime import date

import numpy as np
import polars as pl
import pytest

from glg.build import ba_weather_map as bwm

# --------------------------------------------------------------------------- unit tests


def test_rake_keeps_utility_totals_and_moves_sliver_utility_out_of_big_county():
    # Utility A serves big county 0 only; utility B lists county 0 (a sliver) and county 1.
    pop = np.array([1000.0, 100.0])
    links = np.array([[1000.0, 0.0], [1000.0, 100.0]])
    rows = np.array([1000.0, 100.0])  # B's 100 customers really sit in county 1
    x = bwm.rake(links, rows, pop * rows.sum() / pop.sum())
    np.testing.assert_allclose(x.sum(axis=1), rows)
    # Population-proportional allocation would put 91 of B's customers in county 0.
    assert x[1, 1] > 90
    assert x[0, 1] == 0


def _cust(rows):
    return pl.DataFrame(
        rows, schema=["year", "utility_id_eia", "state", "ba", "customers"], orient="row"
    ).with_columns(pl.col("year").cast(pl.Int32))


def test_smooth_ba_blips_fixes_bracketed_run_only():
    years = range(2015, 2026)
    blip = ["WACM"] * 4 + ["PSCO"] * 3 + ["WACM"] * 4  # Colorado Springs pattern
    move = ["SOCO"] * 7 + ["FPL"] * 4  # Gulf Power: permanent
    edge = ["UNK"] + ["MISO"] * 10  # leading run is not bracketed
    rows = [(y, 1, "CO", b, 10) for y, b in zip(years, blip, strict=True)]
    rows += [(y, 2, "FL", b, 10) for y, b in zip(years, move, strict=True)]
    rows += [(y, 3, "MN", b, 10) for y, b in zip(years, edge, strict=True)]
    out = bwm.smooth_ba_blips(_cust(rows)).sort("utility_id_eia", "year")
    assert out.filter(pl.col("utility_id_eia") == 1)["ba"].unique().to_list() == ["WACM"]
    assert out.filter(pl.col("utility_id_eia") == 2)["ba"].to_list() == move
    assert out.filter(pl.col("utility_id_eia") == 3)["ba"].to_list() == edge


def test_smooth_ba_blips_ignores_gaps_in_years():
    rows = [(2015, 1, "CO", "WACM", 1), (2017, 1, "CO", "PSCO", 1), (2018, 1, "CO", "WACM", 1)]
    out = bwm.smooth_ba_blips(_cust(rows)).sort("year")
    assert out["ba"].to_list() == ["WACM", "PSCO", "WACM"]


POP = pl.DataFrame(
    {
        "county_fips": ["51059", "51600", "51760", "51159", "24005", "24510"],
        "county_name": [
            "Fairfax County",
            "Fairfax city",
            "Richmond city",
            "Richmond County",
            "Baltimore County",
            "Baltimore city",
        ],
        "population": [1_150_000, 25_000, 230_000, 9_000, 850_000, 570_000],
    }
)


def test_ambiguous_city_names_map_to_both_city_and_county():
    terr = pl.DataFrame(
        {
            "report_date": [date(2024, 1, 1)] * 4,
            "utility_id_eia": [1, 1, 2, 3],
            "state": ["VA", "VA", "VA", "MD"],
            "county": ["Fairfax", "Richmond City", "Richmond", "Baltimore"],
            "county_id_fips": ["51600", "51760", "51760", "24510"],
        }
    )
    out = bwm.utility_counties(terr, 2024, bwm.county_name_fixes(POP))
    got = set(out.select("utility_id_eia", "county_fips").iter_rows())
    assert got == {
        (1, "51600"),
        (1, "51059"),  # "Fairfax" -> city and Fairfax County
        (1, "51760"),  # "Richmond City" -> city only
        (2, "51760"),
        (2, "51159"),  # bare "Richmond" -> city and Richmond County
        (3, "24510"),
        (3, "24005"),
    }


def test_connecticut_planning_regions_become_counties():
    pop = pl.DataFrame(
        {
            "county_fips": ["09110", "09120", "01001"],
            "state_name": ["Connecticut", "Connecticut", "Alabama"],
            "county_name": ["Capitol PR", "Bridgeport PR", "Autauga County"],
            "population": [600, 400, 50],
            "year": [2023, 2023, 2023],
        }
    )
    shares = pl.DataFrame(
        {
            "county_fips": ["09001", "09003"],
            "state_name": ["Connecticut"] * 2,
            "county_name": ["Fairfield County", "Hartford County"],
            "share": [0.3, 0.7],
        }
    )
    out = bwm.connecticut_to_counties(pop, shares).sort("county_fips")
    assert out["county_fips"].to_list() == ["01001", "09001", "09003"]
    assert out["population"].to_list() == [50, 300, 700]


def test_bridge_leaves_2010_alone_and_closes_gap_at_2020():
    old = pl.DataFrame(
        {
            "county_fips": ["X"] * 10,
            "year": list(range(2010, 2020)),
            "population": [1000 + 10 * i for i in range(10)],
        }
    ).with_columns(pl.col("year").cast(pl.Int32))
    implied_2020 = 1090 * 1090 / 1080
    new = pl.DataFrame({"county_fips": ["X"], "year": [2020], "population": [1150]})
    out = bwm.bridge_to_2020(old, new).sort("year")["population"].to_list()
    assert out[0] == 1000
    r = 1150 / implied_2020
    assert out[-1] == round(1090 * (1 + (r - 1) * 0.9))


def test_impute_wires_only_shares_retailer_pool():
    cust = pl.DataFrame(
        {
            "utility_id_eia": [10, 11, 20],  # 10, 11 retailers (no counties), 20 a co-op
            "state": ["TX"] * 3,
            "ba": ["ERCO"] * 3,
            "customers": [600, 400, 50],
        }
    )
    counties = pl.DataFrame(
        {
            "utility_id_eia": [1, 2, 20],  # 1, 2 wires-only (no customers)
            "state": ["TX"] * 3,
            "county_fips": ["A", "B", "B"],
        }
    )
    assn = pl.DataFrame({"utility_id_eia": [1, 2], "state": ["TX"] * 2, "ba": ["ERCO"] * 2})
    pop = pl.DataFrame({"county_fips": ["A", "B"], "population": [3000, 2000]})
    out = bwm.impute_wires_only(cust, counties, assn, pop).sort("utility_id_eia")
    # Reach: A=3000 (utility 1 alone); B=2000 shared by 2 and the co-op -> 1000.
    assert out["customers"].to_list() == [750, 250]


def test_population_weights_sum_to_one():
    m = pl.DataFrame(
        {
            "ba": ["X", "X", "Y"],
            "year": [2020] * 3,
            "county_fips": ["A", "B", "B"],
            "weight": [1.0, 0.5, 0.5],
        }
    )
    pop = pl.DataFrame({"county_fips": ["A", "B"], "year": [2020] * 2, "population": [100, 300]})
    w = bwm.population_weights(m, pop, pop_year=2020).sort("ba", "county_fips")
    assert w["pop_weight"].to_list() == pytest.approx([0.4, 0.6, 1.0])


# --------------------------------------------------------------------------- the built map

needs_map = pytest.mark.skipif(
    not bwm.MAP_PATH.exists(), reason="run python -m glg.build.ba_weather_map first"
)


@pytest.fixture(scope="module")
def ba_map():
    return pl.read_csv(bwm.MAP_PATH, schema_overrides={"county_fips": pl.Utf8})


@pytest.fixture(scope="module")
def population():
    return pl.read_csv(bwm.POP_PATH, schema_overrides={"county_fips": pl.Utf8})


def _w(m, fips, year, ba):
    r = m.filter(pl.col("county_fips") == fips, pl.col("year") == year, pl.col("ba") == ba)
    return r["weight"].sum()


@needs_map
def test_weights_per_county_sum_to_one(ba_map):
    # DOM is a zone inside PJM, so it is left out of the sum.
    s = (
        ba_map.filter(pl.col("ba") != "DOM")
        .group_by("county_fips", "year")
        .agg(pl.col("weight").sum())
    )
    assert s["weight"].min() > 0.99 and s["weight"].max() < 1.01


@needs_map
def test_every_conus_county_is_mapped(ba_map, population):
    conus = population.filter(
        pl.col("year") == 2024, ~pl.col("county_fips").str.slice(0, 2).is_in(["02", "15", "72"])
    )
    for y in bwm.YEARS:
        mapped = ba_map.filter(pl.col("year") == y).select("county_fips").unique()
        missing = conus.join(mapped, on="county_fips", how="anti")
        # EIA-861 skips a few small Virginia cities in single years (e.g. Lynchburg in 2025).
        assert missing.height <= 3, (y, missing["county_name"].to_list())
        assert missing["population"].sum() < 100_000, (y, missing["county_name"].to_list())


@needs_map
@pytest.mark.parametrize(
    "fips, ba, year, low",
    [
        ("51107", "DOM", 2024, 0.95),  # Loudoun, VA: Data Center Alley
        ("51059", "DOM", 2024, 0.95),  # Fairfax, VA
        ("51153", "DOM", 2024, 0.95),  # Prince William, VA
        ("51760", "DOM", 2024, 0.95),  # Richmond city
        ("51001", "DOM", 2024, 0.0),  # Accomack (Eastern Shore) is DPL zone: checked below
        ("48113", "ERCO", 2024, 0.99),  # Dallas
        ("48029", "ERCO", 2024, 0.99),  # Bexar (San Antonio)
        ("48201", "ERCO", 2024, 0.80),  # Harris (Houston): Entergy's sliver leaks some to MISO
        ("12086", "FPL", 2024, 0.95),  # Miami-Dade
        ("12033", "SOCO", 2019, 0.95),  # Escambia (Pensacola): Gulf Power in SOCO ...
        ("12033", "FPL", 2023, 0.90),  # ... then FPL (a PowerSouth co-op stays in SOCO)
        ("37119", "DUK", 2024, 0.95),  # Mecklenburg (Charlotte)
        # Wake (Raleigh) is ~all CPLE, but Duke Energy Carolinas lists it for a sliver and a
        # county list can't size a sliver: a known weakness (DATA.md), bounded here.
        ("37183", "CPLE", 2024, 0.55),
        ("37021", "CPLW", 2024, 0.40),  # Buncombe (Asheville), shared with DUK
        ("53025", "GCPD", 2024, 0.65),  # Grant County, WA (Quincy); Avista lists a sliver
    ],
)
def test_known_counties(ba_map, fips, ba, year, low):
    assert _w(ba_map, fips, year, ba) >= low


@needs_map
def test_known_exclusions(ba_map):
    assert _w(ba_map, "51001", 2024, "DOM") == 0  # Accomack: DPL zone, not Dominion
    assert _w(ba_map, "51161", 2024, "DOM") < 0.2  # Roanoke County: AEP zone
    assert _w(ba_map, "48141", 2024, "ERCO") < 0.1  # El Paso: EPE
    assert _w(ba_map, "12033", 2023, "SOCO") < 0.1  # Gulf Power left SOCO
    assert _w(ba_map, "04013", 2024, "AZPS") + _w(ba_map, "04013", 2024, "SRP") > 0.95  # Maricopa


@needs_map
def test_people_per_customer_plausible():
    s = pl.read_csv(bwm.SUMMARY_PATH)
    shortlist = pl.read_csv(bwm.REFERENCE_DIR / "ba_shortlist.csv").filter(
        pl.col("include") != "no"
    )
    s = s.filter(pl.col("ba").is_in(shortlist["series"].implode()))
    # Household size is ~2.5; a mapped population far off that means lost or extra counties.
    assert s["people_per_customer"].min() > 1.9, s.sort("people_per_customer").head(3)
    assert s["people_per_customer"].max() < 3.1, s.sort("people_per_customer").tail(3)


@needs_map
def test_ercot_population_matches_its_footprint():
    s = pl.read_csv(bwm.SUMMARY_PATH).filter(pl.col("ba") == "ERCO", pl.col("year") >= 2019)
    # ERCOT serves ~90% of Texas (~26-27M people in the early 2020s).
    assert s["population"].min() > 24e6 and s["population"].max() < 29e6
