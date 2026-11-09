from datetime import UTC, date, datetime

import pytest

from glg.ingest import eia930


def test_six_month_periods_span_and_order():
    periods = eia930.six_month_periods((2015, 2), date(2016, 7, 1))
    assert periods == [(2015, 2), (2016, 1), (2016, 2)]


def test_six_month_periods_june_is_first_half():
    assert eia930.six_month_periods((2026, 1), date(2026, 6, 30)) == [(2026, 1)]


def test_file_name():
    assert eia930.file_name("BALANCE", 2016, 1) == "EIA930_BALANCE_2016_Jan_Jun"
    assert eia930.file_name("SUBREGION", 2018, 2) == "EIA930_SUBREGION_2018_Jul_Dec"


def test_needs_download_refreshes_only_recent_periods(tmp_path):
    path = tmp_path / "f.csv"
    today = date(2026, 10, 22)
    assert eia930.needs_download(path, 2016, 1, today, refresh=False)  # missing
    path.write_text("x")
    assert not eia930.needs_download(path, 2016, 1, today, refresh=False)  # old, cached
    assert eia930.needs_download(path, 2026, 1, today, refresh=False)  # ended < 120 days ago
    assert eia930.needs_download(path, 2016, 1, today, refresh=True)


@pytest.fixture
def balance_csv(tmp_path):
    header = ",".join(f'"{c}"' for c in eia930.BALANCE_COLUMNS)
    rows = [
        # ba, region, utc, local, DF, D, D imputed, D adjusted, NG, NG adj, TI, TI adj
        'AAA,SE,03/13/2016 7:00:00 AM,03/13/2016 1:00:00 AM,"1,200",1100,,1100,900,900,-200,-200',
        "AAA,SE,03/13/2016 8:00:00 AM,03/13/2016 3:00:00 AM,1200,,1105,1105,900,900,-205,-205",
    ]
    path = tmp_path / "EIA930_BALANCE_2016_Jan_Jun.csv"
    path.write_text(header + "\n" + "\n".join(rows) + "\n")
    return path


def test_read_bulk_file_types_and_values(balance_csv):
    df = eia930.read_bulk_file(balance_csv, eia930.BALANCE_COLUMNS)
    assert df.height == 2
    assert df["demand_forecast"].to_list() == [1200.0, 1200.0]  # thousands separator stripped
    assert df["demand"].to_list() == [1100.0, None]
    assert df["demand_imputed"].to_list() == [None, 1105.0]
    assert df["period_end_utc"][0] == datetime(2016, 3, 13, 7, tzinfo=UTC)
    # The local clock skips 2 AM on the DST start date; UTC stays continuous.
    assert (df["period_end_utc"][1] - df["period_end_utc"][0]).total_seconds() == 3600


def test_build_parquet_drops_duplicate_keys(balance_csv, tmp_path):
    copy = tmp_path / "EIA930_BALANCE_2016_Jul_Dec.csv"
    copy.write_text(balance_csv.read_text())
    out = tmp_path / "out.parquet"
    df = eia930.build_parquet(
        [balance_csv, copy], eia930.BALANCE_COLUMNS, ["ba", "period_end_utc"], out
    )
    assert df.height == 2
    assert set(df["source_file"]) == {copy.name}
    assert out.exists()
