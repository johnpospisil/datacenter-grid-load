"""ERA5 hourly 2 m temperature and dewpoint over CONUS, one NetCDF per month.

Download (Prompt 5, part 1). Aggregation to county x hour and BA x hour comes later.

ECMWF asks for requests to be sent one at a time (Data Stores protection measures,
Oct 2026) and may disable accounts that repeat invalid requests, so this downloader:
- sends one request at a time, pausing between them;
- only builds requests in the format the form accepts (valid days only);
- stops after MAX_CONSECUTIVE_FAILURES failures in a row instead of hammering the API;
- asks for a whole year per request (the CDS queue wait is per request, not per MB: the
  first month queued 2.5 h and downloaded in 34 s), halving a request rejected as too large;
- writes to a .part file, validates it, splits it into monthly files, so reruns skip
  finished months.

Usage:
    python -m glg.ingest.weather download [--start 2015-01] [--end 2026-10] [--chunk 12|6|1]
    python -m glg.ingest.weather status            # progress bar, once
    python -m glg.ingest.weather status --watch    # live progress bar (Ctrl-C to quit)
"""

import argparse
import calendar
import json
import logging
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path

from glg.config import RAW_WEATHER_DIR

log = logging.getLogger(__name__)

DATASET = "reanalysis-era5-single-levels"
VARIABLES = ["2m_temperature", "2m_dewpoint_temperature"]
AREA = [50, -125, 24, -66]  # N, W, S, E: contiguous U.S. with a margin
START = (2015, 1)
PAUSE_SECONDS = 10
MAX_CONSECUTIVE_FAILURES = 3
STATE_FILE = "download_state.json"
LOG_FILE = "download.log"


def months(start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int]]:
    out, (y, m) = [], start
    while (y, m) <= end:
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def last_complete_month(today: date) -> tuple[int, int]:
    """ERA5 (preliminary ERA5T) runs ~5 days behind; only request finished months."""
    y, m = today.year, today.month - 1
    if m == 0:
        y, m = y - 1, 12
    if today.day <= 6:  # previous month's last days may not be published yet
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return y, m


def month_path(year: int, month: int, root: Path = RAW_WEATHER_DIR) -> Path:
    return root / f"era5_t2m_d2m_{year}-{month:02d}.nc"


def request(year: int, mlist: list[int] | int) -> dict:
    """One request for some months of one year. A single month asks only for its real days;
    several months need days 01-31 (the CDS form skips dates that don't exist)."""
    mlist = [mlist] if isinstance(mlist, int) else list(mlist)
    ndays = calendar.monthrange(year, mlist[0])[1] if len(mlist) == 1 else 31
    return {
        "product_type": ["reanalysis"],
        "variable": VARIABLES,
        "year": [str(year)],
        "month": [f"{m:02d}" for m in mlist],
        "day": [f"{d:02d}" for d in range(1, ndays + 1)],
        "time": [f"{h:02d}:00" for h in range(24)],
        "area": AREA,
        "data_format": "netcdf",
        "download_format": "unarchived",
    }


def expected_hours(year: int, mlist: list[int]) -> int:
    return sum(calendar.monthrange(year, m)[1] * 24 for m in mlist)


