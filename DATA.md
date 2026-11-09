# DATA.md

Data sources, balancing-authority definitions, cleaning rules, weather mapping and known EIA-930 issues. Filled in as the pipeline is built (see CLAUDE.md, Prompts 1–6).

## Sources

### EIA-930 Hourly Electric Grid Monitor
- **Files:** EIA six-month bulk CSVs, `https://www.eia.gov/electricity/gridmonitor/sixMonthFiles/EIA930_{BALANCE|SUBREGION}_{YYYY}_{Jan_Jun|Jul_Dec}.csv`. BALANCE from 2015 Jul–Dec, SUBREGION from 2018 Jul–Dec.
- **Why not API v2:** the API (`electricity/rto/region-data`, `region-sub-ba-data`) only serves data from 2019-01-01, which rules out a 2016–2019 baseline. The bulk files also carry EIA's `Imputed` and `Adjusted` demand columns, which the API does not.
- **Reference tables:** `https://www.eia.gov/electricity/930-content/EIA930_Reference_Tables.xlsx` (BA names, reporting time zone, generation-only flag, activation/retirement dates, subregion list).
- **Refresh:** files whose half-year ended within the last 120 days are re-downloaded on every run, because EIA revises recent data.
- **Outputs:** `data/interim/eia930_hourly.parquet` (BA × UTC hour, wide), `data/interim/eia930_subregion_hourly.parquet` (BA × subregion × UTC hour), `data/reference/ba_metadata.csv`, `data/reference/subregion_metadata.csv`.

| Column | Source column | Notes |
|---|---|---|
| `period_end_utc` | UTC Time at End of Hour | Hour-ending, tz-aware UTC. The join key |
| `period_end_local` | Local Time at End of Hour | BA's *reporting* time zone; for reference only |
| `demand` | Demand (MW) | As reported by the BA (becomes `demand_raw` in cleaning) |
| `demand_imputed` | Demand (MW) (Imputed) | EIA's fill value; non-null only where EIA imputed |
| `demand_adjusted` | Demand (MW) (Adjusted) | EIA's cleaned series (reported, with imputations) |
| `demand_forecast` | Demand Forecast (MW) | Day-ahead forecast |
| `net_generation`, `_adjusted` | Net Generation (MW) | |
| `total_interchange`, `_adjusted` | Total Interchange (MW) | |

Generation-by-fuel columns are left in the raw CSVs (column names change across years) and can be added for stretch goal S1.

**Time zones:** EIA's "Arizona" zone means Mountain Standard Time all year. EPE, PNM and WACM report in it but serve areas that observe DST, so `ba_metadata.csv` has both `tz_reporting` (EIA's zone) and `tz_civil` (used for calendar features).

### PJM Data Miner 2: Hourly Load, Metered (DOM only)

- **Why:** EIA-930's DOM subregion is clean only from 2018-09-18, so it gives ≈ 1.5 pre-COVID years. PJM's metered zonal load goes back to 1993.
- **How obtained:** we couldn't get an API key, so the data was exported by hand from dataminer2.pjm.com → Load → *Hourly Load: Metered*, Load Area = DOM, one CSV per calendar year (2015 → 2026-11-05). Saved as `data/raw/pjm/hrl_load_metered_<year>.csv` and read by `src/glg/ingest/pjm_load.py` → `data/interim/pjm_dom_hourly.parquet`. To refresh, export the files again by hand.
- **Time:** PJM's timestamps mark the start of each hour. We add 1 h so each row is labelled by its UTC end, as in EIA-930. With that shift, PJM and EIA-930 line up best.
- **Quality:** checked for missing, duplicate, zero and negative hours and large jumps (tests skip when the export is absent). PJM can revise data up to 90 days after the fact.
- **Versus cleaned EIA-930 DOM:** consistent within the ±3% annual tolerance used for FERC-714 (tested in `test_annual_reconciliation.py`, which skips if the export is absent). Results are kept only in the git-ignored `data/outputs/private/pjm_check_notes.md`.
- **Licence:** Data Miner terms say *internal use only*, and redistributing the data "or data contained in or derived from" it needs a PJM Redistribution License. **Decision (D3, cautious):** published results use EIA-930 only. PJM serves only as a private check of the DOM baseline. No PJM-derived numbers go in the repo, the report or the figures. Never commit the raw files; `data/` is git-ignored.

### FERC Form 714 (benchmark for annual totals)

