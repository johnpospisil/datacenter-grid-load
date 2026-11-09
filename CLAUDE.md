# CLAUDE.md — AI Data Centers and the Power Grid

Project brief: [03-ai-datacenters-grid.md](03-ai-datacenters-grid.md) (the source of truth for scope, method and charts).

**Working title:** *The Load Nobody Planned For: Weather-Adjusted Electricity Demand Growth Across U.S. Grids, 2016–Today*

**Core claim to test:** after removing weather and calendar effects, data-center-heavy grid regions show structural demand growth since 2022, concentrated in a rising 24/7 floor rather than in daytime peaks. The finding is **wrong** if those regions grow no faster than comparison regions, or if the growth shows up only at daytime/peak hours.

---

## Working conventions (read before every task)

- **Environment: the user's `~/tf` virtualenv** (Python 3.12, shared with their TensorFlow work). Run everything with `~/tf/bin/python` (or `make`, which uses it). No `uv`, no project `.venv`. Package `glg` is installed editable (`pip install -e ".[dev]" -c constraints-tf.txt`), src layout, as in brief §7.
- **Never upgrade TensorFlow's dependencies in `~/tf`.** Every `pip install` uses `-c constraints-tf.txt`, which pins numpy 1.26 / tensorflow 2.16 / keras / protobuf / h5py / ml-dtypes. Do a `--dry-run` first if a new package might pull in numpy 2. Snapshot before the first install: `~/tf/pip-freeze-before-glg-2026-10-22.txt`.
- **Dataframes:** polars + DuckDB for anything over 1M rows; pandas only where a library requires it. Store intermediate data as **Parquet**, partitioned by BA/year where it helps.
- **Time:** store every timestamp in **UTC**, hour-ending, as EIA-930 does. Derive local time only for features, using each BA's IANA time zone. Never join on local time.
- **Raw is immutable:** `data/raw/` is written only by `ingest/*`, never edited. Cleaning writes new columns (`demand_raw`, `demand_clean`, `flag_*`), never overwrites.
- **Data location:** all data paths come from `GLG_DATA_DIR` (default `./data`), set in `.env`. Code never hard-codes `/Volumes/...`. See "Storage" below.
- **Secrets:** `EIA_API_KEY` and the Copernicus CDS key go in `.env` (git-ignored) / `~/.cdsapirc`. Never commit them.
- **Caching:** every downloader is idempotent: skip files already on disk, retry with backoff, log what it fetched. Re-running `make data` must be cheap.
- **Tests:** `pytest`. Every cleaning rule and every published number gets a test. Pin headline growth estimates in `tests/test_pinned_estimates.py` so silent changes fail loudly.
- **Reproducibility:** fixed random seeds; `make all` rebuilds everything from raw. Figures are produced by code only, never edited by hand.
- **Language in outputs:** say "consistent with data-center load", not "caused by". Prices are descriptive context, never a causal model.
- **COVID (2020-03 → 2021-12)** is excluded from every baseline and shaded on every time-series chart.

### Storage: internal SSD vs Drive3

Drive3 is a 4 TB WD *My Passport* on USB, formatted HFS+. That is almost certainly a spinning hard drive: fine for bulk sequential copies, slow for the many small random reads that Parquet/DuckDB do.

Estimated footprint:

| Data | Approx. size |
|---|---|
| EIA-930 (all BAs + subregions, 2015–today) | 1–3 GB raw JSON/CSV → ~300 MB Parquet |
| EIA-860M, 861, LBNL queues, PJM/ERCOT market data | < 2 GB |
| ERA5 hourly 2 m temperature + dewpoint, CONUS, 2015–today | ~15–40 GB raw NetCDF → < 1 GB after aggregation to county × hour |
| Models, features, figures | < 5 GB |

The internal SSD had ~317 GB free at the start (~163 GB on 2026-11-08), so **everything fits internally**. Actual footprint: raw ERA5 8 GB (60 MB/month), county × hour weather Parquet 2.2 GB, ISD check data 0.55 GB, BA weather 0.24 GB. Raw ERA5 is needed only to re-grid or add variables, so it can be archived to Drive3 and deleted from the SSD once the analysis is settled.

**Decision:** keep the working data (`GLG_DATA_DIR`) on the internal SSD. Use Drive3 only (a) as the download target for bulk ERA5 NetCDF if space gets tight (`GLG_RAW_WEATHER_DIR=/Volumes/Drive3/glg/era5`), since those files are read once, sequentially, and then aggregated, and (b) as a backup/archive of `data/raw/`. Note that HFS+ is not ideal for heavy parallel I/O, and the pipeline must not fail if the drive isn't mounted.

---

## Decisions

**Audience and pace:** portfolio piece + social media (HN, r/energy, Bluesky, LinkedIn). No journalist outreach. Own pace, moving fast: the brief's weekly milestones are a guide to order only, not a schedule. Prefer the simplest approach that is methodologically sound; don't gold-plate.

