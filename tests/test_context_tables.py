"""Hand-curated context tables (Prompt 14): every row cited, typed and linked."""

import polars as pl
import pytest

from glg.config import REFERENCE_DIR
from glg.models import growth as g

SERIES = set(pl.read_csv(g.RANKING_PATH)["ba"]) if g.RANKING_PATH.exists() else None


@pytest.fixture(scope="module")
def util():
    return pl.read_csv(REFERENCE_DIR / "utility_reported_load.csv")


@pytest.fixture(scope="module")
def ann():
    return pl.read_csv(REFERENCE_DIR / "datacenter_announcements.csv")


@pytest.mark.parametrize("name", ["util", "ann"])
def test_every_row_is_cited(name, request):
    df = request.getfixturevalue(name)
    assert df["id"].is_unique().all()
    assert df["source_url"].str.starts_with("https://").all()
    assert set(df["confidence"]) <= {"high", "medium", "low"}
    assert df["source_type"].null_count() == 0


def test_utility_metric_types(util):
    assert set(util["metric_type"]) <= {
        "measured", "contracted", "pipeline", "forecast", "requests", "approved", "policy",
    }  # fmt: skip
    assert set(util["comparable"]) <= {"yes", "no", "partly"}
    # only measured load can be compared with our estimates
    assert util.filter(pl.col("comparable") == "yes")["metric_type"].to_list() == ["measured"] * 3


@pytest.mark.skipif(SERIES is None, reason="run glg.models.growth")
def test_series_codes_are_ours(util, ann):
    assert set(util["series"]) <= SERIES
    assert set(ann["series"].drop_nulls()) <= SERIES


def test_hero_annotations_point_at_cited_rows(util):
    h = pl.read_csv(REFERENCE_DIR / "hero_annotations.csv")
    assert set(h["ref"]) <= set(util["id"])
    assert h["date"].str.to_date().is_not_null().all()
    assert (h["label"].str.len_chars() <= 70).all()