- **What:** each planning area's hourly demand, as filed annually with FERC (Part III, Schedule 2). We take it from Catalyst Cooperative's [PUDL](https://catalyst.coop/pudl/) release (`out_ferc714__hourly_planning_area_demand`, `core_ferc714__respondent_id`; CC-BY-4.0). It covers 2006–2025. Downloaded by `src/glg/ingest/ferc714.py` to `data/raw/ferc714/`.
- **Use:** only as the benchmark for EIA-930 annual totals (see "Annual reconciliation" below). The BA ↔ respondent mapping is hand-curated in `data/reference/ferc714_ba_map.csv`.
- **Time stamps:** PUDL converts each respondent's local times to UTC, but the alignment with EIA-930 varies by respondent (a zero or one-hour lag gives the best match, depending on who filed). AZPS's 2020 filing doesn't track EIA at all. An hour's offset barely moves an annual total, so we compare without shifting.

### EIA Form 861 (utility service territories and customers)

- **What:** annual utility survey. We use four tables from Catalyst Cooperative's PUDL release (CC-BY-4.0), 2015–2025: `core_eia861__yearly_service_territory` (utility × state × county), `core_eia861__yearly_sales` (residential customers per utility × state × BA), `core_eia861__assn_balancing_authority` and `core_eia861__yearly_balancing_authority` (utility → BA). Downloaded by `src/glg/ingest/eia861.py` to `data/raw/eia861/`.
- **Use:** the BA → county map (see "BA → county weather mapping" below).

### Census county population and boundaries

- **What:** county population estimates, Vintage 2019 (2010–2019) and Vintage 2025 (2020–2025), plus the 2020 cartographic boundary file (1:500k). Public domain. Downloaded by `src/glg/ingest/census.py` to `data/raw/census/`.
- **Output:** `data/reference/county_population.csv` (county FIPS × year, 2010–2025, on 2020 county boundaries).
- **Series join:** stacked as published, the two vintages step up at 2020, because the 2020 census counted more people than the 2010s estimates expected (+1–2% for most BAs). We close the gap as the Census intercensal method does: each county's 2010s values are scaled by a factor that rises linearly from 1 in 2010 to the 2020/2019 closure ratio. The `source` column says which series a value comes from.
- **Connecticut:** from Vintage 2022 the Census reports Connecticut's 9 planning regions instead of its 8 counties. We split each year's state total by the counties' 2019 shares, so everything stays on 2020 county boundaries (the ERA5 aggregation uses the same boundaries).
- **FIPS changes:** Bedford City, VA (51515) → Bedford County (51019); Shannon County, SD (46113) → Oglala Lakota (46102).
- **2026:** no estimate yet. Use 2025.

### ERA5 hourly reanalysis (weather)

- **What:** ECMWF ERA5 single levels: 2 m temperature and 2 m dewpoint, hourly, on a 0.25° grid over 24–50° N, 125–66° W, from 2015-01 on. The most recent ~3 months are the preliminary ERA5T release, which ECMWF can revise slightly. Downloaded from the Copernicus Climate Data Store by `src/glg/ingest/weather.py`: one request per year, split into monthly NetCDF files in `data/raw/era5/` (about 60 MB a month).
- **Licence:** Copernicus licence: free use with attribution (NOTICE.md).

### NOAA ISD / Global Hourly (airport check only)

- **What:** hourly airport observations (routine METAR reports, FM-15) for 4 airports in focus/comparison BAs, plus 7 more around Phoenix, 2016–2025. Taken from NOAA's open-data bucket (`noaa-global-hourly-pds`) by `src/glg/ingest/isd.py` into `data/raw/isd/`. Values that fail ISD quality control are dropped.
- **Use:** only to sanity-check ERA5 (decision D1). It never feeds the model.

## Cleaning rules

Implemented in `src/glg/build/clean.py`; outputs `data/processed/demand_clean.parquet` (BA) and `subregion_demand_clean.parquet`. Only U.S., non-generation-only BAs that report demand are cleaned (58 BAs). Missing UTC hours are inserted first so every series is a complete hourly grid.

| Flag | Rule | Why |
|---|---|---|
| `flag_missing` | `demand_raw` is null | Not reported |
| `flag_negative` | `demand_raw < 0` | Physically impossible for demand |
| `flag_zero` | `demand_raw == 0` | Every load BA in the set is ≥ ~40 MW; zeros are outages of the data feed, not of the load |
| `flag_flat_run` | Same value as the previous hour, in a run of ≥ 6 identical hours (first hour of the run kept) | Stuck telemetry. EIA's adjusted series does *not* catch these (e.g. SWPP 2017-11-27: 28,368 MW for 9+ h) |
| `flag_level_outlier` | `demand_raw` > 2× or < 0.4× the centred 168-hour rolling median | Unit errors and gross mis-reports |
| `flag_spike` | Jump in and jump out both exceed 5 robust σ (1.4826 × MAD of hour-over-hour changes, per BA-year), in opposite directions | Single-hour spikes (the brief's ">5σ jump" rule, made robust and limited to spikes so real ramps aren't flagged) |
| `flag_eia_imputed` | EIA's `Demand (MW) (Imputed)` is non-null | EIA judged the reported value missing or bad |

- `flag_any` = any of the above. `demand_clean` = `demand_raw` with flagged hours set to null, then gaps of **≤ 3 hours** linearly interpolated (`flag_interpolated`). Longer gaps stay null; models drop them.
- `demand_eia_adjusted` (EIA's own cleaned series) is kept for comparison. It fills long gaps with model-based imputations, which we don't want to train on.
- Subregion series get the same rules except `flag_eia_imputed` (the subregion files have no imputed column).
- Result: most shortlisted BAs keep > 99% of hours; see `data/outputs/ba_quality.csv`.

## Known EIA-930 issues and BA changes

**Coverage and structure**
- BALANCE data begins 2015-07-01; SUBREGION data begins 2018-07-01. API v2 only from 2019-01-01.
- 72 BAs appear in total: 58 report demand, the rest are generation-only (e.g. AVRN, DEAA, GRID, GWA, SEPA, YAD).
- Retired during the period: AEC (2021-09), EEI (2020-02), GLHB, GRIF, GRMA, HGMA, NSB (2020-01), OVEC (2018-12), WAUE (2015-10), WWA (2025-10), WACM and WAUW (2026-04, into the new SWPW). New: AVRN (2018), GLHB (2020), SIKE (2025), BHBA and SWPW (2026-04).

**Data problems found in profiling (Prompt 2)**
- **SEC** (Seminole Electric): long stretches of negative demand in 2017–2020 (8,889 negative hours). Excluded.
- **SPA**: demand steps down to ~30% of its old level in late 2023. Excluded.
- **PSEI**: 13,200 missing hours (2018–19 and most of 2020) and ~20% lower level afterwards. Excluded.
- **WACM**: sudden ~50% step up in early 2023. Excluded.
- **TEPC**: summers 2019 and 2021 report about half the normal load. Excluded.
- **SC**: 2018–2019 gaps and an unexplained 2019 dip. Excluded (not needed).
- **SWPP**: ~2,900 hours of stuck values in 2017–2020 and a 2018 gap; a year at 71% clean coverage. Conditional.
- **PACE / PACW**: 2015–2017 heavy EIA imputation; PACE a year at 81%. PACE conditional.
- **Subregion DST bug:** PJM zone data is missing the whole November DST fall-back day (25 h) in 2018, 2019, 2023, 2024 and 2025.
- **PJM zones** are missing 2018-07-09 → 2018-09-18 entirely. **DOM therefore has only ~12,900 clean pre-COVID hours (≈1.5 years, 2018-09 → 2020-02).**
- **RECO** (PJM zone): mostly missing 2019–2022.
- **Subregion consistency check:** subregions sum to their parent BA within ~0.5% (CISO, NYIS, SWPP, MISO), ~1.3% (ERCO), ~2% (ISNE) and 1–3% low for PJM (RECO gaps and unzoned load). Internally consistent.

**Subregion start dates:** all subregions in the 2018-07 file start 2018-07-01 (PJM effectively 2018-09-18, see above). Newer: BHBA subregions 2026-04; PNM's smaller subregions are sparse (ACMA 5%, Jica 12%).

**DST days (Prompt 3, `tests/test_dst.py`)**
- After `complete_hours`, every BA has exactly one row per UTC hour, so local days have 23 / 24 / 25 rows. Arizona BAs (AZPS, SRP) always have 24.
- Several BAs' raw data **drops a whole DST day**, which is unrelated to EIA's normal gap rate. These hours stay null and are never interpolated:
  - CPLE, DUK and FPC: spring 2016 (CPLE and FPC only), 2018 and 2019, plus fall-back 2025-11-02. That's the whole Duke family at once, so it's probably a Duke submission problem.
  - SOCO: every fall-back day 2015–2019, plus spring 2017 and 2018.
  - TVA: spring 2016–2018.
  - NEVP: 9 hours on each spring-forward day 2016–2020.
  - PJM: whole or near-whole DST days from 2017 on (fall-back 2017, 2023, 2024 and 2025; spring 2020, 2024 and 2026, among others), plus single hours on the 2015–2019 DST days. This mirrors the PJM subregion DST bug above.
  - CISO: spring 2018.
- Copied hours at fall-back (the repeated 01:00 hour equal to the previous one): GCPD 4 of 11 years, ISNE 2 of 11. That's too few to matter, so we added no rule.
- Effect: DST days are slightly under-represented in the model for those BAs (≤ 1 day a year). No bias in growth estimates is expected.

**Modelling shortlist:** `data/reference/ba_shortlist.csv`. 19 BAs/zones included, 4 conditional (DOM, DOPD, SWPP, PACE), 6 excluded, each with a reason. Criteria: ≥ 95% clean hours in 2016–2019 and 2022–2025, no year below 90%, mean load > ~500 MW, no unexplained level shift.

## Annual reconciliation (Prompt 3)

**Benchmark choice:** FERC Form 714. The alternatives were weaker:
- **EIA-930's own annual summaries** are the same data, so they aren't independent.
- **EIA-861 retail sales** leave out losses (~4–7%, varying by utility and year) and are reported by utility, not by BA, so ±3% can't be tested honestly.
- **FERC-714** is the BA's or planning area's own *annual, revised* regulatory filing of hourly demand. That is a separate submission from EIA's real-time collection, though it uses the same utility meters. So it catches definition, boundary and reporting problems, not metering error.

**Method** (`src/glg/build/benchmarks.py` → committed `data/reference/annual_benchmarks.csv`; tested by `tests/test_annual_reconciliation.py`):
- Sum EIA-930 `demand_clean` and FERC-714 demand over the **hours present in both**, per BA × year, 2016–2025. This way gaps don't count as disagreement.
- Pass if |EIA / FERC − 1| ≤ 3%.
- For a known *definition difference* (FPC), pass if the error stays within 3 pp of its median, since growth estimates depend on a stable ratio, not on the level.
- Fewer than 90% of the year's hours in common = `n/a`.
- PACE is compared as PACE + PACW, CPLE as CPLE + CPLW, and NEVP against Nevada Power + Sierra Pacific (each pair files one planning area).

**Results:** 185 of 210 BA-years pass, 22 fail, 3 n/a. Values are EIA-930 vs FERC-714 in %; **bold = fail**; FPC is tested on ratio stability.

| BA | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---|---|---|---|---|---|---|---|---|---|
| AZPS | 2.3 | 2.5 | 1.0 | -0.1 | **-85.2** | -0.4 | 0.1 | 0.8 | 0.9 | **7.1** |
| BPAT | -0.0 | -0.0 | -0.0 | 0.0 | 0.0 | 0.0 | -0.0 | 0.0 | -0.0 | -0.0 |
| CISO | 0.8 | 0.0 | 0.5 | -1.7 | -0.5 | -0.5 | -0.0 | 0.5 | -0.9 | -1.2 |
| CPLE | -0.7 | -0.5 | -0.5 | -0.2 | -0.2 | -0.4 | -0.4 | -0.6 | -0.2 | -0.3 |
| DOPD | **-7.0** | **-7.3** | **-6.9** | **-6.2** | n/a | -0.6 | 0.0 | 0.0 | 0.0 | 0.0 |
| DUK | -0.3 | -0.3 | -0.1 | -0.0 | -0.1 | -0.1 | -0.1 | -0.0 | -0.0 | 0.0 |
| ERCO | 0.1 | 0.1 | 0.2 | -0.0 | -0.2 | -0.0 | 0.2 | 0.5 | 0.5 | -0.0 |
| FPC | 30.4 | 31.5 | 31.3 | 31.7 | 31.8 | 32.6 | 33.4 | 33.3 | 34.6 | 34.9 |
| FPL | **4.0** | **4.9** | **4.5** | 3.0 | 2.4 | **3.1** | 2.5 | -0.3 | -1.1 | -0.8 |
| GCPD | 0.6 | 1.2 | 1.2 | 1.1 | 1.1 | 0.9 | 0.9 | 1.0 | 0.8 | 0.8 |
| ISNE | 0.9 | 0.4 | -0.1 | -0.7 | -1.1 | -1.3 | -1.6 | -2.4 | -2.0 | -2.1 |
| LGEE | -0.1 | -0.0 | -0.2 | -2.7 | **-3.4** | **3.9** | **17.0** | **18.6** | **19.0** | **6.5** |
| MISO | -1.0 | -0.4 | -0.9 | -1.6 | -1.4 | -1.6 | -1.8 | -1.4 | -1.7 | -1.9 |
| NEVP | 0.0 | -0.0 | 0.0 | 0.0 | -0.1 | -0.1 | 0.2 | 0.0 | -0.0 | 0.0 |
| NYIS | 0.0 | -0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | -0.0 | -0.0 |
| PACE | n/a | **-3.4** | -2.7 | -2.4 | **-4.2** | -2.8 | -2.9 | -2.0 | -1.7 | -3.0 |
| PJM | 1.9 | 1.8 | 1.8 | 1.6 | 1.7 | 1.7 | 1.9 | 1.9 | 1.9 | 1.8 |
| PSCO | -1.1 | -1.2 | -0.6 | 0.4 | -1.2 | -2.3 | -2.0 | 0.2 | **5.4** | **6.2** |
| SRP | -0.4 | -0.4 | -0.5 | -0.2 | -0.0 | 0.0 | -1.5 | 1.1 | 1.7 | 1.5 |
| SWPP | -0.2 | -0.3 | n/a | -0.2 | **11.1** | -0.2 | -0.2 | -0.2 | 0.0 | **-6.8** |
| TVA | -0.0 | -0.0 | -0.0 | 0.0 | -0.0 | -0.0 | -0.2 | -0.5 | -0.5 | -0.6 |

No benchmark: **SOCO** (its member utilities file separately and don't sum to the BA) and **DOM** (PJM zone; checked internally against PJM metered load, which passes within 3% for 2019–2025).

**Failures explained**
- **AZPS 2020:** FERC's 2020 filing is about 6.8× too large all year, an error in the filing. EIA is fine.
- **AZPS 2025 ⚠ (focus BA):** EIA-930 runs 8–22% above FERC each month from 2025-09, and the gap is widening (+21.5% in 2026-01). EIA is internally consistent (demand = generation − interchange to within a few MW) and its year-on-year growth is mostly +5–20% a month, with no single step. It's unresolved which source has the boundary right. **AZPS growth from late 2025 must be cross-checked against APS disclosures (Prompt 14) before it's published.**
- **DOPD 2016–2019:** EIA is about 7% below FERC until 2021, then identical, which points to a definition change in one source. DOPD is conditional anyway; use 2021 on only, or drop it.
- **FPL 2016–2021:** EIA runs 2.4–4.9% above FERC until 2022, then −0.3 to −1.1%. This is a drift of about 5 pp in a comparison BA. Possibly linked to Gulf Power's integration into FPL (2021–22), but not confirmed. It would make FPL's EIA growth since 2016 about 5% *lower* than FERC's, so it biases against finding growth.
- **LGEE 2020–2025 ⚠:** FERC falls ~15% between 2019 and 2023 (35.7 → 30.5 TWh) while EIA rises, giving a gap of up to +19%, then partly closes in 2025. This looks like a boundary or definition break in one source. **Recommend: LGEE → conditional** (comparison only, with this caveat).
- **PACE 2017, 2020:** PACE + PACW runs 3.4% and 4.2% below PacifiCorp's filing. The usual gap is −2 to −3%, so these years only just fail. Minor; PACE stays conditional.
- **PSCO 2024–2025 ⚠:** EIA is 5–9% above FERC from 2023-08, a step that's still unexplained. **Recommend: treat PSCO post-2023 growth as unreliable** (comparison BA).
- **SWPP 2020:** FERC is low in Jan–Feb and Jun–Aug 2020, a filing gap. SWPP 2025: EIA is about 7% below FERC all year, which fits footprint changes before SPP's 2026-04 western expansion. SWPP stays conditional.

**Other notes:** FPC's definition gap grows from +30% to +35% over the decade, so EIA's FPC grows ~3.5% faster than Duke Florida's own filing. ISNE drifts by −3 pp, still within tolerance. All 9 focus BAs with a benchmark pass every baseline year (2016–2019).

## BA → county weather mapping

Built by `src/glg/build/ba_weather_map.py` (`python -m glg.build.ba_weather_map`). Validated in `notebooks/02_ba_mapping.ipynb` and `tests/test_ba_mapping.py`.

**Output:** `data/reference/ba_county_map.csv` has one row per BA × year × county: `ba, year, county_fips, state, county, weight, res_customers_alloc, source`. It covers every EIA-861 BA code, plus DOM and CPLW, for 2015–2025. Later years reuse 2025. A per-BA-year summary (counties, population, people per residential customer) is in `data/outputs/ba_mapping_summary.csv`.

**What the weight means:** the share of a county's residential customers that belong to the BA. A county's weights sum to 1 across BAs. DOM is the exception, because it is a zone inside PJM. For weather, weight each county by **weight × population** and normalise per BA (`population_weights()`). We recommend a **fixed population year** (`pop_year=2020`) so that people moving between counties can't move the weather series. Population growth is part of demand growth, not weather.

**Method:**
1. **Customers per utility and BA:** residential customers per (utility, state, BA) from EIA-861 sales, excluding energy-only service.
2. **BA-code blips:** if a utility's BA code changes for a run of years and then returns to the same code, the run is treated as a reporting blip and gets the surrounding code. Example: Colorado Springs reports WACM every year except PSCO in 2019–21. Permanent moves are kept. This affects 5 utility-state-years.
3. **Wires-only utilities:** the Texas wires utilities (Oncor, CenterPoint, AEP Texas, TNMP) list counties but report no residential customers, because Texas retail providers report those customers and have no county list. Since 2020, EIA-861 also folds small short-form utilities into "Adjustment" rows with no counties. In each (state, BA), that pool of unplaced customers is shared among utilities that have counties but no customers. The share follows reach: the population of the utility's counties, with each county's population split equally among the utilities that serve it. The BA comes from EIA-861's BA-association table. This places 7–12M customers a year.
4. **Placing customers in counties:** customers are placed by raking (iterative proportional fitting), state by state. Each utility keeps its customer total, and each county's total is pinned to its share of the state's population. Plain population-proportional allocation would send most of a utility's customers to any big county it touches only at the edge. Raking pushes them into the counties where that utility is the main supplier.
5. **Weight:** a BA's share of each county's allocated customers. Customers with an unknown BA (0.2–0.3%) are left out.
6. **Same-named cities:** EIA-861 writes "Fairfax", "Richmond", "Roanoke", "Franklin", "St. Louis" and "Baltimore", and PUDL resolves these to the independent *city*. That dropped Fairfax County (51059), the heart of Northern Virginia, from the map. EIA adds "City" when it means only the city, so a bare name is read as both the city and the county of that name.
7. **DOM (PJM Dominion zone):** EIA-861 reports DOM-zone utilities as PJM. For PJM counties in VA/NC, DOM's share is Dominion (VEPCO) customers ÷ customers of the zone *anchor* utilities. The anchors are VEPCO → DOM, Appalachian Power → AEP and A&N Electric Co-op → DPL. Counties served only by co-ops or municipal utilities take the share of the nearest anchored county.
8. **CPLW:** EIA-861 reports Duke Energy Progress's western BA (Asheville) as CPLE. CPLE counties in North Carolina west of −81.5° (county centroid) are moved to CPLW.

**Checks (all in `tests/test_ba_mapping.py`):**
- Weights sum to 1 per county.
- Every continental U.S. county is mapped in every year, except up to 3 small Virginia cities that EIA-861 skips in single years (Norton 2020–21; Galax and Lynchburg 2025).
- People per residential customer is 1.9–3.1 for every shortlisted BA (household size is about 2.5).
- ERCO maps to 24–29M people (ERCOT serves about 26–27M).
- Known counties land in the right BA: Loudoun, Fairfax, Prince William and Richmond → DOM; Dallas and Bexar → ERCO; Miami-Dade → FPL; Charlotte → DUK; Escambia → SOCO in 2019 and FPL in 2023; Accomack (DPL) and Roanoke County (AEP) are not DOM; Maricopa is split between AZPS and SRP.
- Validation maps for ERCO, DUK, SRP/AZPS, FPL (2019 vs 2023) and DOM vs PJM are in `report/figures/ba_mapping_validation.png`.

**Boundary changes the map follows** (mapped population changes by more than 3% in a year):

| BA | Year | Change | Cause |
|---|---|---|---|
| SOCO | 2021 | +6.4% | PowerSouth (AEC) BA retired 2021-09 and folded into SOCO |
| SOCO / FPL | 2022 | −4.5% / +10.4% | Gulf Power (NW Florida) moved from SOCO to FPL. ⚠ EIA-930 FPL demand steps up about 1.3–1.5 GW year on year from about **2022-07**, not January, and SOCO runs lower from about 2022-08. 2022 is a transition year (see below) |
| FPC | 2022 | +3.2% | Co-op/municipal reporting changes |
| DOPD | 2020 | +9.6% | Tiny BA; reporting change |

**Known weaknesses:**
- **Slivers:** a county list can't say how much of a county a utility serves. When a large utility from another BA lists a county for a sliver, raking still leaves it a share. Harris County (Houston) comes out 86% ERCO (truly ~99%, because Entergy Texas lists it). Wake County (Raleigh) comes out 59% CPLE, 41% DUK (truly ~all CPLE). Grant County, WA comes out 70% GCPD, 27% Avista. The extra weight lands on a neighbouring area with a similar climate, so the effect on BA temperature should be small. Prompt 5 will test this by comparing BA temperature under these weights with whole-county weights. Utility territory polygons (HIFLD Electric Retail Service Territories) would fix it, at the cost of a block-level population overlay.
- **DOM's northern edge:** the former Potomac Edison counties of the northern Shenandoah Valley (Frederick, Clarke, Warren, Shenandoah, Page; ~0.3M people) are PJM's APS zone. Co-ops serve them now, so the nearest-anchor rule assigns them to DOM. Their climate is the same as Loudoun's.
- **Annual resolution:** EIA-861 is annual, so a boundary change lands on 1 January of the reporting year even if EIA-930 changes mid-year (FPL/SOCO in 2022).
- **Population is residential-weighted.** It is the right weight for weather-driven load (heating and cooling). It is the wrong weight for data-center load, but that load isn't weather-driven, and finding it is what this project is about.

**⚠ For the modelling prompts (7–10): FPL and SOCO.** Gulf Power's ~1.3–1.5 GW enters FPL's EIA-930 demand in mid-2022 and leaves SOCO's. Unhandled, this will look like ~8% structural *growth* in FPL (a comparison BA) and a matching loss in SOCO (a focus BA). Options: model FPL + SOCO together, add a level-shift term from the break month, or compare FPL only within its pre-2022 and post-2022 footprints.

## Weather features (Prompt 5)

Built by `src/glg/build/weather.py` (`python -m glg.build.weather`; incremental, monthly). Checked by `src/glg/build/weather_check.py`, `tests/test_weather_coverage.py` and `notebooks/03_weather.ipynb`.

**Pipeline:**
1. **Grid → county:** each ERA5 point is the centre of a 0.25° cell. A county's value is the mean of the cells it overlaps, weighted by overlap area in an equal-area projection (EPSG:5070). This covers 3,108 continental counties (2020 boundaries) and about 10 cells per county; weights in `data/interim/era5_county_cells.parquet`.
2. **County × hour:** `data/interim/weather_county/YYYY-MM.parquet` holds `t2m_c` and `d2m_c` (°C) at ERA5's instants (`valid_time_utc`, on the hour).
3. **Non-linear features per county, before averaging:** relative humidity, heat index and degree-hours are computed for each county and *then* averaged. Averaging temperature first would understate cooling degree-hours in a large BA where one part is hot and another isn't.
4. **County → BA:** a population-weighted mean using the BA → county map for each year (2015–2025; later years reuse 2025) and fixed 2020 county population (decision D8). The result covers 58 BAs, including DOM and CPLW.
5. **Hour-ending alignment:** EIA-930 labels each hour by its end, while ERA5 values are snapshots on the hour. The value for the hour ending at *t* is the mean of the snapshots at *t* − 1 h and *t*, in `period_end_utc`. All features are averaged the same way, which is valid because they are averaged over counties linearly.
6. **Thermal inertia:** trailing 24 h and 72 h means of temperature, and temperature 24 h earlier. These are time-based, so a window or lag never reaches across a period when a BA is absent from the map; it is null instead.

**Output:** `data/processed/weather_ba_hourly.parquet` (one row per BA × hour-ending UTC; float32).

| Column | Meaning | Units |
|---|---|---|
| `ba` | EIA-930 BA code (plus `DOM`, `CPLW`) | |
| `period_end_utc` | end of the hour, UTC (EIA-930 convention) | |
| `t2m_c`, `d2m_c` | population-weighted 2 m temperature and dewpoint | °C |
| `rh_pct` | relative humidity (Magnus formula, Alduchov & Eskridge 1996) | % |
| `hi_c` | NWS heat index (Rothfusz regression with NWS adjustments) at ≥ 60 °F; air temperature below 60 °F | °C |
| `cdh55f`, `cdh65f`, `cdh75f` | cooling degree-hours, max(T − base, 0) | °F·h per hour |
| `hdh55f`, `hdh65f`, `hdh75f` | heating degree-hours, max(base − T, 0) | °F·h per hour |
| `t2m_c_mean24`, `t2m_c_mean72` | trailing 24 h and 72 h mean of `t2m_c` (null until the window is full) | °C |
| `t2m_c_lag24` | `t2m_c` 24 h earlier | °C |

**Coverage:** every shortlisted BA, DOM included, has weather for 100% of its clean EIA-930 demand hours in every year through the last ERA5 month. Nulls occur only in the inertia columns' first 23, 71 or 24 hours. Data runs from 2015-01-01 01:00 UTC to the end of the last downloaded month.

**Check against airports** (`data/outputs/weather_station_check.csv`; ERA5 mean for the county containing the airport, minus the airport):

| Airport (BA) | Bias | RMSE | r (hourly) | Comment |
|---|---|---|---|---|
| Dulles IAD (DOM) | −0.7 to +0.2 °C | 1.2–1.3 °C | 0.993 | no drift (2024 dip reverses in 2025) |
| DFW (ERCO) | −0.8 to 0.0 °C | 0.9–1.2 °C | 0.993–0.996 | no drift |
| Miami MIA (FPL) | −0.8 to −0.3 °C | 1.0–1.1 °C | 0.97–0.98 | afternoon peaks ~1–3 °C low: coastal cells are part sea |
| Phoenix PHX (SRP/AZPS) | −1.5 to −2.4 °C | 2.0–2.7 °C | 0.992 | ⚠ see below |

ERA5 is a 0.25° average over a whole county, so it should be smoother than a runway thermometer. A steady bias is harmless: a per-BA model learns it. A *change* in the bias is what matters.

**⚠ Phoenix drift:** ERA5-minus-Sky Harbor widens from about −1.6 °C (2016–2022) to −2.1/−2.4/−2.2 °C (2023/24/25), by about the same amount at every temperature, winter included. So it isn't ERA5 under-reading extreme heat. Against 7 other Phoenix-area airports, the shift ranges from −0.7 °C (Luke AFB) to +0.2 °C (Deer Valley), with a **median of about −0.3 °C**. Most of Sky Harbor's step is local to that station, but ERA5 may under-capture about 0.3 °C of recent warming in metro Phoenix (urban growth). In that case the weather model under-predicts Phoenix cooling load after 2022, and part of it shows up as structural growth in AZPS and SRP. **Prompt 15 must re-run AZPS/SRP with ERA5 + 0.3 °C from 2023 and report how much the growth estimate moves.** None of the other three airports drifts by more than 0.5 °C (tested).

**Mapping sensitivity** (`data/outputs/weather_mapping_sensitivity.csv`): BA temperature under the raked BA → county weights, compared with a crude version that counts every county a BA touches in full, for 2019 and 2024. Hourly correlation is ≥ 0.997 for every shortlisted BA. Level offsets reach about 1 °C (GCPD −1.2, MISO +1.0, NEVP −0.7) but barely change between the two years (≤ 0.27 °C). A per-BA model absorbs a constant offset, so the mapping weaknesses (slivers) can't put a trend into the weather series.

**Known limitations:**
- No land-sea mask. Coastal counties average in some cells that are partly sea, which damps extremes (Miami above). Doing better would need ERA5's land-sea mask and land-only weights.
- ERA5T (the last ~3 months) can be revised by ECMWF. Re-download those months before final results.
- Population-weighted temperature represents weather-driven load (homes and offices), not data centers. That is intended.

**Disk (2026-11-08):** raw ERA5 NetCDF 8.0 GB (`data/raw/era5`, internal SSD); county × hour Parquet 2.2 GB; BA instants 0.27 GB; final BA table 0.24 GB; ISD 0.55 GB. The county Parquet holds everything needed to rebuild the BA features under a different map or population year. The raw NetCDF is needed only to re-grid (new boundaries, a land-sea mask) or to add variables, so it could be archived to Drive3 and deleted from the SSD (see CLAUDE.md, Storage).

## Feature dictionary (modelling table, Prompt 6)

Built by `src/glg/build/features.py` (`python -m glg.build.features`) → `data/processed/model_table.parquet`. Tested in `tests/test_features.py`.

**Rows:** one per modelled BA × hour (hour-ending UTC). The modelled BAs are the 23 shortlisted ones (`include` = yes or conditional), DOM included. Each BA's rows run from its first EIA-930 hour (2015-07-01; DOM 2018-07-01) to the last. This gives 2.25M rows. Weather is null after the last downloaded ERA5 month.

**Time convention:** local calendar features use the local civil time of the hour's **start**, so `local_hour` 0 is 00:00–01:00 local. The time zone is the BA's civil zone from `ba_metadata.csv`; DOM uses America/New_York. MISO is labelled Eastern although much of its load is on Central time. That's harmless: the hour is only a category to the model, and both zones change DST on the same days. Joins are always on `period_end_utc`, never on local time.

| Column | Meaning |
|---|---|
| `ba`, `period_end_utc` | key (UTC, hour-ending) |
| `local_date`, `local_hour`, `tz` | local date and hour (0–23) of the hour's start; IANA zone |
| `demand_raw`, `demand_clean`, `flag_any` | EIA-930 demand (MW) before and after cleaning; any cleaning flag (see "Cleaning rules"). DOM comes from its PJM subregion series |
| weather columns | as in "Weather features": `t2m_c`, `d2m_c`, `rh_pct`, `hi_c`, `cdh55f/65f/75f`, `hdh55f/65f/75f`, `t2m_c_mean24`, `t2m_c_mean72`, `t2m_c_lag24` |
| `dow` | day of week, 0 = Monday |
| `month`, `doy` | month (1–12), day of year (1–366) |
| `is_weekend` | Saturday or Sunday |
| `is_holiday`, `holiday_name` | U.S. federal holiday, on the actual date *and* the observed weekday. Juneteenth counts from 2021 |
| `is_bridge` | Friday after Thanksgiving, Dec 24, and weekdays Dec 26–31 that aren't holidays |
| `day_type` | one category in priority order: `holiday_major` (New Year's Day, Memorial Day, July 4, Labor Day, Thanksgiving, Christmas) › `holiday_minor` (MLK Day, Presidents' Day, Juneteenth, Columbus Day, Veterans Day: mostly government and banks closed) › `bridge` › `saturday` / `sunday` › `weekday` |
| `is_dst` | local zone on daylight time (always false in Arizona) |
| `is_dst_change_day` | local day of 23 or 25 hours (measured from the time zone, not by counting rows) |
| `is_covid` | hour falls in 2020-03-01 → 2021-12-31 (UTC). Excluded from every baseline |
| `in_baseline` | UTC year 2016–2019 and not COVID. For DOM, EIA-930 is clean only from 2018-09-18 (D3), so its baseline is shorter |
| `trend_years` | years since 2015-07-01. **Diagnostics only: never a feature in the baseline model** |
| `emp_index` | optional economic control (see below), 2019 = 100 |

**Economic-activity control (`emp_index`):** BLS Current Employment Statistics, total nonfarm employment by state, monthly, seasonally adjusted. It's taken from FRED (series `{ST}NA`; `src/glg/ingest/employment.py`). Each BA's index is a weighted mean of its states' indexes (2019 average = 100). The weights are the BA's 2020 population in each state, from the 2019 BA → county map, so the footprint stays fixed. The value for an hour is that month's.
- **Why this one:** it's cheap, monthly, public, and covers every state. GDP by state is quarterly and revised heavily; industrial production has no state detail.
- **Why it's not in the primary model:** employment rises almost steadily (ERCO and AZPS +11–12% from 2019 to 2025). Fitted on 2016–2019, its coefficient mostly re-learns the baseline's trend and projects it forward, so it can absorb the very growth we want to measure. Data centers add little employment, so the control would not separate them from general growth anyway. It is therefore used only as a robustness specification in Prompt 15 (with vs without), as the brief asks.
- **Caveat:** CES state data is revised each March (benchmarking). The table uses the vintage downloaded on the build date.

**No leakage:** nothing in the table is fitted, scaled or normalised on the data. The holiday lists and time zones are fixed rules, and the employment index is normalised to 2019, a baseline year. `test_no_leakage_of_later_data_into_baseline_rows` rebuilds the table from data cut at 2020-01-01 and checks that the baseline rows are identical. That test also caught a bug: DST days had been detected by counting rows, which wrongly flagged partial days at the edges of the data.

## Baseline models and validation (Prompt 7)

**Models.** Fitted per BA on baseline hours (2016–2019, COVID excluded; DOM: EIA-930 from 2018-09-18) with a clean target and complete weather. Neither model has a time trend or the employment index (tested).

- **GAM** (`models/normalize_gam.py`, primary): pygam `LinearGAM` in MW with these terms:
  - `te(temperature, local hour)`: the temperature response, allowed to differ by hour;
  - `s(72 h mean temperature)`: thermal inertia;
  - `s(dewpoint)`: humidity;
  - `te(day of year, hour)`: season and daylight;
  - `te(hour, day type)`: the daily shape of weekdays, weekends, holidays and bridge days.

  Hour and day of year use cyclic bases that wrap 24 → 0 and 366 → 1. Smoothing is fixed at λ = 0.6: on ERCO, CISO, PJM and AZPS, λ = 10 was slightly worse and a log target was no better. Splines extrapolate linearly outside the baseline's temperature range.
- **Degree-day OLS** (`models/normalize_ddr.py`, sanity floor): least squares with:
  - fixed effects: hour × day type, and month;
  - hour-specific slopes on CDH65 and HDH65;
  - extreme-temperature terms: CDH75 and HDH55;
  - cooling and heating degree-days of the 24 h and 72 h mean temperature.

**Validation** (`python -m glg.models.validate`):
- Leave one year out. DOM leaves one month out instead, because its EIA-930 baseline is about 16 months.
- Metrics per held-out year and pooled: MAPE, *shape MAPE* (MAPE after removing that year's mean error), bias, R² and RMSE.
- **Residual trend**: the monthly in-sample residual (% of demand) regressed on time, with HAC standard errors.

Outputs (`data/outputs/`): `validation_metrics.csv`, `baseline_residual_trend.csv`, `validation_flags.csv`, `baseline_predictions.parquet`.

**Rules.**
- **fail** if pooled shape MAPE > 5% (the model can't follow weather and calendar).
- **flag** if pooled held-out MAPE > 5%, |residual trend| > 1 %/yr, or the baseline is short.

**Results.**
- The GAM beats degree-day OLS on shape MAPE in every BA: GAM 2.0–3.5% vs OLS 2.3–4.1% (excluding DOPD).
- Most held-out error is a level effect, not a weather-fit effect. Because the models have no trend, a BA that was growing in 2016–2019 is over-predicted when 2016 is held out and under-predicted for 2019 (ERCO +5.1% / −4.3%).
- That in-baseline trend is real growth that started before 2022, and Prompt 9 compares post-2022 growth against it.

| GAM status | BAs |
|---|---|
| pass | BPAT, CPLE, DUK, FPC, FPL, MISO, NYIS, PJM, SOCO, TVA |
| flag: growing in the baseline (> +1 %/yr) | ERCO +2.2, GCPD +3.5 (also held-out MAPE 6.7%), PACE +1.9, PSCO +2.4, NEVP +1.1, SRP +1.1, SWPP +1.1, DOM +1.5 (also short baseline) |
| flag: shrinking in the baseline (< −1 %/yr) | CISO −2.1, ISNE −1.7, AZPS −1.5, LGEE −1.3 |
| **fail** | **DOPD**: shape MAPE 5.3%, trend +5.2 %/yr, 2019 bias −13% |

**Steps inside the baseline** (from the monthly residuals):
- **PSCO +8% from May 2019.** FERC 714 shows the same jump (44.4 → 47.7 TWh, 2018 → 2019), so the BA's load really changed then, rather than this being an EIA-930 error. Organic growth looks smooth, not like a one-month step. The 2019 held-out bias is −8%.
- **LGEE about −5% from April 2019.** EIA-930 runs 2.7% below FERC in 2019. This is the start of the LGEE break already pinned in Prompt 3.

Both are comparison BAs only.

**DOM, private check (decision D3; nothing derived from it is published).** The same GAM was fitted on PJM metered DOM load for 2016–2019 (`validate.pjm_dom_check`, output in `data/outputs/private/`). In words only: the fit is as good as for the public BAs; DOM load was already growing before 2022, consistent with its EIA-930 residual trend; and the short EIA-930 window shifts the level relative to the baseline but leaves growth since 2022 practically unchanged.

## LightGBM comparison (Prompt 8)

**Model** (`models/normalize_gbm.py`). LightGBM with the same contract as the GAM: weather and calendar only, no trend. Its features are:
- temperature, dewpoint, 24 h and 72 h mean temperature, and temperature 24 h earlier;
- local hour, day of week, day of year, and day type (categorical).

Settings are fixed: 31 leaves, learning rate 0.05, 500 rounds, at least 200 hours per leaf, deterministic, seed 42. Validation is the same as for the GAM. Outputs:
- `data/outputs/model_comparison.csv`: BA × model held-out MAPE and shape MAPE, in-sample residual trend, the GBM's absorbed trend, and the share of hours since 2022 outside the baseline's temperature range;
- `model_level_gap.csv`: the GBM vs GAM mean counterfactual since 2022, in %.

**Findings.**
- *Fit:* the GBM is better on held-out shape MAPE in 18 of 23 BAs, by a median of about 0.2 percentage points. It is worse in DOPD, GCPD, PACE, PSCO and CISO.
- *Time trend:* out of fold, the GBM's bias by held-out year matches the GAM's, so it is not extrapolating a trend. In-sample, though, its residual trend is about half the GAM's in every BA that trended (ERCO 2.2 → 1.2 %/yr, DOM 1.45 → 0.5, GCPD 3.5 → 1.6), while degree-day OLS agrees with the GAM. With every baseline year in training, the GBM partly memorises each year's level through weather patterns specific to that year.
- *Effect on the counterfactual:* small. Since 2022, GBM and GAM agree on mean predicted load within ±0.4% in nearly every BA-year (GCPD ±0.9%), with no consistent sign.
- *Extrapolation:* trees go flat above the hottest baseline hour. Few hours since 2022 are outside the baseline range (≤ 0.15% per BA), but this is exactly the record-heat case a sceptic would check.
- *Physical response:* partial dependence and TreeSHAP for DOM and ERCO (`report/figures/gbm_gam_temperature_response.png`) show heating and cooling arms meeting at about 15–18 °C in both models. The GBM's curves are steppier.

**Decision: the GAM stays the primary model for every BA.** The GBM is the model-family robustness check (Prompts 9 and 15). Its gap from the GAM is reported as model uncertainty.

## Structural growth estimates (Prompt 9)

**Method** (`models/growth.py`, `python -m glg.models.growth`).
- For every hour since 2016, the baseline GAM, fitted on 2016–2019, predicts demand under the baseline's relationship to weather and calendar. Structural growth = actual − predicted.
- Aggregates are ratios of sums, r = Σactual / Σpredicted, over hours with clean demand and complete weather:
  - `resid_pct` = 100 (r − 1);
  - the index is 100 × r / r₂₀₁₉;
  - MW are average megawatts, and TWh/yr = average MW × 8,766 h.
- **95% intervals** combine two parts:
  - a jackknife over baseline years, where the GAM is refitted with each baseline year left out (DOM: each baseline month);
  - a 14-day moving-block bootstrap of daily residuals within each period.
- The GBM and degree-day OLS values are reported alongside as the model-family check.
- **Growth since 2022** compares the latest 12 months (to 2026-10) with calendar 2022, two ways (decision D11):
  - against the flat baseline (headline);
  - *beyond trend*: after extending the BA's own 2016–2019 residual trend forward.

**Series rules (breaks found in Prompts 3–4 and here):**
- **FPL / SOCO:** Gulf Power entered FPL's EIA-930 demand in 2022-07 (FPL steps up about 2 GW year on year). Their growth is measured from 2023 instead. **FPL+SOCO** combined is unaffected by the move and is measured from 2022.
- **AZPS:** EIA-930 runs 8–22% above FERC 714 from 2025-09 (unresolved), so the headline window ends 2025-08. `AZPS_full` is the sensitivity version.
- **AZPS / SRP ⚠:** from 2025 the split of Phoenix load between the two BAs swings:
  - AZPS spikes to +36% above baseline in 2025-02;
  - SRP falls from +20% to +7% between 2026-04 and 2026-10 while AZPS rises from +28% to +40%.

  Combined, they are steady at +20–25% in 2026. **AZPS+SRP** (to 2025-08) is the Phoenix headline.

**Results** (`data/outputs/growth_ranking.csv`; 95% intervals in brackets; *beyond trend* = after removing the BA's 2016–2019 trend):

| Series | Growth since 2022 | Avg MW added | 2016–19 trend | Beyond trend |
|---|---|---|---|---|
| DOM | **+23.6%** [22.9, 24.3] | +3,210 | +1.5 %/yr | +18.3% |
| ERCO | **+19.5%** [18.8, 20.1] | +9,440 | +2.2 %/yr | +11.7% |
| AZPS+SRP (to 2025-08) | **+13.7%** [13.1, 14.3] | +1,010 | −0.2 %/yr | +14.3% |
| BPAT | +13.2% [12.6, 13.8] | +835 | +0.5 %/yr | +11.5% |
| SWPP | +10.9% | +3,540 | +1.1 %/yr | +6.8% |
| GCPD | +10.6% | +68 | +3.4 %/yr | −0.7% (already growing) |
| PJM | +6.4% [6.1, 6.7] | +6,000 | −0.1 %/yr | +6.6% |
| MISO | +4.1% | +3,070 | 0.0 %/yr | +4.1% |
| NYIS / ISNE | −0.8% / +0.2% | | −0.6 / −1.7 %/yr | |

**Comparison:**
- The median data-center-heavy region (DOM, ERCO, AZPS+SRP, NEVP, GCPD, BPAT) grew **13.4%**, against **3.3%** for the comparison regions (D10 set).
- Beyond their own trends, the figures are 11.6% and 2.6%.
- GBM and degree-day OLS agree with the GAM within about 1.5 points for every headline BA.
- This is the first half of the brief's "wrong if" test, and it is passed. The 24/7-floor half is Prompt 10.

**Caveats:**
- *Beyond trend* extends a straight line for 7+ years. For BAs that were shrinking in 2016–2019 (CISO −2.0 %/yr, ISNE −1.7, LGEE −1.3), this predicts steep further declines and so inflates "beyond trend". Read it as an upper bound there.
- **BPAT:** a smooth rise from late 2021 with no step, consistent with data-center growth in its service area (Oregon's Morrow and Umatilla counties). This is unverified, so check it in Prompt 14.
- **ERCO** includes crypto mining and oil-and-gas electrification. Prompt 10's load shape and Prompt 15 must separate these.
- The last month (2026-10) is partial: 511 of 744 hours.

Outputs (`data/outputs/`):
- `counterfactual_hourly.parquet`: actual and the GAM/GBM/DDR counterfactuals per BA-hour;
- `structural_growth_monthly.parquet`: per month, with the trailing 12-month index and band;
- `structural_growth_annual.csv`, `growth_ranking.csv`.

Headline numbers are pinned in `tests/test_pinned_estimates.py` (±0.5 pp). Hero chart v1 (Prompt 9) was replaced by the themed `report/figures/hero.png` (Prompt 16).

## Load-shape fingerprint (Prompt 10)

`models/load_shape.py` asks *where in the day* the weather-adjusted growth sits. It works from the Prompt 9 counterfactual and refits nothing. A data center draws about the same power at 3 am as at 3 pm, so data-center load should add as many MW at night as in the afternoon. Growth from air-conditioning, offices or daytime industry shows up mostly in the afternoon and at peaks.

**Definitions** (local time, by the hour's start):
- **Night** = 1–5 am; **afternoon** = 2–7 pm.
- **Peak hours** = the 100 hours of each period with the highest *predicted* load. They are chosen by prediction, not by actual load, so the selection doesn't depend on the residual being measured.
- **Weather-adjusted floor** = the night-hours ratio Σactual/Σpredicted × the 2016–2019 mean night load.
- **Decomposition** of the hourly residual (actual − predicted, MW) per period, by OLS: `resid = a + Σ b_h·hour_h + c·CDH65 + d·HDH65`, with the hour effects summing to zero.
  - *flat 24/7* = a + mean(b_h over night hours): the residual at night at 65 °F;
  - *weather-sensitive* = c·mean(CDH65) + d·mean(HDH65);
  - *daytime shape* = the rest.
  - The three parts add up to the mean residual.
- Growth since 2022 is the change in each part between the start year and the latest 12 months, using the Prompt 9 windows (FPL/SOCO from 2023; AZPS and AZPS+SRP to 2025-08).
- 95% intervals come from a 14-day block bootstrap of days. The GBM residuals give the model-family check (`flat_share_gbm`).
- **Verdict:**
  - "24/7 floor" if night MW added ≥ 0.8 × afternoon MW added (or the afternoon fell);
  - "daytime/peak" if it is < 0.5 ×;
  - "mixed" in between;
  - "no growth" if the total's interval includes zero.

**Results** (change from 2022 to the 12 months ending 2026-10):

| Series | Growth | Night MW | Afternoon MW | Night ÷ afternoon [95%] | Flat 24/7 share | Weather-sensitive MW | Verdict |
|---|---|---|---|---|---|---|---|
| DOM | +23.6% | +3,236 | +3,227 | 1.00 [0.97, 1.04] | 103% (GBM 100%) | +55 | 24/7 floor |
| ERCO | +19.5% | +9,127 | +10,111 | 0.90 [0.87, 0.94] | 90% | +1,309 | 24/7 floor |
| AZPS+SRP | +13.7% | +1,232 | +903 | 1.36 [1.22, 1.62] | 131% | +29 | 24/7 floor |
| BPAT | +13.2% | +843 | +889 | 0.95 [0.88, 1.02] | 116% | −107 | 24/7 floor |
| GCPD | +10.6% | +62 | +75 | 0.83 [0.74, 0.93] | 88% | −2 | 24/7 floor |
| NEVP | +8.8% | +364 | +335 | 1.09 [0.96, 1.40] | 90% | +53 | 24/7 floor |
| PJM | +6.4% | +6,557 | +5,736 | 1.14 [1.04, 1.23] | 110% | +104 | 24/7 floor |
| SOCO (from 2023) | +4.6% | +603 | +1,584 | 0.38 [0.29, 0.50] | 17% | +598 | daytime/peak |
| CISO | +5.3% | +263 | +1,508 | 0.17 [−0.01, 0.35] | 49% | −198 | daytime/peak |
| CPLE | +2.1% | +188 | +156 | 1.21 [0.78, 2.02] | 31% | +165 | 24/7 floor |
| FPC | +2.9% | +240 | +207 | 1.16 [0.80, 1.99] | 77% | +153 | 24/7 floor |
| NYIS / ISNE | −0.8% / +0.2% | +348 / +507 | −489 / −245 | | | | no growth |

A flat share above 100% means the daytime-shape part is negative: the afternoon gained less than the night (Phoenix: rooftop solar).

**The "wrong if" test, second half: passed.**
- All six data-center regions are "24/7 floor". At night, the weather-adjusted load of the median data-center region grew **18.0%** since 2022 (afternoon 12.3%, peak hours 8.6%). The comparison regions' median grew 4.5% at night (afternoon 2.8%, peak 3.1%).
- In MW, Dominion's growth is the same at every hour: +3.24 GW at night, +3.23 GW in the afternoon, +3.17 GW in its peak hours. Its weather-sensitive part is only +55 MW.
- Dominion's weather-adjusted floor ratio (night load ÷ mean load) rose from 0.846 in 2019 to 0.901 in 2026. CPLE's went from 0.823 to 0.837.

**Caveats:**
- **A flat shape is necessary, not sufficient.** Comparison regions that grew a little (CPLE, FPC, MISO) also grew flat. What sets the data-center regions apart is the *size* of the night-time growth (4× the comparison median), not its shape alone. Other loads with a 24/7 profile are consistent with the same pattern:
  - crypto mining (ERCO; it curtails at peaks, which fits ERCO's peak-hour growth of 15.0% against 22.4% at night);
  - oil-and-gas electrification (ERCO, SWPP);
  - industrial process load.
- **Rooftop solar lowers the daytime residual** wherever it grows (Phoenix, California, New England, New York), which pushes the night ÷ afternoon ratio up. The night-hours growth itself contains no solar, so the comparison of night growth is the robust test.
- **EV charging** lands mostly at night, but at today's EV numbers it is hundreds of MW per region at most, against 3.2 GW in Dominion. Prompt 15 discusses it.
- **ERCO** has a weather-sensitive part of +1.3 GW: more cooling load per degree, consistent with population growth on top of the flat load.
- **SOCO** (Georgia/Alabama, measured from 2023) grew mostly in daytime and weather-sensitive hours. Georgia Power's reported data-center growth doesn't (yet) show as a floor in EIA-930 SOCO; check this in Prompt 14.
- **GCPD** is the weakest floor case (night ÷ afternoon 0.74–0.93), and it was already growing before 2022.

**Comparison-zone pairings** (`data/outputs/comparison_pairs.csv`; similar climate, no large data-center build-out):

Each data-center region is reported **both** against its paired region and against the pooled comparison median: night growth 4.5%, total 3.3% (decided 2026-11-08). The pair is the stricter like-for-like test. The pooled median is the stable yardstick, and no single region can swing it. Both columns are in `comparison_pairs.csv`. Against PACE, BPAT's night growth is about 2× (15.4% vs 7.0%) and GCPD's is only 1.5× (10.2% vs 7.0%). Against the pooled median, they are about 3× and 2.3×.

| Data-center region | Comparison | Why |
|---|---|---|
| DOM | CPLE | Neighbouring Carolinas utility; same humid-subtropical climate and mixed heating/cooling load; no hyperscale cluster on Loudoun's scale |
| ERCO | FPC | Hot, humid, cooling-dominated, fast-growing Sun Belt population: controls for migration-driven growth |
| AZPS+SRP | CISO | Hot-summer Southwest neighbour with strong rooftop-solar growth, which tests whether falling midday load alone makes a "floor" signal (it doesn't: CISO's night load grew only 1.2%) |
| NEVP | CISO | Desert/hot-summer West neighbour; same rooftop-solar caveat |
| BPAT | PACE | Interior West, winter-heating climate, PacifiCorp territory neighbouring BPA; small data-center presence (PACE does have some in Utah, so it is a conservative comparison) |
| GCPD | PACE | Interior West, cold-winter climate; no Quincy-scale cluster |
| PJM | MISO | Neighbouring multi-state ISO with a similar climate range and industrial mix |

**Outputs** (`data/outputs/`):
- `load_shape_metrics.csv`: per BA × year, actual mean/peak/load factor/daily-minimum ratio, weather-adjusted night/afternoon/mean load, band residuals and the decomposition;
- `growth_decomposition.csv`: the since-2022 decomposition with intervals and verdicts;
- `load_shape_monthly.parquet`: trailing 12-month index per band;
- `comparison_pairs.csv`.

**Charts:**
- `report/figures/chart2_rising_floor.png`: night vs afternoon index (legend for the two time bands) for DOM/CPLE, ERCO/FPC and AZPS+SRP/CISO (Phoenix to 2025-08). The Phoenix row shows the rooftop-solar test: CISO's afternoon index fell in 2022–2024 while its night index barely rose; Phoenix's night index climbed;
- `report/figures/chart3_dom_heat_calendar.png`: week × hour residual MW for DOM and CPLE.

Notebook: `notebooks/05_load_shape.ipynb`. Pinned: `tests/test_load_shape.py`.

## Generation additions: EIA-860M (Prompt 11)

**Source:** EIA-860M, *Preliminary Monthly Electric Generator Inventory*, September 2026 release (published 2026-10-23), from `https://www.eia.gov/electricity/data/eia860m/`. `ingest/eia860m.py` finds the newest release by trying month files backwards; EIA posts placeholder links for future months that return 503. One release is enough to rebuild history:
- the Operating sheet gives each unit's first month of operation;
- the Retired sheet gives each retired unit's operating and retirement months.

The ingest reads the CONUS sheets only, which hold 35,805 generators, 7,321 of them retired. A few old retired units have unknown operating months (coded 0, 88 or 99); these are set to January, which doesn't matter after 2016. Net summer capacity is used throughout, with nameplate as a fallback where it is missing. **Not captured:** uprates, derates and repowers of existing units, and units under 1 MW (rooftop solar).

**Capacity factors** (`data/reference/capacity_factors.csv`) come from EIA *Electric Power Monthly* Tables 6.07.A and 6.07.B: national annual capacity factor by technology, mean of 2023–2025.

| Technology | Capacity factor |
|---|---|
| Solar PV | 0.235 |
| Wind | 0.338 |
| Gas combined cycle | 0.596 |
| Gas combustion turbine | 0.135 |
| Nuclear | 0.916 |
| Coal | 0.446 |
| Hydro | 0.347 |

Batteries and pumped storage count as **zero energy**: they shift energy rather than produce it. Their MW are reported separately.

**Assigning plants to series:** each plant goes to the series named by its 860M balancing-authority code, which is its *current* BA. Combined series sum their parts. **DOM** is a PJM zone, not a BA in 860M. Its plants are the PJM plants in the 134 counties where Dominion serves any customers (`ba_county_map.csv`), about 31 GW operating. PJM's DOM zone is Dominion's *transmission* zone and includes co-op territory inside it, so a rule requiring Dominion to serve most of a county's customers would miss plants such as Bear Garden. The DOM set leaves out:
- Dominion's Mt Storm plant in West Virginia;
- plants in the AEP and FirstEnergy zones.

**Since-2022 comparison (`supply_gap.py`):** each unit counts in proportion to the months it ran in each window, which mirrors Prompt 9's demand measure (start year vs latest 12 months):

new supply = Σ MW × CF × 8,766 h × (share of the latest 12 months online − share of the start year online)

860M ends in September 2026, so units are assumed unchanged in October.

**Results** (TWh per year, 2022 to the 12 months ending 2026-10):

| Series | Demand growth | New local supply | Supply ÷ demand | Mainly |
|---|---|---|---|---|
| DOM | +28.1 | **+1.4** | 0.05 | +6.2 solar, −3.9 coal, −1.3 gas |
| ERCO | +82.8 | +65.1 | 0.79 | +45.2 solar, +19.6 wind; +14.7 GW batteries |
| PJM (all) | +52.6 | +17.6 | 0.34 | +24.0 solar, +17.5 gas, −26.5 coal |
| SWPP | +31.0 | +16.6 | 0.54 | +17.0 wind |
| Phoenix (AZPS+SRP, to 2025-08) | +8.9 | +5.2 | 0.58 | +3.9 solar; +2.4 GW batteries |
| BPAT | +7.3 | +1.0 | 0.13 | |
| GCPD | +0.6 | 0.0 | — | |
| MISO | +26.9 | +25.6 | 0.95 | +39.0 solar, −26.9 coal |
| CISO | +12.0 | +21.6 | 1.80 | +17.5 solar; +12.6 GW batteries |
| ISNE | +0.2 | −4.4 | | Mystic gas retirement |

**Reading it:**
- Dominion's weather-adjusted demand grew by about a quarter (+28 TWh/yr), while the zone's own generation fleet added almost no expected energy. Solar additions roughly offset the coal (Chesterfield) and gas retirements.
- The difference is met by **imports from the rest of PJM** and by running existing plants harder. The data shows the gap but can't say which of the two fills it.
- Coastal Virginia Offshore Wind's 2.6 GW commercial project is listed in 860M as *planned* for January 2027, so it falls outside this window.
- The Pacific Northwest regions (BPAT, GCPD) also grew with little new local supply. Their large hydro systems can't simply be expanded.

**Caveats:**
- This is descriptive, not a reliability or adequacy assessment.
- Capacity factors are national: Arizona and Texas solar runs above 0.235, Northwest solar below it. Gas turbines' low factor understates their value at peak.
- Grids trade power, so local supply is not the only supply.
- A plant's BA is its current one; Gulf Power is FPL, consistent with the FPL/SOCO windows.

**Outputs:**
- `data/processed/capacity_changes.parquet`: generator add/retire events per series;
- `data/outputs/supply_vs_demand.csv`: series × year, MW added and retired by group, expected TWh of net additions, change in weather-adjusted demand;
- `data/outputs/supply_vs_demand_since2022.csv`;
- Chart 4: `report/figures/chart4_supply_gap.png`;
- notebook `notebooks/06_supply_gap.ipynb`.

## Interconnection queues: LBNL Queued Up (Prompt 12)

**Source:** Lawrence Berkeley National Laboratory and GridTracker, *Queued Up: 2026 Edition*, data file `lbnl_ix_queue_data_file_thru2025.xlsx` (updated May 2026; queues as of 2025-12-31). It is licensed **CC BY 4.0**, with attribution "Lawrence Berkeley National Laboratory and GridTracker". The file has 38,201 interconnection requests from 7 ISO/RTOs and about 50 non-ISO balancing areas. Each request has a status (active, withdrawn, suspended or operational), its request, agreement, withdrawal and online dates, county, transmission provider, resource type and MW.

The LBNL web page (emp.lbl.gov/queues) returns 403 to scripted downloads. The file itself is downloaded from `eta-publications.lbl.gov`.

**Processing** (`ingest/queues.py` → `data/processed/queues.parquet`):
- MW = mw_1 + mw_2 + mw_3, so hybrids count all their components. LBNL's imputed storage MW for hybrids is not in the public file.
- Types are grouped into Solar, Wind (including offshore), Storage (batteries and other storage), Hybrid (any combination), Gas, Nuclear and Other.
- Requests are mapped to our series:
  - each ISO → its series (ERCOT → ERCO, SPP → SWPP, ...);
  - APS → AZPS; SRP and its sub-entities → SRP; NVE → NEVP; BPA → BPAT; GrantPUD → GCPD;
  - Duke split by utility: Carolinas → DUK, Progress → CPLE (East and West together), Florida → FPC;
  - PacifiCorp → PACE for Utah, Wyoming and Idaho only.
- ⚠ The LBNL entity "Dominion" is **Dominion Energy South Carolina**, not the Virginia DOM zone. DOM = PJM requests in the counties Dominion serves, as in Prompt 11.
- ⚠ APS reports no fuel type for 53 active requests (15.8 GW), which therefore count as "Other".

**Summaries** (`models/queue_summary.py`):
- **Active backlog:** requests still active at the end of 2025, with suspended requests excluded. Shown by region × technology and by queue-entry year.
- **Completion:** of requests that entered in 2000–2020, the share that reached operation, by count and by MW. 2000–2020 is LBNL's convention for cohorts old enough to have resolved.
- **Timing:** median years from request to operation, and from request to a signed interconnection agreement, for projects online in 2018–2025.

| Region | Active GW | Main types | Completion, 2000–20 cohort (requests / MW) | Median years to operation |
|---|---|---|---|---|
| West (non-ISO) | 449 | Hybrid 238, storage 72 | 17% / 9% | 3.2 |
| ERCOT | 408 | Storage 121, hybrid 116, solar 83, gas 44 | **30% / 24%** | 3.8 |
| MISO | 350 | Solar 133, gas 65 | 18% / 14% | 4.2 |
| CAISO | 191 | Hybrid 131, storage 49 | 12% / 8% | **7.8** |
| SPP | 151 | Solar 37, gas 33, storage 31 | 17% / 14% | 4.8 |
| Southeast (non-ISO) | 144 | **Gas 63**, solar 44 | 16% / 11% | 4.0 |
| PJM | 131 | Solar 54, storage 29 | 21% / 13% | 4.9 |
| — of which DOM zone | 29 | Solar 13, storage 9, gas 3, wind 3 | 22% / 16% | 4.7 |
| NYISO / ISO-NE | 27 / 15 | Storage, wind | 19% / 12%, 27% / 18% | 5.0 / – |

In total, **1.87 TW is active**. Of requests that entered in 2000–2020, **19% reached operation (13% of MW)**.

**Reading it:**
- Queue MW is a ceiling, not a forecast.
- **Queue pipeline vs demand growth** (`data/outputs/queue_pipeline.csv`; kept as a table, not on chart 4, by decision on 2026-11-08). This is a rough, descriptive comparison, *not a forecast*. Columns:
  - *Queue energy* = active MW of each component × capacity factor × 8,766 h. Capacity factors are as in Prompt 11, with gas at the combined-cycle 0.596 (an upper bound), storage at zero, and types LBNL doesn't specify left out.
  - *At historical completion* = queue energy × the series' 2000–2020 MW completion rate.

| Series | Active GW | Queue energy, TWh/yr | Historical MW completion | At historical completion, TWh/yr | Demand growth since 2022, TWh/yr | New local supply since 2022, TWh/yr |
|---|---|---|---|---|---|---|
| DOM | 28.9 | 51.6 | 16% | **8.2** | 28.1 | 1.4 |
| ERCO | 408 | 677 | 24% | 165 | 82.8 | 65.1 |
| PJM | 131 | 276 | 13% | 37 | 52.6 | 17.6 |
| AZPS / SRP | 48 / 15 | 60 / 19 | 7% / 38% | 4.4 / 7.2 | 8.9 combined | 5.2 combined |
| BPAT | 130 | 162 | 7% | 12 | 7.3 | 1.0 |
| NEVP | 35 | 91 | 10% | 9.2 | 3.5 | 5.0 |
| GCPD | 1.2 | 2.6 | 0% | 0 | 0.6 | 0.0 |

  Even if Dominion's queue completes at its past rate, it covers under a third of the growth already seen since 2022. APS's 15.8 GW of unspecified type is left out of its energy.
- The Southeast's backlog is the most gas-heavy (63 GW). This is consistent with utilities there planning gas for load growth.

**Gaps by entry year are real, not missing data:**
- PJM accepted almost no new requests while it moved to its reformed cluster process (2022–2025).
- CAISO and MISO take requests in cluster windows.

**Outputs** (`data/outputs/`):
- `queue_active.csv`: region and series × technology, GW;
- `queue_active_by_year.csv`;
- `queue_completion.csv`: overall and by technology;
- `queue_timing.csv`;
- `queue_pipeline.csv`;
- Chart 5: `report/figures/chart5_queue_backlog.png`;
- notebook `notebooks/07_queues.ipynb`.

## Price context (Prompt 13)

**Descriptive only.** Prices also move with gas prices, weather, retirements, transmission and market rules. Nothing here estimates what data centers did to prices; the report may say price changes are *consistent with* tighter supply and demand.

**PJM capacity auction** (`data/reference/pjm_bra_prices.csv`, chart 6). Base Residual Auction clearing prices in $/MW-day UCAP:
- The RTO prices for 2018/19 to 2028/29 come from Table 2 of PJM's 2028/29 BRA report. DOM prices and the uncapped simulations come from the 2025/26 to 2028/29 reports.
- The PDFs are cached in `data/raw/pjm_rpm/`, and `tests/test_prices.py` re-checks every value against them (needs `pdftotext`).

| Delivery year | RTO | DOM | Notes |
|---|---|---|---|
| 2018/19 to 2021/22 | $165, $100, $77, $140 | n/a | |
| 2022/23 to 2024/25 | $50, $34, $29 | n/a | Three years under $55 |
| 2025/26 | **$269.92** | **$444.26** | DOM is a constrained zone in its first auction |
| 2026/27 | $329.17 | $329.17 | At the cap; $388.57 without it |
| 2027/28 | $333.44 | $333.44 | At the cap; $529.80 without it (DOM $542.83); 6,517 MW UCAP below the Reliability Requirement |
| 2028/29 | $325.00 | $325.00 | At the cap; $554.72 without it; 6,831 MW UCAP short |

- ⚠ **DOM before 2025/26:** the DOM zone joined the auction (RPM) in 2025/26. Earlier, Dominion met its capacity obligation under the **Fixed Resource Requirement** alternative, so earlier auction prices did not apply to Dominion's load. Chart 6 shows DOM only from 2025/26.
- **Cap and floor:** a temporary cap and floor (about $325–333 and $175–180/MW-day UCAP) applies from 2026/27.
- **PJM's own wording:** in the 2027/28 report, PJM attributes the 5,250 MW rise in forecast load "largely" to "additional Large Loads".

**PJM zonal energy prices:** real-time load-weighted average LMP, 2016–2025. The source is the PJM Independent Market Monitor's (Monitoring Analytics) *State of the Market* reports, volume 2 (the 2017, 2019, 2021, 2023 and 2025 editions; each covers two years). Two table layouts are parsed: the zonal LMP-components table and the older section-3 table.

| $/MWh | 2016 | 2019 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---|---|---|---|---|---|---|
| DOM | 32.2 | 27.7 | 44.7 | 99.5 | 37.5 | 39.1 | **65.6** |
| PJM | 29.3 | 27.3 | 39.8 | 80.1 | 31.1 | 33.7 | 50.7 |

- DOM's premium over PJM grew from about $0.4/MWh (2019) to **$14.85/MWh (29%) in 2025**, the highest of any zone.
- Almost all of the premium is congestion: DOM's congestion component was $14.04/MWh in 2025, against $4.92 in 2024. This is consistent with a zone importing more power over constrained lines (see Prompt 11's supply gap).

**Other wholesale prices** (annual means, `data/processed/wholesale_prices.parquet`):
- **ERCOT:** real-time settlement point prices for hubs and load zones, via gridstatus (ERCOT's public historical RTM price reports). HB_HUBAVG was $21.6 (2016), $148.2 (2021, Winter Storm Uri), $62.3 (2022), $26.8 (2024) and $32.5 (2025).
- **Palo Verde, Mid-C and PJM West:** day-ahead on-peak bilateral prices from EIA's ICE data. Palo Verde was $35.2 in 2025 and Mid-C $46.2; Mid-C stayed high in 2023–24 because of low Northwest hydro.
- The 2022 spike in every market follows gas prices.

**Retail prices** (`data/processed/retail_prices.parquet`, `report/figures/retail_prices.png`):
- **Source:** the EIA API v2 `electricity/retail-sales` dataset (EIA-861 annual plus EIA-861M monthly), price = revenue ÷ sales.
- **Coverage:** annual 2010–2025 plus the 12 months to 2026-08, for the U.S. and VA, TX, GA, AZ, OR, WA and NV.
- **Inflation adjustment:** prices are converted to 2025 dollars with CPI-U (CPIAUCSL, FRED).

| 2025 ¢/kWh | 2021 all sectors | Latest 12 months, all sectors | 2021 residential | Latest 12 months, residential |
|---|---|---|---|---|
| Virginia | 10.9 | **12.4 (+14%)** | 14.2 | 16.2 (+14%) |
| Texas | 10.9 | 10.1 (−7%) | 14.4 | 15.6 (+8%) |
| Georgia | 12.4 | 12.1 (−2%) | 14.9 | 14.8 (−1%) |
| Arizona | 12.7 | 12.5 (−2%) | 14.9 | 15.0 (+1%) |
| U.S. | 13.2 | 13.8 (+5%) | 16.2 | 17.6 (+9%) |

- Virginia's inflation-adjusted retail prices rose faster than the U.S. average since 2021, although they remain below it.
- Texas, Georgia and Arizona all-sector prices fell slightly in real terms.
- The chart keeps Texas as the brief specifies (decided 2026-11-08): it contrasts fast demand growth with falling real prices, which guards against reading this as "demand up → prices up". Oregon and Washington (the Bonneville and Grant County regions) are in the notebook table only. Their all-sector real prices went from 10.6¢ to 11.2¢ (OR, +5%) and from 10.4¢ to 11.5¢ (WA, +11%), from 2021 to the latest 12 months. Statewide averages dilute a few rural data-center counties, and hydro conditions dominate prices there.
- This is descriptive: retail rates are set by regulators, with lags, riders and fuel pass-through.

**Gaps:**
- Wholesale prices are complete calendar years only (2016–2025). 2026 PJM zonal LMPs are not yet published annually.
- Mid-C and Palo Verde are on-peak bilateral prices, not load-weighted LMPs.

**Outputs:**
- `data/processed/pjm_capacity_prices.parquet`, `wholesale_prices.parquet`, `retail_prices.parquet`;
- Chart 6: `report/figures/chart6_pjm_capacity.png`; notebook `notebooks/08_prices.ipynb`.

## Data-center context: announcements and utility disclosures (Prompt 14)

Hand-curated and cited. These tables are **context, not ground truth**: announcements are plans, and contract and pipeline figures are capacity, not load. Primary documents are cached in `data/raw/disclosures/`. `tests/test_context_tables.py` checks that every row has a source URL, a confidence level and one of our series codes.

**`data/reference/utility_reported_load.csv`** has 23 rows. Each row's `metric_type` is one of measured, contracted, pipeline, forecast, requests, approved or policy. Only *measured* load (`comparable = yes`) can be set against our weather-adjusted estimates in Prompt 15.

| Entity | Measured load | Contracts / pipeline (not load) |
|---|---|---|
| **Dominion** | Data-center coincident peak **4.0 GW (2025)**; billing peak **4.8 GW (Aug 2026)**; 55 companies, ~500 metered accounts | 47 GW of contract capacity (Jul 2025) → ~54 GW (Sep 2026), of which 12.0 GW ESA and 9.4 GW CLOA are firm. Dominion forecasts 16.6 GW of data-center demand by 2046 |
| **ERCOT** | Approved large loads (all types): 9.0 GW approved to energize, **3.9 GW observed non-simultaneous peak** (TAC report, March 2026) | Large-load requests: 410 GW (March 2026, ~87% data centers) → 474 GW (June 2026, ~90%). Energization of data centers and crypto paused from August 2026 (6.6 GW waiting) |
| **Georgia Power / Southern** | Data-center *sales* +55% year on year (Q2 2026); no MW given | Large-load pipeline 73.1 GW (March 2026); 17 GW contracted through the mid-2030s; 8.4 GW of load growth forecast for 2025/26–2030/31 |
| **APS / SRP** | APS 2025 peak 8.7 GW, data centers ~5% of it; about 200 MW of APS's 2026 peak growth came from data centers; data centers were 7% of SRP's 2026 peak | APS has 4.5 GW committed in its IRP and a 19 GW uncommitted queue; it has made no new data-center commitments since January 2024 |
| **Grant County PUD** | **280 aMW from 8 data-center operators (2025), 37% of system load** | ~800 MW of large-load requests |

**`data/reference/datacenter_announcements.csv`** has 13 rows. They are major campuses in the focus regions, each with date, county, series, announced MW and what the MW measures (campus, IT, or co-located generation), status, source and confidence:
- **Georgia:** OpenAI Effingham County, 3.2 GW, power 2028–2032; Microsoft Fairwater Atlanta, about 636 MW IT, operating.
- **Virginia:** EdgeCore Louisa (1.1 GW) and Culpeper.
- **Texas:** Stargate Abilene (1.2 GW), Vantage Frontier (1.4 GW), Microsoft Pecos (up to 2 GW, behind-the-meter gas), Google/Intersect Haskell County, and others.
- **Phoenix:** EdgeCore Mesa (450+ MW) and Vantage Goodyear.

Rows with low confidence come from aggregator trackers or local approvals; one site's BA is unverified.

**`data/reference/hero_annotations.csv`** has 7 labels for the hero chart, each pointing to a cited row in `utility_reported_load.csv`.

**Implications for Prompt 15:**
- **⚠ ERCOT:** by ERCOT's own figures, *all* approved large loads (data centers, crypto and industrial) peaked at about 3.9 GW, non-simultaneous. Our weather-adjusted ERCOT growth since 2022 averages +9.4 GW. So data centers can be at most a minority of Texas's growth. The rest is consistent with population growth (ERCOT's weather-sensitive part is +1.3 GW, Prompt 10) and oil-and-gas electrification. The report must not describe ERCOT's growth as mainly data centers.
- **Dominion:** the measured data-center peak grew to 4.0 GW (2025) and 4.8 GW (Aug 2026). That is the same order as our weather-adjusted DOM growth (+3.2 GW since 2022; ~4.9 GW above the 2018–19 baseline in 2026). Prompt 15 needs Dominion's data-center load for 2022 (shown only as a chart image in its PJM presentation) for a like-for-like comparison.
- **Grant County:** 280 aMW of data-center load (2025) against our GCPD growth of +68 MW since 2022. Most of Grant County's data-center load predates 2022, consistent with its pre-2022 residual trend of +3.4 %/yr (D11).
- **⚠ Bonneville:** there is still **no utility disclosure for BPAT** (Bonneville wholesale customers such as Umatilla Electric Cooperative). The Prompt 9 lead (+13% smooth growth) stays unverified.
- **Southern:** reports data-center *sales* +55%, yet SOCO's weather-adjusted growth since 2023 is daytime- and weather-heavy, not floor-shaped (Prompt 10). This is unresolved: the MW may be small relative to SOCO, or the load sits in the hours we class as daytime.

## Robustness (Prompt 15)

`models/robustness.py` re-estimates growth since 2022 over the same windows under seven specifications. The output is `data/outputs/robustness_grid.csv`, with medians in `robustness_medians.csv`. The forest plot is `report/figures/robustness_forest.png` and the notebook is `notebooks/09_robustness.ipynb` (CLAUDE.md calls it 05_robustness, but 05 was taken).

**Specifications:**
- the headline GAM on 2016–19;
- LightGBM and degree-day OLS (Prompts 7–8);
- the GAM on 2017–19;
- the GAM fitted on the start year itself (2022, or 2023 for FPL/SOCO);
- the GAM plus a linear term in state employment (`gam_emp`);
- Phoenix with ERA5 temperature raised 0.3 °C from 2023 (see "Phoenix drift").

| Growth since 2022, % | Headline | Range over all specs | With employment control |
|---|---|---|---|
| DOM | 23.6 | 17.7–23.9 | 17.7 |
| ERCO | 19.5 | **10.9–20.1** | **10.9** |
| AZPS+SRP (to 2025-08) | 13.7 | 12.3–14.2 (+0.3 °C: 13.0) | 14.2 |
| BPAT | 13.2 | 12.2–13.2 | 12.2 |
| NEVP | 8.8 | 5.2–8.9 | 5.2 |
| GCPD | 10.6 | 4.0–10.6 | 4.0 |
| PJM | 6.4 | 6.4–6.8 | 6.7 |
| Comparison regions | −0.8 to 7.2 | −1.0 to 9.0 | |

**The core comparison** (median growth of the data-center regions vs the comparison regions):

| Grouping | Gap, % points (ratio) across the 7 specs |
|---|---|
| Six data-center regions vs the D10 comparison set (the headline grouping, from Prompt 9) | **8.4–10.3 (3.6–4.5×)** |
| The same, without boundary-change BAs (AZPS+SRP, FPL) | 8.4–10.3 |
| **Pre-registered roles** (all 10 Prompt 2 "focus" series vs all 9 "comparison" series, set before any result) | 4.5–6.0 (2.2–2.6×); **2.0 (1.6×) with the employment control** |

The six-region grouping leaves out four Prompt 2 focus regions that grew slowly (SOCO, DUK, TVA, PJM). The pre-registered grouping guards against choosing the "data-center regions" after seeing the results. Under it, the effect is smaller but still positive under every specification.

**Supply gap with regional capacity factors:**
- State solar and wind factors = EIA-923 state generation (EIA API) ÷ time-weighted 860M capacity, 2023–25 (`regional_capacity_factors.csv`).
- Examples: AZ solar 0.28, NV solar 0.30, TX wind 0.34, OR wind 0.26.
- Result (`supply_vs_demand_since2022_regional_cf.csv`): DOM gap −23.3% → −24.1%; ERCO −4.1% → −2.7%; Phoenix −5.7% → −5.1%. No region changes sign.

**Our estimates vs utility disclosures (measured load only):**

| Series | Utility figure | Our weather-adjusted figure | Ratio |
|---|---|---|---|
| DOM | Dominion's metered data-center coincident demand, 2022→2025: +1,373 MW | Residual 2022→2025: +2,301 MW | 0.60 |
| DOM | Dominion data-center billing demand, 2019→2025: +2,816 MW | Residual vs 2018–19 baseline, 2025: +3,880 MW | 0.73 |
| ERCO | All approved large loads, observed peak (level): 3,883 MW | Growth since 2022: +9,440 MW | ≤ 0.41 |
| AZPS+SRP | APS: ~200 MW of its 2026 peak growth from data centers | +1,013 MW | partial |
| GCPD | Data-center load 2025 (level, 280 aMW) | Residual vs baseline 2025: +190 MW | levels, not comparable |

- **Dominion:** Dominion's own data centers explain about 60–73% of our DOM zone growth. The remainder is consistent with data centers outside Dominion's service territory but inside PJM's DOM zone (NOVEC, Rappahannock and other co-ops; the EdgeCore Louisa campus is REC-served). Our DOM residual tracks Dominion's data-center series year by year (2019 +30 MW; 2022 +1.6 GW; 2025 +3.9 GW).
- Dominion's 2016–19 data-center growth (+668 MW billing) also is consistent with DOM's pre-2022 residual trend (D11).

**Alternative growth drivers, and what the load shape says:**
- **Economic and population growth:**
  - The employment control halves ERCO (19.5% → 10.9%) and lowers DOM to 17.7%. Employment also rises *because of* data-center construction, so this is a conservative lower bound, not a clean decomposition.
  - In ERCOT, the weather-sensitive part of growth (+1.3 GW, Prompt 10) and ERCOT's own 3.9 GW of observed large loads both say population and other loads explain most of the growth.
- **Crypto mining (ERCOT):** ERCOT forecasts about 2.5 GW of crypto demand (secondary source). Crypto curtails at peaks, but ERCO's peak-hour MW growth (+11.6 GW) exceeds its night growth (+9.1 GW), so curtailment doesn't dominate the aggregate.
- **EVs:** EV charging lands at night, so it does add to the floor. But even 100,000 EVs at ~3.5 MWh/yr each average only ~40 MW, which is small next to DOM's +3.2 GW.
- **Heat pumps and building electrification:** these would show up as weather-sensitive (heating) growth. It is negligible in DOM (+55 MW) and negative in BPAT (−107 MW).
- **Manufacturing:** semiconductor fabs are also flat 24/7 loads. **Phoenix's floor is consistent with both data centers and fabs** (TSMC in north Phoenix, Intel in Chandler); the load shape can't tell them apart. Weekday-daytime manufacturing would show up as daytime shape, and there is none in DOM (−152 MW).

**Claims that survive every specification:**
1. Dominion's weather-adjusted demand grew the most of any region since 2022 (17.7–23.9%). Its growth is a flat 24/7 addition of about 3.2 GW, and about 60–73% of it is Dominion's own metered data-center load.
2. The six data-center regions outgrew the comparison regions by 8–10 points. Under pre-registered roles the gap is 2–6 points, still positive in every specification.
3. Dominion's local new generation covers about 5% or less of its demand growth, with national or regional capacity factors.
4. Phoenix grew 12–14% (to 2025-08), robust to the ERA5 warming check.

**Claims that don't survive as stated:**
1. "ERCOT grew ~19.5% because of data centers." The range is 11–20% across specifications, and ERCOT's own large-load data caps all large loads (data centers, crypto and industrial) at ≤41% of the growth. Say instead: ERCOT grew strongly, and data centers are one contributor among several.
2. A precise "data-center regions grow 4× faster" multiple. It depends on the grouping (1.6–4.5×), so report a range.
3. Any causal attribution for BPAT (no utility disclosure), GCPD (4–11%, most of its data-center load predates 2022) or NEVP (5–9%).

### Validation report (validate-data checklist, 2026-11-08)

**Overall assessment: share with the caveats below.**

| Check | Result |
|---|---|
| Independent recomputation of DOM growth from hourly data (numpy, not the pipeline) | +23.63%, +3,208 MW: **matches** |
| Duplicate hours in the counterfactual | 0 |
| Partial last month (2026-10, 511 h) | Ending at 2026-09 instead: 23.1% (−0.5 pt), noted |
| Raw vs weather-adjusted growth (DOM) | 26.5% raw vs 23.6% adjusted: plausible (warmer recent weather) |
| Time zones | UTC throughout; local hour only for features (CLAUDE.md convention) |
| Denominators | Ratio of sums (no average of averages); window lengths identical across series |
| Selection bias | ⚠ Six-region group chosen in Prompt 9; pre-registered Prompt 2 roles give a smaller gap (above). **Report both** |
| Cross-reference with external sources | Dominion metered data-center series (consistent); ERCOT large loads (limits the ERCO attribution); FERC 714 reconciliation (Prompt 3) |
| Charts | Bar and step charts start at zero; index charts at 2019 = 100 are labelled; COVID shaded on time series |

**Issues found:**
1. **High:** ERCOT's attribution to data centers is too strong in hero chart v1's framing. Fix in Prompts 16–17 with the caveat and the 11–20% range (decision: ERCO stays in the group, with the caveat).
2. **Medium:** report the grouping sensitivity (1.6–4.5×) next to the headline multiple.
3. **Medium:** DOM's EIA-930 baseline is short (2018-09 to 2019). The private PJM check shows this affects the level relative to the baseline, not growth since 2022 (corrected in Prompt 19).
4. **Medium:** "beyond trend" is an upper bound for BAs that were shrinking in 2016–19 (CISO, ISNE).
5. **Low:** the last 12 months include a partial October (−0.5 pt); national vs regional capacity factors make no material difference.

**Required caveats for readers:**
- Say "consistent with data-center load", never "caused by".
- Name the other 24/7 loads: crypto (ERCOT) and fabs (Phoenix).
- DOM is PJM's zone, which is larger than Dominion's service territory.
- Phoenix ends 2025-08 (EIA-930 vs FERC dispute).
- The data cut-off is 2026-10.

## Final figures and theme (Prompt 16)

`make figures` (`python -m glg.viz.figures`) redraws every report chart from the processed outputs in about 10 seconds; no model is refitted. It writes each chart as PNG (200 dpi) and SVG, in a light and a dark variant (`<name>.png`, `<name>_dark.png`), to `report/figures/`, and the two social cards (1200 × 675 px) to `report/figures/social/`. Rerunning gives byte-identical files. The notebooks call the same functions, so rerunning a notebook can no longer overwrite a figure with a draft.

| File | Chart |
|---|---|
| `hero` | Weather-adjusted demand index, ten fastest-growing regions, with utility disclosures |
| `chart2_rising_floor` | Night vs afternoon index, three data-center regions and their pairs |
| `chart3_dom_heat_calendar` | Dominion vs Duke Progress East residual, week × hour |
| `chart4_supply_gap` | New local generation minus demand growth, by region |
| `chart5_queue_backlog` | Active queue by technology and entry year, per ISO/region |
| `chart6_pjm_capacity` | PJM capacity auction prices |
| `chart7_validation` | Held-out fit and baseline residuals (was a draft) |
| `chart8_dominion_metered` | **New:** our DOM estimate vs Dominion's metered data-center demand, change since 2022 |
| `retail_prices`, `robustness_forest` | Context charts |
| `social/hero_card`, `social/chart2_card` | Dominion only, for posts |

**Theme (`viz/theme.py`).**
- **Palette:** Okabe-Ito in a fixed slot order, plus gray for "Other" and reference lines. The same entity keeps its slot in both themes; text never takes a series colour.
- **Palette checks** (dataviz skill's `validate_palette.js`):
  - Light, on white: passes every check on all pairs. Amber and sky blue fall below 3:1 contrast, so they appear only with a legend.
  - Dark, on `#16181c`: hues stepped into the dark lightness band; passes on adjacent pairs, all six ≥ 3:1.
  - Two dark pairs are weak but never adjacent: amber/vermillion for deuteranopes, and sky/blue. So chart 5 keeps amber and vermillion apart in the stack and folds nuclear (≤ 3.7 GW in any region) into "Other".
- **Ink:** text tokens pass WCAG AA in both themes (tested).
- **Frame:** title, subtitle, legend row and source line sit at fixed distances from the edges.
- **Every figure has** a title that states the finding, units on its axes, a "Chart N · Source" line, and alt text in the SVG/PNG metadata.
- **COVID:** every time-series chart shades COVID or, in chart 3, marks it with a labelled line. `figures.build` refuses a time-series chart without it, and a test checks it.
- **Fonts:** Source Sans Pro, falling back to Lato, Helvetica Neue, then DejaVu Sans. A machine without the first font renders slightly wider text.

**Chart 8 caveats (shown on the chart).**
- The two sides are not the same territory: Dominion's service area is smaller than PJM's DOM zone, which co-ops also serve.
- They are not the same measure: Dominion reports peak MW (coincident or billing), we report annual average MW.
- 2026 is Jan–Oct for us and a forecast for Dominion; both are drawn with open markers.
- Dominion's metered data-center demand rose 1,373 MW from 2022 to 2025, about 60% of our +2.30 GW. The rest is consistent with other load in the zone. That is a statement of consistency, not a decomposition.

**Wording changed during review.**
- Chart 2: "add *nearly* as many MW at night", because ERCOT added 9.1 GW at night vs 10.1 GW in the afternoon.
- Chart 7: the residual is "each region's own pre-2022 trend" (ERCOT +2.2 %/yr), not "little".
- Chart 8: "rises in step … but explains part of it", not "tracks".
- Social cards: "Dominion's grid (Virginia)", because the DOM zone also covers northeast North Carolina.

## Report (Prompt 17)

`report/index.qmd` is a Quarto page rendered by `make report` to `report/_site/index.html` (Quarto 1.10, Jupyter kernel `tf`). It takes **no number from the prose**: every result is read from `data/outputs/` and `data/reference/` by `src/glg/report.py` (`numbers()` for inline values, `*_table()` for tables, `fig()` for figures). `tests/test_report.py` checks those values against the pins and fails if a headline number is typed into the `.qmd` by hand. Reference facts quoted from sources (ERCOT's 3.9 GW, PJM's auction shortfalls, retail price changes) are written in the text with their source in DATA.md.

- **Figures:** each chart is included in both variants; the page shows the one that matches the reader's light/dark setting (`respect-user-color-scheme`) and links to the full-size PNG so phone readers can zoom. Alt text is read from the SVG metadata written by `viz/figures.py`.
- **Freezing:** `execute: freeze: auto` writes `report/_freeze/`, which lets the page be re-rendered without the data (e.g. on GitHub Pages). `make report` deletes it first, so a local build always re-runs against current outputs.
- **Structure:** summary and hero; the question and its "wrong if" test; method in plain language; growth table; Dominion (charts 2, 3, 8); other regions (ERCOT, Phoenix, Pacific Northwest, Southern, PJM); supply (charts 4, 5); prices (chart 6, retail); robustness (forest plot, specification and grouping tables, utility comparison, alternative drivers, what survives); caveats; methods appendix (data, cleaning, mapping, chart 7 and the validation table, growth measure, load shape, supply, reproduction).
- **Checked in a browser** (Playwright, Chromium) at 1400 px light and dark and at 390 px: no horizontal page scroll (wide tables scroll inside their box), all 10 figures load in the right theme, no console errors.
- **Private data:** nothing in the report reads `data/outputs/private/`. The Dominion short-baseline caveat mentions the PJM check only in words ("internal-use data, not published here"), as D3 allows.

## Region explorer (Prompt 18)

A static page in `app/` (decision D6): `index.html`, `app.js`, `style.css`, with charts drawn in the browser by Observable Plot 0.6.16 (D3 7.9.0), both pinned and loaded from jsDelivr. There is no server and no build step for the page itself.

**Data** (`python -m glg.viz.explorer`, `make explorer` → `app/data/`, 426 KB, committed so the page can be hosted without the pipeline):
- `index.json`: every offered region's headline growth, 95% interval, night-time growth and MW added; the medians of the data-center and comparison groups; the comparison-median index by month; the data cut-off.
- `series/<slug>.json`, one per region:
  - summary numbers (as in `growth_ranking.csv`, `growth_decomposition.csv` and the robustness range);
  - caveat notes;
  - monthly actual and weather-normalised MW, the trailing-12-month index with its band, and the night and afternoon indices;
  - annual load-shape metrics;
  - EIA-860M MW added and retired by technology and year (retirements exported as positive magnitudes);
  - the since-2022 supply comparison;
  - the LBNL queue by type, with completion rate and expected energy;
  - hero annotations and cited utility disclosures.
- **Regions offered:** the six data-center regions, PJM, SPP, TVA, Southern, Duke Carolinas, FPL + Southern, the eight comparison regions, and APS and SRP separately (flagged as parts of Phoenix). DOPD, PSCO, LGEE and the `AZPS_full` sensitivity series are not offered (D10).
- **Caveats travel with the numbers:** each region's notes carry the report's caveats (ERCOT ≤ 41%, Phoenix ends 2025-08, Bonneville unverified, Gulf Power's move, the Dominion zone vs Dominion Energy).
- Nothing is read from `data/outputs/private/`. The Dominion note mentions the PJM check only in words, as D3 allows.

**Page:**
- **Overview:** a ranked dot chart of growth since 2022 with 95% intervals, which is the phone-friendly version of the hero. A toggle switches to night-time growth, and there is a table view.
- **Region view:** stat tiles, caveat notes, and four charts:
  - the weather-adjusted index vs the comparison median, with numbered disclosure markers;
  - actual vs weather-normalised MW;
  - night vs afternoon, the phone version of chart 2, with a link to the paired comparison region;
  - generation added and retired;

  plus a load-shape table, a queue bar, and disclosures with source links.
- Every time-series chart shades COVID. Colours are the theme tokens from `viz/theme.py`, with light and dark selected by the reader's system setting. Every chart has hover tooltips, a legend and an ARIA label. Routing uses the URL hash (`#/dom`), so each region has a shareable link.
- **Hosting:** `make report` copies the page and its data to `report/_site/explorer/`. The report links to it from its opening callout and the growth table, and the explorer links back.

**Checks:**
- `tests/test_explorer.py`: series coverage, the pins, retirement signs, combined-series queue sums, caveats present, finite and compact JSON, no private data, pinned CDN versions.
- In Chromium (Playwright), at 1280 px and 390 px, light and dark:
  - no horizontal page scroll;
  - no console errors;
  - the report → explorer → region → report navigation works;
  - tapping a row of the overview chart opens that region.
- Screenshots are in `app/screenshots/`.

**Bug found while checking:** EIA-860M retirements are stored as negative MW, so the first chart drew Chesterfield's coal retirement upward. They are now exported as magnitudes, and a test checks the sign.

## Publication (Prompt 20)

- **Site:** `.github/workflows/pages.yml` renders the report on GitHub Pages from the committed `report/_freeze/`, so the hosted build needs Quarto only (no data, no Python), and adds the explorer under `explorer/`. A local test with Python made unavailable rendered the same page with the same numbers. The page URLs are https://johnpospisil.github.io/datacenter-grid-load/ and `…/explorer/`.
- **Link previews:** the report and explorer carry Open Graph and Twitter-card tags pointing at `report/social/og_card.png` (1200 × 630, the Dominion hero card).
- **Social images** (`make social`, `src/glg/viz/social.py`): each thread chart is placed unchanged on whichever of 1600 × 900 or 1200 × 1200 it fills best (the two cards are already 1200 × 675). Drafts for Show HN, r/energy, a 7-post Bluesky/X thread and LinkedIn are in `report/social/posts.md`, with alt text. Every thread post is ≤ 280 characters (X counts links as 23).
- **Licences:** code MIT (`LICENSE`); text, figures and derived tables CC BY 4.0, subject to the source terms in NOTICE.md. Citation metadata is in `CITATION.cff`.
- **PJM (decision D3, review issue H4):** the committed docs no longer quote any number from the private PJM check. Those notes are in the git-ignored `data/outputs/private/pjm_check_notes.md`. The repository was published from a fresh, single-commit history; the earlier history is kept only in a local, git-ignored bundle (`data/outputs/private/pre-publish-history.bundle`).