| # | Decision | Status |
|---|---|---|
| D1 | Weather source | **Decided: ERA5** (gridded, consistent, no station gaps). NOAA ISD used only for sanity checks |
| D2 | Population weights | Census county population (2020 decennial + annual estimates) |
| D3 | Dominion/PJM-zone history | **Verified in Prompt 2:** EIA-930 DOM is usable only from 2018-09-18 (≈1.5 pre-COVID years). **Resolved 2026-11-06:** no API key (PJM's portal sign-up dead-ends), so DOM `hrl_load_metered` 2015-01 → 2026-11 was exported by hand from Data Miner 2 into `data/raw/pjm/` and loaded by `ingest/pjm_load.py`. It is consistent with cleaned EIA-930 DOM (details only in the git-ignored `data/outputs/private/pjm_check_notes.md`). **Licence:** Data Miner is internal-use only; redistribution of the data *or anything derived from it* needs a PJM licence. **Decided (cautious):** published DOM results use EIA-930 only; PJM is an internal check of the DOM baseline (2016–2019) and is never published or committed. The report may say "checked against PJM metered load" |
| D4 | Primary model | GAM first (interpretable), LightGBM second, degree-day OLS as the sanity floor |
| D5 | Publication target | Quarto report (`report/index.qmd`) on GitHub Pages + static explorer. Quarto is **not installed** yet (`brew install --cask quarto`) |
| D6 | Explorer tech | **Decided: static web page** (Observable Plot / plain HTML reading pre-built JSON, no server) |
| D7 | Monthly refresh | **Decided: no.** One-off analysis with a stated data cut-off date |
| D8 | Weather population weights | **Decided (2026-11-08):** fixed 2020 county population (`population_weights(..., pop_year=2020)`), so population shifts can't move the weather series. The BA → county map itself still varies by year |
| D9 | Reference tables in git | **Decided (2026-11-08):** generated reference tables (`ba_county_map.csv`, `county_population.csv`, ...) stay committed in `data/reference/`, even at a few MB |
| D10 | Headline comparison set | **Decided (2026-11-08):** DOPD (fails validation), PSCO (+8% load step in May 2019) and LGEE (EIA-930 break from 2019) are left out of headline comparisons; appendix only, with caveats. Comparisons: MISO, NYIS, ISNE, FPL, FPC, CPLE, CISO, PACE |
| D11 | Growth reference | **Decided (2026-11-08):** Prompt 9 reports growth since 2022 two ways: vs the flat 2016–2019 baseline (headline) and vs each BA's own 2016–2019 residual trend extended forward (the stricter test), so pre-2022 growth (ERCO, GCPD, DOM) isn't read as new |
| D12 | Comparison yardstick | **Decided (2026-11-08):** each data-center region is reported against both its paired comparison region (like for like, stricter) and the pooled comparison median (stable). Pairs: DOM–CPLE, ERCO–FPC, AZPS+SRP/NEVP–CISO, BPAT/GCPD–PACE, PJM–MISO |
| D13 | ERCOT framing | **Decided (2026-11-08):** ERCO stays in the data-center group, always with the caveat that ERCOT's own large-load data cap data centers + crypto + industrial at ≤41% of its growth, and with its 11–20% range across specifications |

---

## Prompt playbook

Run these in order, one per session or more. Each prompt assumes Claude has read this file and the brief. Tick deliverables off here as they land.

**After each prompt:** ask the user any open questions, and flag clearly any data found to be invalid.

**Progress marking (keep doing this):** when a prompt is finished, change its heading from `**[ ] Prompt N: ...**` to `**[X] Prompt N: ...**` and tick its deliverables `[x]`. When every prompt in a phase is finished, append ` ✅ DONE!` to the phase heading.

### Phase 0: Setup ✅ DONE!

**[X] Prompt 0: Scaffold the repo**
> Read CLAUDE.md and 03-ai-datacenters-grid.md. Initialise git, then scaffold the repo from brief §7 inside this folder (package `glg`, src layout). Set up `uv` with Python 3.12 and dependencies (polars, duckdb, pyarrow, requests, tenacity, python-dotenv, xarray, netCDF4, cdsapi, geopandas, pygam, lightgbm, statsmodels, holidays, matplotlib, altair, gridstatus, pytest, ruff). Add `.env.example`, `.gitignore` (data/, .env), a `config.py` that reads `GLG_DATA_DIR`, a Makefile with stub targets (`data`, `features`, `models`, `figures`, `test`, `all`), and a minimal README. Verify `uv run pytest` passes on an empty suite.

Deliverables:
- [x] git repo with first commit
- [x] `pyproject.toml`, `uv.lock`, `.python-version` (3.12)
- [x] `src/glg/` package skeleton with all modules from brief §7 as stubs
- [x] `src/glg/config.py` (paths from env), `.env.example`, `.gitignore`
- [x] `Makefile` with stub targets; `README.md`; empty `DATA.md`
- [x] `uv run pytest` and `uv run ruff check` both pass

### Phase 1: Demand data (brief week 1) ✅ DONE!

**[X] ✅ DONE! Prompt 1: EIA-930 ingest**
> Implement `src/glg/ingest/eia930.py` using EIA API v2 (`electricity/rto/region-data` and `electricity/rto/region-sub-ba-data`). Pull hourly demand (D), day-ahead demand forecast (DF), net generation (NG) and interchange (TI) for every BA, plus subregion demand, July 2015 → today. Paginate, cache raw JSON per BA × month under `data/raw/eia930/`, retry with backoff, and make reruns fetch only missing or the most recent months. Then write a consolidated Parquet at `data/interim/eia930_hourly.parquet` (UTC timestamps, long format). Also save the BA metadata (codes, names, time zones, parent ISO).

Deliverables:
- [x] `ingest/eia930.py` with CLI entry point (`python -m glg.ingest.eia930`)
- [x] Raw cache in `data/raw/eia930/`, consolidated `data/interim/eia930_hourly.parquet` (+ `eia930_subregion_hourly.parquet`)
- [x] `data/reference/ba_metadata.csv` (code, name, tz, ISO, has-subregions) + `subregion_metadata.csv`
- [x] Log summary: rows per BA, first/last timestamp, missing-hour counts
- [x] `make data` runs this

*Done note:* switched from API v2 to EIA's six-month bulk CSVs, because the API only goes back to 2019-01 (see DATA.md). Result: 6.3M BA-hours (72 BAs, 2015-07 →), 6.1M subregion-hours (90 subregions, 2018-07 →), no duplicate or missing UTC hours. DOM subregion starts 2018-07, so D3 (PJM Data Miner for Dominion's baseline) is likely needed.

