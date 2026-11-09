"""Interconnection queues (Prompt 12): type groups, region mapping, summary arithmetic."""

from datetime import date

import polars as pl
import pytest

from glg.ingest import queues as qi
from glg.models import queue_summary as qs


@pytest.mark.parametrize(
    "raw, group",
    [
        ("Solar", "Solar"),
        ("Solar+Battery", "Hybrid"),
        ("Offshore Wind", "Wind"),
        ("Other Storage", "Battery"),
        ("Geothermal", "Other"),
    ],
)
def test_type_groups(raw, group):
    assert pl.DataFrame({"t": [raw]}).select(qi.type_group(pl.col("t")).alias("x"))["x"][0] == group


@pytest.mark.parametrize(
    "entity, utility, state, series",
    [
        ("Duke", "Duke Energy Florida", "FL", "FPC"),
        ("Duke", "Duke Energy Progress", "SC", "CPLE"),
        ("PacifiCorp", "PacifiCorp", "UT", "PACE"),
        ("PacifiCorp", "PacifiCorp", "OR", None),
        ("SRP_ANPP", "Salt River Project", "AZ", "SRP"),
        ("Dominion", "Dominion Energy South Carolina", "SC", None),  # not the DOM zone
        ("ERCOT", None, "TX", "ERCO"),
    ],
)
def test_series_mapping(entity, utility, state, series):
    df = pl.DataFrame(
        {"entity": [entity], "utility": [utility], "state": [state]},
        schema={"entity": pl.Utf8, "utility": pl.Utf8, "state": pl.Utf8},
    )
    assert df.select(qi.series_expr().alias("x"))["x"][0] == series


def synthetic_queue() -> pl.DataFrame:
    rows = [
        # region, series, status, q_year, type, mw, q_date, on_date
        ("PJM", "PJM", "operational", 2015, "Solar", 100, date(2015, 1, 1), date(2019, 1, 1)),
        ("PJM", "PJM", "withdrawn", 2016, "Solar", 300, date(2016, 1, 1), None),
        ("PJM", "PJM", "active", 2018, "Gas", 600, date(2018, 1, 1), None),
        ("PJM", "PJM", "active", 2024, "Battery", 50, date(2024, 1, 1), None),
        ("PJM", "DOM", "active", 2024, "Battery", 50, date(2024, 1, 1), None),
    ]
    cols = ["region", "series", "q_status", "q_year", "type_group", "mw_total", "q_date", "on_date"]
    df = pl.DataFrame(rows, schema=cols, orient="row")
    return df.with_columns(pl.lit(None, pl.Date).alias("ia_date"))


def test_active_backlog_and_completion():
    q = synthetic_queue()
    a = qs.active(q).filter(pl.col("unit") == "PJM", pl.col("level") == "region").row(0, named=True)
    assert a["total_gw"] == pytest.approx(0.65)  # the DOM copy is not double-counted
    assert a["n_active"] == 2
    c = qs.completion(q, []).filter(pl.col("unit") == "PJM", pl.col("level") == "region")
    c = c.row(0, named=True)
    assert c["n"] == 3  # entered 2000-2020
    assert c["pct_n_operational"] == pytest.approx(100 / 3)
    assert c["pct_mw_operational"] == pytest.approx(10.0)
    t = qs.timing(q).filter(pl.col("unit") == "PJM", pl.col("level") == "region").row(0, named=True)
    assert t["median_years_ir_to_cod"] == pytest.approx(4.0, abs=0.01)


needs_outputs = pytest.mark.skipif(not qs.ACTIVE_PATH.exists(), reason="run queue_summary")


@needs_outputs
def test_queue_outputs():
    a = pl.read_csv(qs.ACTIVE_PATH)
    regions = a.filter(pl.col("level") == "region")
    assert set(regions["unit"]) == {
        "CAISO", "ERCOT", "ISO-NE", "MISO", "NYISO", "PJM", "SPP", "Southeast", "West",
    }  # fmt: skip
    assert 1500 < regions["total_gw"].sum() < 2300  # LBNL: ~1.9 TW active at end-2025
    assert "DOM" in a.filter(pl.col("level") == "series")["unit"].to_list()


def test_pipeline_energy_by_component():
    q = pl.DataFrame(
        {
            "series": ["X"],
            "q_status": ["active"],
            "type_1": ["Solar"],
            "mw_1": [1000.0],
            "type_2": ["Battery"],
            "mw_2": [500.0],
            "type_3": [None],
            "mw_3": [0.0],
        },
        schema_overrides={"type_3": pl.Utf8},
    )
    comp = pl.DataFrame({"level": ["series"], "unit": ["X"], "pct_mw_operational": [20.0]})
    r = qs.pipeline(q, comp).row(0, named=True)
    assert r["active_gw"] == pytest.approx(1.5)
    assert r["queue_twh"] == pytest.approx(1000 * 0.235 * 8766 / 1e6)  # storage adds no energy
    assert r["expected_twh"] == pytest.approx(0.2 * r["queue_twh"])
