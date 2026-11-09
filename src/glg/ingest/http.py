"""Shared, idempotent file download: stream to a .part file, check it, then rename."""

import logging
from pathlib import Path

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)

PUDL_BASE = "https://s3.us-west-2.amazonaws.com/pudl.catalyst.coop/stable"
PARQUET_MAGIC = b"PAR1"
ZIP_MAGIC = b"PK\x03\x04"


@retry(stop=stop_after_attempt(4), wait=wait_exponential(min=2, max=30), reraise=True)
def _fetch(url: str, dest: Path, magic: bytes | None) -> None:
    tmp = dest.with_name(dest.name + ".part")
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with tmp.open("wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    if magic is not None:
        with tmp.open("rb") as f:
            head = f.read(len(magic))
        if head != magic:
            tmp.unlink()
            raise ValueError(f"{url} did not return the expected file type")
    tmp.rename(dest)


def download(url: str, dest: Path, magic: bytes | None = None, refresh: bool = False) -> Path:
    """Download url to dest unless it is already there (or refresh=True)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not refresh:
        log.info("Cached %s", dest.name)
        return dest
    log.info("Downloading %s", url)
    _fetch(url, dest, magic)
    log.info("%s: %.1f MB", dest.name, dest.stat().st_size / 1e6)
    return dest