def chunks(todo: list[tuple[int, int]], size: int) -> list[tuple[int, list[int]]]:
    """Group missing months into requests of at most `size` months, never crossing a year
    or a block boundary (size 12 = calendar years, 6 = half-years, 1 = single months)."""
    out: dict[tuple[int, int], list[int]] = {}
    for y, m in todo:
        out.setdefault((y, (m - 1) // size), []).append(m)
    return [(y, ms) for (y, _), ms in out.items()]


def validate(path: Path, year: int, mlist: list[int] | int) -> None:
    """Raise if the file isn't every hour of the requested months, with both variables."""
    import xarray as xr

    mlist = [mlist] if isinstance(mlist, int) else list(mlist)
    with xr.open_dataset(path) as ds:
        missing = {"t2m", "d2m"} - set(ds.data_vars)
        if missing:
            raise ValueError(f"{path.name}: missing variables {missing}")
        n = ds.sizes.get("valid_time", 0)
        if n != expected_hours(year, mlist):
            raise ValueError(f"{path.name}: {n} hours, expected {expected_hours(year, mlist)}")
        got = sorted(set(ds["valid_time"].dt.month.values.tolist()))
        if got != sorted(mlist) or set(ds["valid_time"].dt.year.values.tolist()) != {year}:
            raise ValueError(f"{path.name}: wrong months {got}, expected {sorted(mlist)}")


def split_months(part: Path, year: int, mlist: list[int], root: Path) -> None:
    """Write one compressed NetCDF per month from a multi-month download, then delete it."""
    import xarray as xr

    enc = {v: {"zlib": True, "complevel": 1, "shuffle": True} for v in ("t2m", "d2m")}
    with xr.open_dataset(part) as ds:
        for m in mlist:
            dest = month_path(year, m, root)
            tmp = dest.with_name(dest.name + ".part")
            ds.sel(valid_time=ds["valid_time"].dt.month == m).load().to_netcdf(tmp, encoding=enc)
            validate(tmp, year, m)
            tmp.rename(dest)
    part.unlink()


def _write_state(root: Path, **fields) -> None:
    path = root / STATE_FILE
    state = json.loads(path.read_text()) if path.exists() else {}
    state.update(fields, updated_utc=datetime.now(UTC).isoformat(timespec="seconds"))
    path.write_text(json.dumps(state, indent=2))


def _too_large(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(k in msg for k in ("cost limit", "too large", "limits exceeded"))


def download(
    start: tuple[int, int],
    end: tuple[int, int],
    limit: int | None = None,
    chunk: int = 12,
) -> int:
    import cdsapi

    root = RAW_WEATHER_DIR
    root.mkdir(parents=True, exist_ok=True)
    todo = [ym for ym in months(start, end) if not month_path(*ym, root).exists()]
    total = len(months(start, end))
    queue = chunks(todo, chunk)
    if limit:
        queue = queue[:limit]
    log.info(
        "%d of %d months missing; %d request(s) of up to %d months into %s",
        len(todo),
        total,
        len(queue),
        chunk,
        root,
    )
    _write_state(
        root,
        start=f"{start[0]}-{start[1]:02d}",
        end=f"{end[0]}-{end[1]:02d}",
        total=total,
        running=True,
        current=None,
        last_error=None,
        run_started_utc=datetime.now(UTC).isoformat(timespec="seconds"),
        run_done_at_start=total - len(todo),
    )

    client = cdsapi.Client(quiet=True, progress=False, timeout=120, retry_max=5)
    failures = 0
    while queue:
        y, ms = queue.pop(0)
        label = f"{y}-{ms[0]:02d}" + (f"..{ms[-1]:02d}" if len(ms) > 1 else "")
        part = root / f"era5_t2m_d2m_{label}.request.nc.part"
        _write_state(
            root,
            current=label,
            current_started_utc=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        t0 = time.monotonic()
        try:
            client.retrieve(DATASET, request(y, ms), str(part))
            validate(part, y, ms)
            if len(ms) == 1:
                part.rename(month_path(y, ms[0], root))
            else:
                split_months(part, y, ms, root)
        except Exception as exc:  # noqa: BLE001 - log, back off, maybe stop
            part.unlink(missing_ok=True)
            if _too_large(exc) and len(ms) > 1:
                # Rejected up front for size: split it in two and carry on (no backoff needed).
                half = (len(ms) + 1) // 2
                log.warning("%s rejected as too large; splitting into two requests", label)
                queue[:0] = [(y, ms[:half]), (y, ms[half:])]
                failures += 1
                if failures >= MAX_CONSECUTIVE_FAILURES:
                    log.error("Stopping after %d consecutive failures.", failures)
                    _write_state(root, running=False, current=None, last_error=str(exc)[:500])
                    return 1
                continue
            failures += 1
            log.error("%s failed (%d in a row): %s", label, failures, exc)
            _write_state(root, last_error=f"{label}: {exc}"[:500])
            if failures >= MAX_CONSECUTIVE_FAILURES:
                log.error("Stopping after %d consecutive failures.", failures)
                _write_state(root, running=False, current=None)
                return 1
            queue.insert(0, (y, ms))
            time.sleep(PAUSE_SECONDS * 6 * failures)
            continue
        failures = 0
        _write_state(root, last_error=None)  # a retry succeeded: don't keep showing a stale error
        log.info("%s done: %d month(s) in %.0f s", label, len(ms), time.monotonic() - t0)
        if queue:
            time.sleep(PAUSE_SECONDS)
    _write_state(root, running=False, current=None)
    log.info("Finished.")
    return 0


# ---------------------------------------------------------------- progress display


def _bar(done: int, total: int, width: int = 40) -> str:
    frac = done / total if total else 0
    filled = int(round(frac * width))
    return f"[{'█' * filled}{'░' * (width - filled)}] {done}/{total} months ({frac:5.1%})"


def status_text(root: Path = RAW_WEATHER_DIR) -> str:
    state_path = root / STATE_FILE
    if not state_path.exists():
        return f"No ERA5 download started yet ({root})."
    st = json.loads(state_path.read_text())
    s, e = (tuple(int(x) for x in st[k].split("-")) for k in ("start", "end"))
    all_months = months(s, e)
    done_files = [month_path(*ym, root) for ym in all_months if month_path(*ym, root).exists()]
    done = len(done_files)
    size_gb = sum(p.stat().st_size for p in done_files) / 1e9
    lines = [f"ERA5 download  {_bar(done, len(all_months))}"]
    lines.append(f"  on disk: {size_gb:.2f} GB in {root}")
    if done_files:
        avg = size_gb / done
        lines.append(
            f"  projected total: ~{avg * len(all_months):.1f} GB ({avg * 1000:.0f} MB/month)"
        )
        if st.get("run_started_utc") and done < len(all_months):
            run_done = done - st.get("run_done_at_start", 0)
            elapsed = datetime.now(UTC) - datetime.fromisoformat(st["run_started_utc"])
            secs = elapsed.total_seconds()
            if run_done > 0:
                eta_h = secs / run_done * (len(all_months) - done) / 3600
                lines.append(
                    f"  pace this run: {run_done} months in {secs / 3600:.1f} h, ETA ~{eta_h:.0f} h"
                )
    state = "running" if st.get("running") else "stopped"
    if st.get("current"):
        started = datetime.fromisoformat(st["current_started_utc"])
        mins = (datetime.now(UTC) - started).total_seconds() / 60
        state += f", working on {st['current']} ({mins:.0f} min, incl. Copernicus queue)"
    lines.append(f"  status: {state}")
    if st.get("last_error"):
        lines.append(f"  last error: {st['last_error']}")
    lines.append(f"  log: {root / LOG_FILE}")
    return "\n".join(lines)


def watch(interval: int = 15) -> None:
    try:
        while True:
            sys.stdout.write(
                "\033[2J\033[H" + status_text() + "\n\n(refreshes every "
                f"{interval} s; Ctrl-C to quit; the download keeps running)\n"
            )
            sys.stdout.flush()
            time.sleep(interval)
    except KeyboardInterrupt:
        print()


def _ym(s: str) -> tuple[int, int]:
    y, m = s.split("-")
    return int(y), int(m)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m glg.ingest.weather")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download")
    d.add_argument("--start", type=_ym, default=START)
    d.add_argument("--end", type=_ym, default=None)
    d.add_argument("--limit", type=int, default=None, help="send at most N requests")
    d.add_argument("--chunk", type=int, default=12, choices=(12, 6, 1), help="months per request")
    s = sub.add_parser("status")
    s.add_argument("--watch", action="store_true")
    args = p.parse_args(argv)

    if args.cmd == "status":
        watch() if args.watch else print(status_text())
        return 0

    RAW_WEATHER_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(RAW_WEATHER_DIR / LOG_FILE), logging.StreamHandler()],
    )
    end = args.end or last_complete_month(date.today())
    return download(args.start, end, args.limit, args.chunk)


if __name__ == "__main__":
    sys.exit(main())
