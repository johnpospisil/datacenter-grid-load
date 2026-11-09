from datetime import UTC, datetime

import pytest

from glg.ingest import pjm_load

HEADER = (
    "datetime_beginning_utc,datetime_beginning_ept,nerc_region,mkt_region,"
    "zone,load_area,mw,is_verified\n"
)


def _write(path, rows):
    path.write_text(HEADER + "\n".join(rows) + "\n")


def test_hour_beginning_becomes_hour_ending_utc(tmp_path):
    _write(
        tmp_path / "hrl_load_metered_2016.csv",
        [
            "1/1/2016 5:00:00 AM,1/1/2016 12:00:00 AM,SERC,SOUTH,DOM,DOM,8549.125,True",
            "1/1/2016 11:00:00 PM,1/1/2016 6:00:00 PM,SERC,SOUTH,DOM,DOM,9000,False",
        ],
    )
    df = pjm_load.build(tmp_path)
    assert df["period_end_utc"].to_list() == [
        datetime(2016, 1, 1, 6, tzinfo=UTC),
        datetime(2016, 1, 2, 0, tzinfo=UTC),
    ]
    assert df["mw"].to_list() == [8549.125, 9000.0]
    assert df["is_verified"].to_list() == [True, False]


def test_overlapping_exports_deduplicate(tmp_path):
    row = "1/1/2016 5:00:00 AM,1/1/2016 12:00:00 AM,SERC,SOUTH,DOM,DOM,{},True"
    _write(tmp_path / "hrl_load_metered_2016.csv", [row.format(1.0)])
    _write(tmp_path / "hrl_load_metered_2016b.csv", [row.format(2.0)])
    df = pjm_load.build(tmp_path)
    assert df.height == 1 and df["mw"].item() == 2.0


def test_rejects_other_load_areas(tmp_path):
    _write(
        tmp_path / "hrl_load_metered_2016.csv", ["1/1/2016 5:00:00 AM,x,RFC,WEST,AEP,AEPAPT,1,True"]
    )
    with pytest.raises(ValueError, match="AEPAPT"):
        pjm_load.build(tmp_path)


def test_missing_hours(tmp_path):
    _write(
        tmp_path / "hrl_load_metered_2016.csv",
        [
            "1/1/2016 5:00:00 AM,x,SERC,SOUTH,DOM,DOM,1,True",
            "1/1/2016 8:00:00 AM,x,SERC,SOUTH,DOM,DOM,1,True",
        ],
    )
    assert pjm_load.missing_hours(pjm_load.build(tmp_path)) == 2
