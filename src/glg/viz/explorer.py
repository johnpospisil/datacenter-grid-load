# ruff: noqa: E501
"""Export compact JSON for the static region explorer (Prompt 18, decision D6).

Writes `app/data/index.json` (every region's headline numbers, the comparison-median index
and the data cut-off) and `app/data/series/<code>.json` (one file per region: monthly
actual vs weather-normalised demand, the weather-adjusted index, night/afternoon indices,
annual load-shape metrics, generation added and retired, the interconnection queue, and
cited utility disclosures). The page in `app/` reads these files; there is no server.

Only published outputs are read. Nothing comes from `data/outputs/private/` (PJM Data
Miner, decision D3): DOM is EIA-930 throughout.

Run: python -m glg.viz.explorer
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from statistics import median

import polars as pl

from glg.config import OUTPUTS_DIR, REFERENCE_DIR
from glg.report import COMPARISONS, DC, NAMES, cutoff

log = logging.getLogger(__name__)

APP_DIR = Path(__file__).resolve().parents[3] / "app"
DATA_DIR = APP_DIR / "data"

# Which series the explorer offers, in the menu's order. D10: DOPD, PSCO and LGEE are
# appendix-only and AZPS_full is a sensitivity series, so they are not offered.
OTHERS = ["PJM", "SWPP", "TVA", "SOCO", "DUK", "FPL+SOCO"]
PARTS = ["AZPS", "SRP"]
SERIES = [*DC, *OTHERS, *COMPARISONS, *PARTS]

NAMES = {**NAMES, "SWPP": "Southwest Power Pool"}

GROUP = {
    **{b: "data-center" for b in DC},
    **{b: "comparison" for b in COMPARISONS},
    **{b: "other" for b in OTHERS},
    **{b: "part" for b in PARTS},
}

SHORT = {
    "DOM": "Dominion (VA)",
    "ERCO": "ERCOT (TX)",
    "AZPS+SRP": "Phoenix",
    "BPAT": "Bonneville",
    "GCPD": "Grant County",
    "NEVP": "Nevada",
    "PJM": "PJM (all)",
    "SWPP": "SPP",
    "TVA": "TVA",
    "SOCO": "Southern",
    "DUK": "Duke Carolinas",
    "FPL+SOCO": "FPL + Southern",
    "MISO": "MISO",
    "NYIS": "New York",
    "ISNE": "New England",
    "FPC": "Duke Florida",
    "CPLE": "Duke Progress E.",
    "CISO": "California",
    "PACE": "PacifiCorp East",
    "FPL": "FPL",
    "AZPS": "APS",
    "SRP": "SRP",
}

PAIR = {
    "DOM": "CPLE",
    "ERCO": "FPC",
    "AZPS+SRP": "CISO",
    "NEVP": "CISO",
    "BPAT": "PACE",
    "GCPD": "PACE",
    "PJM": "MISO",
}

# Queue and supply rows for combined series
COMBINED = {"AZPS+SRP": ["AZPS", "SRP"], "FPL+SOCO": ["FPL", "SOCO"]}

NOTES = {
    "DOM": [
        "PJM's Dominion transmission zone (most of Virginia and north-east North Carolina), which is larger than Dominion Energy's service territory: co-ops inside it serve data centers of their own.",
        "EIA-930 data for the zone starts in September 2018, so its weather baseline is about 16 months, not four years. A check against PJM's own metered load gives practically the same growth since 2022; the short baseline mainly shifts the level relative to 2019.",
        "Dominion's own metered data-center demand grew 1,373 MW from 2022 to 2025, about 60% of the zone's weather-adjusted growth over the same years.",
    ],
    "ERCO": [
        "ERCOT's own data put the observed peak of all approved large loads (data centers, crypto mining and industry) at 3.9 GW in March 2026, about 41% of this growth. It is a guide, not a hard cap: smaller loads outside ERCOT's large-load process, and load served behind the meter, aren't counted.",
        "Growth is 11–20% across model specifications; adding a state-employment control halves it.",
        "Crypto mining and oil-field electrification also run around the clock, so a flat shape here is not specific to data centers.",
    ],
    "AZPS+SRP": [
        "Ends August 2025: from September 2025, EIA's Arizona Public Service figures run 8–22% above APS's own federal filing (unresolved).",
        "APS and SRP are combined because the split of Phoenix load between them swings in 2025–26.",
        "Semiconductor fabs (TSMC, Intel) are also flat 24/7 loads; the load shape can't tell them from data centers. Rooftop solar lowers afternoon demand, which flatters the night ÷ afternoon ratio.",
    ],
    "AZPS": [
        "Ends August 2025 (EIA-930 vs APS's FERC filing dispute from September 2025). The Phoenix headline is the combined APS + SRP series."
    ],
    "SRP": [
        "Part of Phoenix. Load moves between APS and SRP in 2025–26, so read the combined APS + SRP series."
    ],
    "BPAT": [
        "No utility disclosure of data-center load was found for Bonneville's customers, so this growth is a lead, not a finding."
    ],
    "GCPD": [
        "Grant County PUD reports 280 average MW of data-center load in 2025 (37% of its system), but most of it predates 2022: the region was already growing about 3.4%/yr in 2016–2019.",
        "Growth is 4–11% across specifications.",
    ],
    "NEVP": ["Growth is 5–9% across specifications; no utility disclosure is compared."],
    "SOCO": [
        "Measured from 2023, because Gulf Power moved from Southern Company to FPL in mid-2022.",
        "Southern reports data-center sales up 55% in a year, but this series grew mostly in afternoon and weather-sensitive hours (unresolved).",
    ],
    "FPL": [
        "Measured from 2023, because Gulf Power (about 1.3–1.5 GW) joined FPL's reported demand in mid-2022."
    ],
    "FPL+SOCO": [
        "FPL and Southern combined, so Gulf Power's 2022 move between them cancels out; measured from 2022."
    ],
    "CISO": [
        "California's 2016–2019 trend was falling (−2.0%/yr), so 'beyond trend' is an upper bound here. Rooftop solar lowers its afternoon demand."
    ],
    "ISNE": [
        "New England's 2016–2019 trend was falling (−1.7%/yr), so 'beyond trend' is an upper bound here."
    ],
    "PJM": [
        "All of PJM (13 states and DC); about half of its weather-adjusted growth is in the Dominion zone."
    ],
}

R1 = 1  # decimals for MW-scale numbers in JSON


def _r(x, nd=R1):
    if x is None or x != x:  # None or NaN
        return None
    return round(float(x), nd)


def _ym(d) -> str:
    return d.strftime("%Y-%m")


def _rows(df: pl.DataFrame, key: str) -> dict[str, dict]:
    return {r[key]: r for r in df.iter_rows(named=True)}


def _sum_rows(rows: list[dict]) -> dict:
    out = {}
    for k in rows[0]:
        vals = [r[k] for r in rows]
        out[k] = (
            sum(v for v in vals if v is not None)
            if all(isinstance(v, (int, float)) or v is None for v in vals)
            else vals[0]
        )
    return out


def load() -> dict:
    o = OUTPUTS_DIR
    return {
        "rank": _rows(pl.read_csv(o / "growth_ranking.csv"), "ba"),
        "dec": _rows(pl.read_csv(o / "growth_decomposition.csv"), "ba"),
        "grid": _rows(pl.read_csv(o / "robustness_grid.csv"), "ba"),
        "monthly": pl.read_parquet(o / "structural_growth_monthly.parquet"),
        "shape_m": pl.read_parquet(o / "load_shape_monthly.parquet"),
        "shape_y": pl.read_csv(o / "load_shape_metrics.csv"),
        "supply_y": pl.read_csv(o / "supply_vs_demand.csv"),
        "supply_s": _rows(pl.read_csv(o / "supply_vs_demand_since2022.csv"), "series"),
        "queue": pl.read_csv(o / "queue_active.csv").filter(pl.col("level") == "series"),
        "qcomp": pl.read_csv(o / "queue_completion.csv").filter(
            (pl.col("level") == "series") & pl.col("type_group").is_null()
        ),
        "qpipe": _rows(pl.read_csv(o / "queue_pipeline.csv"), "series"),
        "ann": pl.read_csv(REFERENCE_DIR / "hero_annotations.csv"),
        "util": pl.read_csv(REFERENCE_DIR / "utility_reported_load.csv"),
    }


def comparison_median(monthly: pl.DataFrame) -> list[dict]:
    m = (
        monthly.filter(pl.col("ba").is_in(COMPARISONS))
        .group_by("month")
        .agg(pl.col("index_12m").median().alias("i"), pl.col("index_12m").count().alias("n"))
        .filter(pl.col("n") >= len(COMPARISONS) - 1)
        .sort("month")
    )
    return [
        {"m": _ym(r["month"]), "i": _r(r["i"], 2)}
        for r in m.iter_rows(named=True)
        if r["i"] is not None
    ]


def summary(code: str, d: dict) -> dict:
    r, dec, g = d["rank"][code], d["dec"].get(code), d["grid"].get(code)
    s = {
        "growth_pct": _r(r["growth_pct"], 3),
        "growth_lo": _r(r["growth_lo"], 3),
        "growth_hi": _r(r["growth_hi"], 3),
        "growth_gbm_pct": _r(r["growth_gbm_pct"], 3),
        "growth_ddr_pct": _r(r["growth_ddr_pct"], 3),
        "mw_added": _r(r["mw_added"], 0),
        "twh_added": _r(r["twh_per_yr_added"], 3),
        "beyond_trend_pct": _r(r["beyond_trend_pct"], 3),
        "baseline_trend_pct_yr": _r(r["baseline_trend_pct_yr"], 3),
        "from_year": r["from_year"],
        "last12_from": r["last12_from"][:7],
        "last12_to": r["last12_to"][:7],
    }
    if g:
        specs = ["gam_2016_19", "gbm", "ddr", "gam_2017_19", "gam_start_yr", "gam_emp"]
        s["spec_min"] = _r(min(g[k] for k in specs), 3)
        s["spec_max"] = _r(max(g[k] for k in specs), 3)
    if dec:
        s.update(
            night_growth_pct=_r(dec["night_growth_pct"], 3),
            afternoon_growth_pct=_r(dec["afternoon_growth_pct"], 3),
            night_mw=_r(dec["d_night_mw"], 0),
            afternoon_mw=_r(dec["d_afternoon_mw"], 0),
            night_to_afternoon=_r(dec["night_to_afternoon"], 3)
            if dec["verdict"] != "no growth"
            else None,
            flat_mw=_r(dec["d_flat_mw"], 0),
            weather_mw=_r(dec["d_weather_mw"], 0),
            shape_mw=_r(dec["d_shape_mw"], 0),
            verdict=dec["verdict"],
        )
    return s


def monthly_series(code: str, d: dict) -> list[dict]:
    m = d["monthly"].filter(pl.col("ba") == code).sort("month")
    sm = d["shape_m"].filter(pl.col("ba") == code)
    bands = {
        b: dict(sm.filter(pl.col("band") == b).select("month", "index_12m").iter_rows())
        for b in ("night", "afternoon")
    }
    out = []
    for r in m.iter_rows(named=True):
        mo = r["month"]
        out.append(
            {
                "m": _ym(mo),
                "h": r["n_hours"],
                "a": _r(r["actual_mw"], 0),
                "p": _r(r["pred_mw"], 0),
                "i": _r(r["index_12m"], 2),
                "lo": _r(r["index_12m_lo"], 2),
                "hi": _r(r["index_12m_hi"], 2),
                "n": _r(bands["night"].get(mo), 2),
                "d": _r(bands["afternoon"].get(mo), 2),
            }
        )
    return out


def annual_shape(code: str, d: dict) -> list[dict]:
    y = d["shape_y"].filter(pl.col("ba") == code).sort("year")
    return [
        {
            "y": r["year"],
            "partial": r["partial_year"],
            "mean_mw": _r(r["mean_mw"], 0),
            "peak_mw": _r(r["peak_mw"], 0),
            "load_factor": _r(r["load_factor"], 3),
            "min_mean": _r(r["min_mean_ratio"], 3),
            "night_adj_ratio": _r(r["night_adj_ratio"], 3),
            "resid_pct": _r(r["resid_pct"], 2),
            "night_resid_pct": _r(r["night_resid_pct"], 2),
            "afternoon_resid_pct": _r(r["afternoon_resid_pct"], 2),
        }
        for r in y.iter_rows(named=True)
    ]


TECH = ["solar", "wind", "gas", "coal", "nuclear", "storage", "other"]


def supply(code: str, d: dict) -> dict:
    sy = d["supply_y"].filter(pl.col("series") == code).sort("year")
    years = []
    for r in sy.iter_rows(named=True):
        if r["added_mw"] is None:
            continue
        row = {"y": r["year"], "partial": r["partial_year"]}
        for t in TECH:
            row[f"add_{t}"] = _r(r.get(f"added_{t}") or 0, 0)
            # supply_vs_demand stores retirements as negative MW; export magnitudes
            row[f"ret_{t}"] = _r(abs(r.get(f"retired_{t}") or 0), 0)
        years.append(row)
    s = d["supply_s"].get(code)
    since = None
    if s:
        since = {
            "demand_twh": _r(s["demand_twh_added"], 2),
            "supply_twh": _r(s["new_supply_twh"], 2),
            "storage_mw": _r(s["new_storage_mw"], 0),
            **{
                f"twh_{t}": _r(s[f"twh_{t}"], 2)
                for t in ["solar", "wind", "gas", "nuclear", "coal", "other"]
            },
        }
    return {"years": years, "since": since}


def queue(code: str, d: dict) -> dict | None:
    parts = COMBINED.get(code, [code])
    q = d["queue"].filter(pl.col("unit").is_in(parts))
    if q.height != len(parts):
        return None
    tot = _sum_rows(list(q.iter_rows(named=True)))
    by = {
        k.removesuffix("_gw"): _r(tot[k], 2)
        for k in [
            "solar_gw",
            "wind_gw",
            "battery_gw",
            "hybrid_gw",
            "gas_gw",
            "nuclear_gw",
            "other_gw",
        ]
    }
    out = {"active_gw": _r(tot["total_gw"], 1), "n_active": tot["n_active"], "by_type_gw": by}
    if len(parts) == 1:
        c = d["qcomp"].filter(pl.col("unit") == code)
        if c.height:
            out["completion_pct_mw"] = _r(c["pct_mw_operational"][0], 1)
        p = d["qpipe"].get(code)
        if p:
            out["expected_twh"] = _r(p["expected_twh"], 1)
    return out


def disclosures(code: str, d: dict) -> list[dict]:
    parts = {code, *COMBINED.get(code, [])}
    u = d["util"]
    cols = [
        c
        for c in [
            "entity",
            "as_of",
            "metric",
            "metric_type",
            "value",
            "unit",
            "source_url",
            "confidence",
        ]
        if c in u.columns
    ]
    rows = (
        u.filter(pl.col("series").is_in(list(parts))).select(cols)
        if "series" in u.columns
        else u.head(0)
    )
    return [{k: v for k, v in r.items() if v is not None} for r in rows.iter_rows(named=True)]


def annotations(code: str, d: dict) -> list[dict]:
    a = d["ann"].filter(pl.col("series") == code)
    return [{"m": r["date"][:7], "label": r["label"]} for r in a.iter_rows(named=True)]


def build() -> dict:
    d = load()
    missing = [s for s in SERIES if s not in d["rank"]]
    if missing:
        raise KeyError(f"series without growth estimates: {missing}")
    (DATA_DIR / "series").mkdir(parents=True, exist_ok=True)
    regions = []
    for code in SERIES:
        s = summary(code, d)
        doc = {
            "code": code,
            "name": NAMES.get(code, code),
            "short": SHORT[code],
            "group": GROUP[code],
            "pair": PAIR.get(code),
            "summary": s,
            "notes": NOTES.get(code, []),
            "monthly": monthly_series(code, d),
            "annual": annual_shape(code, d),
            "supply": supply(code, d),
            "queue": queue(code, d),
            "annotations": annotations(code, d),
            "disclosures": disclosures(code, d),
        }
        _write(DATA_DIR / "series" / f"{_slug(code)}.json", doc)
        regions.append(
            {
                "code": code,
                "slug": _slug(code),
                "name": doc["name"],
                "short": SHORT[code],
                "group": GROUP[code],
                "growth_pct": s["growth_pct"],
                "growth_lo": s["growth_lo"],
                "growth_hi": s["growth_hi"],
                "night_growth_pct": s.get("night_growth_pct"),
                "mw_added": s["mw_added"],
                "from_year": s["from_year"],
                "last12_to": s["last12_to"],
            }
        )
    dc_med = median(d["rank"][b]["growth_pct"] for b in DC)
    comp_med = median(d["rank"][b]["growth_pct"] for b in COMPARISONS)
    index = {
        "cutoff": cutoff().isoformat(),
        "covid": ["2020-03", "2021-12"],
        "dc_median_pct": _r(dc_med, 3),
        "comp_median_pct": _r(comp_med, 3),
        "comparison_median_index": comparison_median(d["monthly"]),
        "regions": regions,
    }
    _write(DATA_DIR / "index.json", index)
    log.info("wrote %d regions to %s", len(regions), DATA_DIR)
    return index


def _slug(code: str) -> str:
    return code.lower().replace("+", "-")


def _write(path: Path, obj) -> None:
    path.write_text(
        json.dumps(obj, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n"
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    idx = build()
    size = sum(p.stat().st_size for p in DATA_DIR.rglob("*.json"))
    print(f"{len(idx['regions'])} regions, {size / 1024:.0f} KB of JSON in {DATA_DIR}")


if __name__ == "__main__":
    main()