**[X] ✅ DONE! Prompt 2: Profile and clean EIA-930**
> Profile `eia930_hourly.parquet` (run the explore-data approach: coverage by BA × year, missing hours, negatives, zeros, repeated-value runs, > 5σ hour-over-hour jumps, BA start/end dates and retirements). Confirm when subregion data (especially PJM's DOM zone) begins, and report what that means for decision D3. Then implement `build/clean.py` with documented, testable flag rules from brief §4 step 2; keep `demand_raw` and `demand_clean`; fill short gaps (≤ 3 h) by interpolation and leave longer ones null. Write every rule, and every BA boundary/definition change you find, into DATA.md.

Deliverables:
- [x] `notebooks/01_eia930_profile.ipynb` with coverage heatmap (BA × month) and anomaly counts
- [x] `build/clean.py` → `data/processed/demand_clean.parquet` with `flag_*` columns (+ subregion version, `data/outputs/ba_quality.csv`)
- [x] DATA.md sections: cleaning rules, known EIA-930 issues, BA changes, subregion start dates
- [x] A recommendation on D3 (EIA-930 subregion vs PJM Data Miner for Dominion)
- [x] Shortlist of ~15–20 BAs/zones with enough clean history to model (`data/reference/ba_shortlist.csv`)

*Done note:* 7 flag rules; most shortlisted BAs keep > 99% of hours. 6 BAs excluded as invalid (SEC, SPA, PSEI, WACM, TEPC, SC), 4 conditional. **D3 recommendation: use PJM Data Miner `hrl_load_metered` for DOM** (EIA-930 gives only ~1.5 clean pre-COVID years, 2018-09 → 2020-02); keep EIA-930 DOM as a cross-check for 2019+. Needs a free PJM API key. Raw DOM 2nd-percentile load already rises ~8.0 → ~13.5 GW (2019 → 2026), not yet weather-adjusted.

**[X] ✅ DONE! Prompt 3: Data-quality tests**
> Write the tests in brief §4 step 6 for demand: no duplicated (BA, hour) keys; exactly one row per UTC hour (DST transitions have no gaps or doubles); each cleaning rule tested on a small synthetic fixture; annual totals per BA within ±3% of an independent EIA figure (choose the best available benchmark, e.g. EIA-930's own annual summaries or EIA-861 sales for single-utility BAs, and explain the choice). Report which BAs fail reconciliation and why.

Deliverables:
- [x] `tests/test_demand_integrity.py`, `tests/test_clean_rules.py`, `tests/test_dst.py`, `tests/test_annual_reconciliation.py`
- [x] `data/reference/annual_benchmarks.csv` with sources cited
- [x] Short table in DATA.md: BA, reconciliation error by year, pass/fail, explanation
- [x] All tests green (or failures documented as known and excluded)

*Done 2026-11-06:* benchmark = FERC Form 714 via PUDL (EIA-930's own summaries aren't independent; EIA-861 sales leave out losses). 185 of 210 BA-years are within ±3%; the 25 that aren't are pinned in `KNOWN_FAILURES`. All 9 focus BAs with a benchmark pass 2016–2019. ⚠ AZPS: EIA runs 8–22% above FERC from 2025-09 (cross-check in Prompt 14). LGEE breaks by up to +19% from 2021; PSCO by +5–9% from 2023-08. SOCO and DOM have no FERC match (DOM checked privately against PJM). Several BAs drop whole DST days (left null). 43 tests green.

### Phase 2: Weather and calendar (brief week 2) ✅ DONE!

**[X] ✅ DONE! Prompt 4: BA → county mapping**
> Build `data/reference/ba_county_map.csv` mapping each modelled BA (and PJM/ERCOT subzone) to the counties it serves, with a weight for counties split between BAs. Use the best public sources (EIA-861 "Service Territory" utility-county file joined to utility→BA, plus HIFLD control-area or retail-territory shapefiles if needed), and record the source for every BA. Validate on 4–5 well-known BAs (e.g. ERCO, DUK, SRP, FPL, and DOM) with maps. Add population per county (Census 2020 + annual estimates).

Deliverables:
- [x] `build/ba_weather_map.py` and `data/reference/ba_county_map.csv` (BA, county FIPS, weight, source)
- [x] `data/reference/county_population.csv` by year
- [x] Validation maps in `notebooks/02_ba_mapping.ipynb` for the 4–5 test BAs
- [x] DATA.md section on the mapping method and its known weaknesses

*Done 2026-11-08:* EIA-861 territories + residential customers (via PUDL), raked to county population, built per year 2015–2025 so boundary moves are followed (AEC → SOCO 2021; Gulf Power SOCO → FPL 2022). Fixes: Texas wires utilities given imputed customers (Houston was coming out 92% MISO), and PUDL's city/county name mix-up corrected (Fairfax County had dropped out). DOM is built from anchor utilities; CPLW is split off CPLE. Every CONUS county is mapped; people per customer is 2.0–2.9 for every shortlisted BA. Census series bridged across 2020. 80 tests green. ⚠ Gulf Power's ~1.3–1.5 GW enters EIA-930 FPL (and leaves SOCO) in about mid-2022: this must be handled in Prompts 7–9 or it reads as growth. Known weakness: slivers (Harris 86% ERCO, Wake 59% CPLE); Prompt 5 to check how sensitive BA temperature is to this.

**[X] ✅ DONE! Prompt 5: Weather ingest and features**
> Implement `ingest/weather.py` for ERA5 hourly 2 m temperature and 2 m dewpoint over CONUS, 2015 → today, via `cdsapi`, downloading month by month to `GLG_RAW_WEATHER_DIR` (see Storage section). Aggregate each month to county × hour right after download (area-weighted grid cells per county), then to BA × hour with population weights. Compute temperature, dewpoint, relative humidity or heat index, CDH and HDH at a few base temperatures (e.g. 65 °F, plus 55/75 °F variants), and 24 h/72 h lagged/rolling temperature for thermal inertia. Check coverage ≥ 95% of hours per BA.

Deliverables:
- [x] `ingest/weather.py` (resumable, month-chunked) and aggregation code
- [x] `data/processed/weather_ba_hourly.parquet`
- [x] Sanity plots: BA temperature vs a nearby airport station (NOAA ISD) for 3 BAs
- [x] `tests/test_weather_coverage.py` (≥ 95% hours, plausible ranges)
- [x] Disk usage report: raw ERA5 size, where it lives, whether raw NetCDF can be deleted

*Done 2026-11-08:* `build/weather.py`: ERA5 → 3,108 counties (cell overlap areas) → 58 BAs (D8 weights), hour-ending aligned to EIA-930; humidity, heat index, degree-hours at 55/65/75 °F (computed per county before averaging), 24/72 h means, 24 h lag. 100% of shortlisted BAs' demand hours covered (DOM included). ERA5 vs airports: r 0.97–0.996, no drift at Dulles, DFW or Miami. ⚠ Phoenix: ERA5 may miss ~0.3 °C of 2023–25 warming (Sky Harbor −0.6 °C, area median −0.3 °C) → robustness check in Prompt 15. The mapping choice moves BA temperature by a near-constant offset only (r ≥ 0.997). Disk: raw ERA5 8 GB, county Parquet 2.2 GB. 98 tests green.

**[X] ✅ DONE! Prompt 6: Calendar features and the modelling table**
> Implement `build/features.py`: local hour, day-of-week, month, US federal holidays plus the days around Thanksgiving/Christmas, DST flags, a COVID-period flag, and a time trend term (kept out of the baseline model but available for diagnostics). Join with clean demand and weather into one modelling table per BA. Add an economic-activity control if a cheap, honest one exists (e.g. state-level monthly employment from BLS) and document the choice.

Deliverables:
- [x] `build/features.py` → `data/processed/model_table.parquet`
- [x] Feature dictionary in DATA.md
- [x] `tests/test_features.py` (holidays, DST, no leakage of post-baseline information)

*Done 2026-11-08:* 2.25M rows (23 BAs incl. DOM from its EIA-930 subregion) × 35 columns: clean demand + Prompt 5 weather + local-time calendar (hour of the hour's start, day type with major/minor holidays and bridge days, DST flags measured from the time zone), COVID flag, baseline flag, and a trend term kept for diagnostics only. Economic control: BLS state nonfarm employment via FRED, BA-weighted, 2019 = 100. It is **kept out of the primary model** (it would extrapolate the baseline trend and absorb the growth we're measuring) and used only in Prompt 15's with/without spec. The leakage test (rebuild from data cut at 2020) passes, and it caught a DST-day edge bug. 112 tests green.

### Phase 3: Weather normalisation (brief weeks 3–4) ✅ DONE!

**[X] ✅ DONE! Prompt 7: Baseline models and validation**
> Implement `models/normalize_gam.py` (pygam: smooth temperature × hour interactions, factor terms for day-type, holidays) and a simple degree-day OLS benchmark. Fit per BA on the 2016–2019 baseline (or the longest pre-COVID window available, per D3). Validate with leave-one-year-out across the baseline years: report MAPE, R², and bias per BA, and plot residuals over the baseline to show they don't drift. If pygam struggles (speed, convergence, numpy compatibility), fall back to statsmodels GAM or spline-based OLS and say so.

Deliverables:
- [x] `models/normalize_gam.py`, `models/normalize_ddr.py` (degree-day regression)
- [x] `data/outputs/validation_metrics.csv` (BA × model × held-out year: MAPE, R², bias)
- [x] Chart 7 draft: predicted vs actual on a held-out year + baseline residual time series, for 3–4 BAs
- [x] A list of BAs that fail validation (e.g. MAPE > 5% or drifting residuals), excluded or flagged

*Done 2026-11-08:* pygam works on the TF stack (about 5 s per fit), so no fallback was needed. The GAM beats degree-day OLS in every BA: held-out shape MAPE (error after removing each year's mean) is 2.0–3.5% vs 2.3–4.1%. Most held-out error is level drift, because the models have no trend term. That drift is reported as a residual trend: ERCO +2.2 %/yr, GCPD +3.5, PSCO +2.4, DOM +1.5; CISO −2.1, AZPS −1.5. Prompt 9 must measure growth against it. **DOPD fails** (shape MAPE 5.3%, +5 %/yr). ⚠ Steps inside the baseline: PSCO +8% from May 2019 (also in FERC 714, so a real change in the BA's load) and LGEE −5% from April 2019. A private PJM check of DOM's baseline was run (results only in the git-ignored private notes, D3). Chart 7 draft is in `report/figures/`. 122 tests green.

**[X] ✅ DONE! Prompt 8: LightGBM comparison**
> Implement `models/normalize_gbm.py` with the same features and the same validation as Prompt 7. Compare against the GAM per BA, and check that the GBM doesn't just learn a time trend (no trend feature in the baseline). Recommend a primary model per BA, or overall, with reasons.

Deliverables:
- [x] `models/normalize_gbm.py`
- [x] Model comparison table (BA × model MAPE) and a one-paragraph recommendation
- [x] SHAP or partial-dependence plot for 2 BAs, confirming the temperature response looks physical

*Done 2026-11-08:* the GBM fits slightly better on held-out years (better shape MAPE in 18 of 23 BAs, median 0.2 points), with the same bias pattern by held-out year, so it isn't extrapolating a trend. In-sample, though, it absorbs about half of each BA's 2016–2019 drift by memorising year levels through weather. Since 2022 it changes the counterfactual by only ±0.4% (GCPD ±0.9%). Both models' temperature responses are physical: U-shaped, with the minimum at about 15–18 °C. **Recommendation: GAM primary everywhere; GBM as the model-family robustness check** (outputs `model_comparison.csv`, `model_level_gap.csv`). Notebook `04b_gbm_comparison.ipynb`. 128 tests green.

**[X] ✅ DONE! Prompt 9: Structural growth estimates**
> Using the chosen model, predict baseline-relationship demand for every hour 2016 → today. Compute structural growth = actual − predicted, aggregated by month and year per BA, in TWh/yr and average MW, with uncertainty (bootstrap over residual blocks or across model families). Index to 2019 = 100. Rank BAs by growth since 2022. Build hero chart v1.

Deliverables:
- [x] `data/outputs/structural_growth_monthly.parquet` and `_annual.csv` (with intervals)
- [x] Ranked table: top BAs by weather-adjusted growth since 2022 vs 2016–2019 trend
- [x] **Hero chart v1:** indexed small multiples, top 10 BAs, COVID shaded
- [x] `tests/test_pinned_estimates.py` pinning headline numbers (with tolerance)

*Done 2026-11-08:* weather-adjusted growth from 2022 to the 12 months ending 2026-10, with 95% intervals (jackknife over baseline years + block bootstrap):
- **DOM +23.6%** (+3.2 GW), ERCO +19.5% (+9.4 GW), Phoenix AZPS+SRP +13.7%, BPAT +13.2%, PJM +6.4%;
- median data-center region +13.4% vs comparison regions +3.3%; beyond each BA's own 2016–2019 trend (D11), 11.6% vs 2.6%;
- GBM and degree-day OLS agree within about 1.5 points.

Breaks handled:
- FPL/SOCO measured from 2023, plus a combined FPL+SOCO series;
- AZPS window ends 2025-08;
- ⚠ new: the Phoenix load split between AZPS and SRP swings from 2025, so the combined **AZPS+SRP** series is the Phoenix headline.

BPAT's smooth rise is a lead for Prompt 14. Hero chart v1 (now `report/figures/hero.png`, Prompt 16); numbers are pinned. 154 tests green.

**[X] ✅ DONE! Prompt 10: Load-shape fingerprint**
> Implement `models/load_shape.py`: per BA × year, overnight minimum (e.g. mean of 1–5 am local, weather-adjusted), min/mean ratio, load factor, and peak. Decompose residual growth into a flat 24/7 component and a weather-/hour-sensitive component (e.g. regress residual on hour-of-day and temperature terms, per year). Pick comparison zones of similar climate without big data-center build-outs, and justify each pairing. Make charts 2 and 3.

Deliverables:
- [x] `models/load_shape.py` → `data/outputs/load_shape_metrics.csv`
- [x] Growth decomposition table (24/7 vs variable MW) per BA
- [x] Documented comparison-zone pairings, with the reason for each
- [x] **Chart 2** (rising floor, Dominion vs comparison) and **Chart 3** (day × hour residual heat calendar for Dominion)
- [x] An explicit verdict against the brief's "wrong if" test

*Done 2026-11-08:* **the "wrong if" test is passed on both halves.**
- All six data-center regions grew as a 24/7 floor: DOM added +3.24 GW at night and +3.23 GW in the afternoon (flat share 103%, GBM 100%). Their median night-time growth since 2022 is 18.0%, against 4.5% for the comparison regions.
- A flat shape alone is not proof: small comparison growth is flat too, and crypto mining and oil-field load (ERCO) are also 24/7. It is the size of the night-time growth that separates the regions.
- Rooftop solar lowers the afternoon residual (Phoenix, CISO, NY, NE), so night-hours growth is the robust measure.
- ⚠ SOCO (since 2023) grew in daytime and weather-sensitive hours, not as a floor (check Georgia Power's disclosures in Prompt 14).

Pairings: DOM–CPLE, ERCO–FPC, AZPS+SRP/NEVP–CISO, BPAT/GCPD–PACE, PJM–MISO. Charts 2 and 3 are in `report/figures/`; notebook `05_load_shape.ipynb`. 168 tests green.

### Phase 4: Supply, queues, prices (brief week 5)

**[X] ✅ DONE! Prompt 11: Generation additions (EIA-860M)**
> Implement `ingest/eia860m.py` and `models/supply_gap.py`: monthly generator inventory → MW added and retired per BA/ISO per year, by technology. Convert to expected annual energy (TWh) with technology capacity factors (cite the source, e.g. EIA's Electric Power Monthly averages) so supply compares with the weather-adjusted demand growth in energy terms.

Deliverables:
- [x] `ingest/eia860m.py`, `data/processed/capacity_changes.parquet`
- [x] `models/supply_gap.py` → `data/outputs/supply_vs_demand.csv` (region × year: demand growth TWh, new supply TWh)
- [x] **Chart 4:** diverging bar, weather-adjusted growth vs new generation, by region

*Done 2026-11-08:*
- **Data:** the EIA-860M September 2026 release (35,805 generators), with capacity factors from Electric Power Monthly 6.07.A/B (2023–25 mean); batteries count as zero energy. The supply change is month-weighted to match Prompt 9's windows. DOM = PJM plants in the 134 counties Dominion serves (~31 GW).
- **Dominion:** weather-adjusted demand grew +28.1 TWh/yr since 2022, but local net new supply is only **+1.4 TWh/yr** (solar offset by coal/gas retirements), a gap of 23% of 2022 demand. The rest comes from imports or harder-running existing plants. CVOW (2.6 GW) is planned for 2027-01, outside the window.
- **Other regions:** ERCO +82.8 vs +65.1 (solar/wind; +14.7 GW batteries), PJM +52.6 vs +17.6, BPAT +7.3 vs +1.0. CISO, PACE, FPL+SOCO and NYIS added more than their demand grew.

Chart 4 and notebook `06_supply_gap.ipynb`. 179 tests green.

**[X] ✅ DONE! Prompt 12: Interconnection queues (LBNL Queued Up)**
> Implement `ingest/queues.py` for the latest LBNL "Queued Up" release. Summarise active queue MW by ISO, technology and queue-entry year, plus completion rates and median time to commercial operation where the data allows. Make chart 5.

Deliverables:
- [x] `ingest/queues.py`, `data/processed/queues.parquet`
- [x] Summary table: MW in queue by ISO × technology; historical completion rates
- [x] **Chart 5:** stacked bars of queue backlog by technology and year, per ISO

*Done 2026-11-08:*
- **Data:** LBNL *Queued Up 2026* (through 2025, CC BY 4.0): 38,201 requests, of which 1.87 TW is active.
- **Completion:** 19% of the 2000–2020 entrants reached operation (13% of MW). ERCOT has the best rate (30% / 24%) and CAISO the slowest path to operation (7.8 years median).
- **DOM zone:** 29 GW active, mostly solar and storage; 16% completion by MW.
- **Southeast:** the backlog is the most gas-heavy (63 GW).
- ⚠ LBNL "Dominion" = Dominion Energy South Carolina (DOM is selected by PJM county). APS lists 15.8 GW with no fuel type.

Chart 5 and notebook `07_queues.ipynb`. 193 tests green.

**[X] ✅ DONE! Prompt 13: Price context**
> Implement `ingest/iso_markets.py` (via gridstatus where it works; cite its licence) for PJM Base Residual Auction clearing prices by delivery year and zone, plus annual average real-time LMPs for key zones, and `ingest/eia861.py` for state retail prices by sector. Make chart 6 (PJM capacity price step chart with annotated events). Keep everything descriptive.

Deliverables:
- [x] `ingest/iso_markets.py`, `ingest/eia861.py` and their Parquet outputs
- [x] **Chart 6:** PJM capacity auction step chart with annotations
- [x] Retail price trend chart for VA, TX, GA, AZ vs the U.S. average
- [x] Licence/attribution notes for each source in `NOTICE.md`

*Done 2026-11-08:*
- **Capacity:** PJM capacity prices (verified against PJM's BRA PDFs by a test) went from $29 (2024/25) to $270 (2025/26; DOM $444). They then cleared at the cap three times ($329, $333, $325), with the last two auctions 6.5–6.8 GW short of the reliability requirement.
- ⚠ The DOM zone was outside the auction (Fixed Resource Requirement) before 2025/26.
- **Energy:** DOM's real-time LMP was $65.6/MWh in 2025, 29% above the PJM average and almost all congestion (from the Market Monitor's reports, 2016–2025). ERCOT prices come via gridstatus; Palo Verde and Mid-C from EIA ICE.
- **Retail** (EIA API + CPI): Virginia's real prices are up 14% since 2021, against 5% for the U.S.; TX, GA and AZ are flat or down.

All descriptive. Chart 6, retail chart, notebook `08_prices.ipynb`.

**[X] ✅ DONE! Prompt 14: Data-center context table**
> Build `data/reference/datacenter_announcements.csv`: a hand-curated, cited table of major data-center announcements and utility disclosures in the focus regions (date, location/county, BA, announced MW, source URL, confidence). Gather utility IRP and earnings-call load figures for Dominion, Georgia Power, APS/SRP and ERCOT, to compare with our estimates later. Treat this as context, not ground truth.

Deliverables:
- [x] `data/reference/datacenter_announcements.csv` (every row cited)
- [x] `data/reference/utility_reported_load.csv` (utility-disclosed data-center load figures)
- [x] Annotation set for the hero chart

*Done 2026-11-08:*
- **Tables:** 23 utility disclosures (each typed as measured, contracted, pipeline, forecast or requests), 13 announcements and 7 hero-chart labels, all cited and tested.
- **Measured load:** Dominion's data-center peak was 4.0 GW (2025) and 4.8 GW (Aug 2026); Grant PUD's was 280 aMW (37% of its load).
- ⚠ **ERCOT:** all approved large loads peak at only 3.9 GW against our +9.4 GW of growth, so data centers are a minority of Texas's growth.
- ⚠ **BPAT:** no disclosure found, so the lead is still unverified.
- ⚠ **SOCO:** data-center sales are up 55%, but its growth is not floor-shaped (unresolved).

### Phase 5: Robustness and write-up (brief week 6) ✅ DONE!

**[X] ✅ DONE! Prompt 15: Robustness checks**
> Rerun the growth estimates under: baselines 2016–2019, 2017–2019, and 2022-only (as a growth-since-2022 view); model families GAM vs GBM vs degree-day OLS; excluding BAs with boundary changes; and with/without the economic control. Compare our estimates with utility-reported figures (Prompt 14). Discuss the alternative growth drivers named in the brief (EVs, heat pumps, manufacturing, ERCOT crypto mining) with whatever evidence the load shape gives. Run the validate-data checklist on the headline claims.

Deliverables:
- [x] `notebooks/05_robustness.ipynb` and `data/outputs/robustness_grid.csv`
- [x] Specification curve or forest plot: headline growth per BA across all specs
- [x] Comparison table: our estimates vs utility disclosures
- [x] A list of claims that survive every spec and those that don't
- [x] Phoenix weather drift: AZPS/SRP re-run with ERA5 + 0.3 °C from 2023 (DATA.md, "Phoenix drift")
- [x] FPL/SOCO: estimates with Gulf Power's mid-2022 boundary move handled (DATA.md, BA → county mapping)
- [x] Supply gap with regional capacity factors (EIA-923 generation ÷ 860M capacity by state/BA × technology) instead of national ones (decided 2026-11-08, Prompt 11)

*Done 2026-11-08:*
- **Grid:** 7 specifications (GAM / GBM / DDR, 2017–19 baseline, fitted on 2022, employment control, Phoenix +0.3 °C), in `notebooks/09_robustness.ipynb`.
- **Survives everything:** DOM is the fastest-growing region (17.7–23.9%). The six data-center regions lead the comparison set by 8–10 points (3.6–4.5×). DOM's local new supply is ≤5% of its growth, also with regional capacity factors.
- **Survives in weaker form:** under pre-registered Prompt 2 roles the gap is 2–6 points (1.6–2.6×).
- **Doesn't survive:** ERCO falls to 10.9% with the employment control, and ERCOT's own data caps large loads at ≤41% of its growth.
- **Utility comparison:** Dominion's metered data centers (2022→2025: +1.37 GW; 2019→2025: +2.82 GW) explain 60–73% of our DOM growth.
- **Validation:** independent recomputation matches; assessment "share with caveats".
- ⚠ Phoenix's floor could also be semiconductor fabs.

**[X] ✅ DONE! Prompt 16: Final figures and theme**
> Implement `viz/theme.py` and `viz/figures.py` so `make figures` regenerates charts Hero and 2–7 in a consistent, accessible style (colour-blind-safe, light and dark variants, direct labels, source line on each chart). Export PNG + SVG and a social-card crop of the hero and chart 2.

Deliverables:
- [x] `viz/theme.py`, `viz/figures.py`; `make figures` works from processed data
- [x] `report/figures/` with all charts (PNG + SVG), plus social crops
- [x] Every chart has title, units, source, COVID shading where relevant

*Done 2026-11-08:*
- **Output:** `make figures` redraws the hero, charts 2–8, the retail and robustness charts and 2 social cards (1200 × 675) in about 10 s, as PNG + SVG in light and dark variants. Output is byte-reproducible.
- **Palette:** Okabe-Ito, checked with the dataviz validator in both themes (dark hues stepped into the dark lightness band). Text tokens pass WCAG AA.
- **Checks:** every figure has a source line and alt text; `build` refuses a time-series chart without COVID shading.
- **New chart 8:** Dominion's metered data-center demand vs our DOM estimate. It accounts for ~60% of our 2022–25 growth.
- **Titles tightened:** chart 2 "nearly as many", chart 7 "own pre-2022 trend".
- **Notebooks** now call `glg.viz.figures`; the drafts `hero_v1` and `chart7_validation_draft` were removed.
- 228 tests green.

**[X] ✅ DONE! Prompt 17: Report**
> Write `report/index.qmd`: question, headline findings, method in plain language, the charts, caveats ("consistent with", not "caused by"), robustness, and a methods appendix (model validation, cleaning rules, BA mapping). Target a smart general reader; keep jargon defined. Render it to HTML.

Deliverables:
- [x] `report/index.qmd` and rendered `report/_site/` (or `index.html`)
- [x] Methods appendix with chart 7 and validation tables
- [x] Final pass of README.md, DATA.md, NOTICE.md

*Done 2026-11-08:*
- **Report:** `make report` renders `report/index.qmd` to `report/_site/index.html` in about 10 s.
- **Order:** it leads with Dominion (+23.6%, a 24/7 floor, 60–73% matched by Dominion's metered data centers) and gives the region comparison as a range: 2–10 points depending on the grouping. ERCOT carries the ≤41% caveat; Phoenix carries the fabs caveat.
- **Numbers:** every result comes from `src/glg/report.py`, which reads the outputs. `tests/test_report.py` fails if a headline number is hand-typed.
- **Figures:** they follow the reader's light/dark setting and open full size on tap. Checked in Chromium at desktop and phone widths, in light and dark.
- **Repo:** no git remote yet, so the repo links are relative (to fix in Prompt 20). 236 tests green.

**[X] ✅ DONE! Prompt 18: BA explorer app**
> Build the interactive explorer as a static web page (decision D6): pick a BA → actual vs weather-normalised demand, load-shape metrics, and supply pipeline. Pre-compute compact JSON per BA from the outputs so the app is static and fast. Test it in a browser at phone and desktop widths.

Deliverables:
- [x] `app/` with build script that exports per-BA JSON
- [x] Working static explorer; screenshots at mobile and desktop widths
- [x] Linked from the report

*Done 2026-11-08:*
- **Build:** `make explorer` writes 22 regions of JSON (426 KB, committed) to `app/data/` via `glg.viz.explorer`. `app/` is plain HTML plus Observable Plot (pinned CDN), with no server.
- **Overview:** a phone-friendly ranked growth chart (all hours or night) that opens a region on tap.
- **Region view:** tiles and caveats, the index vs the comparison median, actual vs weather-normalised MW, night vs afternoon (the phone version of chart 2), a load-shape table, generation added and retired, the queue, and cited disclosures.
- **Hosting:** `make report` copies it to `report/_site/explorer/`, linked both ways with the report.
- **Checks:** in Chromium at 1280 and 390 px, light and dark: no sideways scroll, no console errors. Screenshots are in `app/screenshots/`.
- **Bug found:** 860M retirements are stored negative, so they first drew upward; now fixed and tested. 246 tests green.

### Phase 6: Review and publish (brief week 7)

**[X] ✅ DONE! Prompt 19: Pre-publication review**
> Act as a sceptical grid-industry reviewer. Review the report, figures and code for overclaiming, methodological holes, EIA-930 quirks we missed, and unclear charts. Produce a list of issues by severity and fix the ones we agree on. Optionally draft a short DM to an energy person on Bluesky/LinkedIn asking them to sanity-check the 3 key claims.

Deliverables:
- [x] Review issues list with resolution status
- [x] Fixes committed; pinned-estimate tests updated deliberately, if numbers changed
- [x] (Optional) reviewer DM draft

*Done 2026-11-08:*
- **Issues:** 15 in `report/REVIEW.md` (4 high, 6 medium, 5 low); 10 fixed by wording, caveats or a redrawn source line, 4 needed no change, 1 is open. No headline number changed, so the pins are untouched.
- **Fixed:**
  - the summary wrongly said EVs don't add night load;
  - the Dominion short-baseline caveat misstated what the baseline affects: growth since 2022 is practically unchanged (only the level vs 2019 moves);
  - ERCOT's 41% was called a hard cap and is now a guide;
  - the supply bullet implied imports and capacity prices were causal;
  - the intervals now say what they cover;
  - added general caveats on rooftop solar and preliminary data.
- **H4 (PJM-derived numbers in committed docs and history):** resolved in Prompt 20. Numbers moved to the git-ignored private notes, docs reworded, and the repo published from a fresh, squashed history.
- **Draft message:** `report/social/reviewer_dm.md`. 246 tests green.

**[ ] Prompt 20: Publish (portfolio + socials)** (prepared 2026-11-08; awaiting the push)
> Prepare publication: GitHub repo polish as a portfolio piece (README leading with the hero chart and 3-line findings, how to reproduce, licence, citation, data cut-off date), GitHub Pages for the report and explorer. Then draft social posts: a Hacker News "Show HN" title + first comment, an r/energy post, a Bluesky/X thread (5–7 posts, one chart each), and a LinkedIn post framed around the data-science method. Export chart images sized for each platform.

Deliverables:
- [ ] Public repo with portfolio-quality README + GitHub Pages site (report + explorer)
- [x] `report/social/` with platform-sized chart images
- [x] Drafts for Show HN, r/energy, Bluesky thread, LinkedIn in `report/social/posts.md`

*Prepared 2026-11-08:*
- **Repo:** README leads with the hero chart, three findings, how to reproduce, the cut-off, licence (MIT code, CC BY 4.0 content) and `CITATION.cff`.
- **Pages:** the workflow renders from the committed freeze; tested without Python.
- **Socials:** 7 thread images plus a 1200 × 630 link-preview card, with post drafts and alt text.
- **PJM (H4):** numbers moved to git-ignored private notes, and history squashed to a single commit (old history in a git-ignored bundle).
- **Remaining:** create the public GitHub repo, push, and enable Pages.

### Stretch (after publishing)
- **S1 Emissions:** marginal fuel mix in high-residual hours (EIA-930 generation by fuel). Is new load met by gas?
- **S2 Forecast:** next-year demand per BA with prediction intervals, scored publicly as data arrives.
- **S3 International benchmark:** Ireland's EirGrid/CSO data-center consumption series vs our method applied to Irish demand.

---

## Pre-flight checklist (before Prompt 1)
- [x] EIA API key in `.env` (verified: API returns 200)
- [x] Copernicus CDS key in `~/.cdsapirc` (format checked; first real request in Prompt 5). Remember to accept the ERA5 single-levels licence on the dataset page. ERA5 requests queue on Copernicus's side, so start Prompt 5's downloads early (they can run in the background while Prompts 2–4 proceed)
- [x] `brew install --cask quarto` (needed by Prompt 17; Quarto 1.10.19 installed)
