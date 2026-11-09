from datetime import date

from glg.ingest import weather


def test_months_span_year_boundary():
    assert weather.months((2015, 11), (2016, 2)) == [(2015, 11), (2015, 12), (2016, 1), (2016, 2)]


def test_last_complete_month_allows_for_era5_lag():
    assert weather.last_complete_month(date(2026, 11, 6)) == (2026, 9)
    assert weather.last_complete_month(date(2026, 11, 7)) == (2026, 10)
    assert weather.last_complete_month(date(2026, 1, 3)) == (2025, 11)
    assert weather.last_complete_month(date(2026, 1, 20)) == (2025, 12)


def test_request_only_asks_for_real_days():
    assert len(weather.request(2016, 2)["day"]) == 29
    assert len(weather.request(2019, 2)["day"]) == 28
    r = weather.request(2019, 7)
    assert r["day"][-1] == "31" and len(r["time"]) == 24
    assert set(r["variable"]) == {"2m_temperature", "2m_dewpoint_temperature"}


def test_progress_bar():
    assert weather._bar(0, 10, width=10).startswith("[░░░░░░░░░░] 0/10")
    assert weather._bar(5, 10, width=10).startswith("[█████░░░░░] 5/10")


def test_chunks_group_missing_months_by_year():
    todo = [(2015, m) for m in range(2, 13)] + [(2016, 1), (2016, 2)]
    assert weather.chunks(todo, 12) == [(2015, list(range(2, 13))), (2016, [1, 2])]
    assert weather.chunks(todo, 6) == [
        (2015, [2, 3, 4, 5, 6]),
        (2015, [7, 8, 9, 10, 11, 12]),
        (2016, [1, 2]),
    ]
    assert weather.chunks([(2015, 3)], 1) == [(2015, [3])]


def test_multi_month_request_lists_all_days():
    r = weather.request(2016, [1, 2, 3])
    assert r["month"] == ["01", "02", "03"] and len(r["day"]) == 31
    assert weather.expected_hours(2016, [1, 2, 3]) == (31 + 29 + 31) * 24


def _fake_era5(year, mlist):
    import numpy as np
    import pandas as pd
    import xarray as xr

    t = pd.date_range(
        f"{year}-{mlist[0]:02d}-01", periods=weather.expected_hours(year, mlist), freq="h"
    )
    shape = (len(t), 3, 4)
    rng = np.random.default_rng(0)
    data = {
        v: (("valid_time", "latitude", "longitude"), rng.normal(280, 5, shape).astype("float32"))
        for v in ("t2m", "d2m")
    }
    return xr.Dataset(
        data,
        coords={
            "valid_time": t,
            "latitude": [50, 49.75, 49.5],
            "longitude": [-125, -124.75, -124.5, -124.25],
        },
    )


def test_split_months_writes_valid_monthly_files(tmp_path):
    part = tmp_path / "chunk.nc.part"
    _fake_era5(2016, [1, 2, 3]).to_netcdf(part)
    weather.validate(part, 2016, [1, 2, 3])
    weather.split_months(part, 2016, [1, 2, 3], tmp_path)
    assert not part.exists()
    for m in (1, 2, 3):
        weather.validate(weather.month_path(2016, m, tmp_path), 2016, m)


def test_validate_rejects_short_file(tmp_path):
    import pytest

    p = tmp_path / "x.nc"
    _fake_era5(2016, [1]).isel(valid_time=slice(0, 700)).to_netcdf(p)
    with pytest.raises(ValueError, match="hours"):
        weather.validate(p, 2016, 1)


def test_too_large_detection():
    assert weather._too_large(Exception("400 Client Error: cost limits exceeded"))
    assert not weather._too_large(Exception("Read timed out"))
