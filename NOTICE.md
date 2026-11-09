# Data sources and attribution

| Source | Used for | Licence / terms |
|---|---|---|
| U.S. EIA, Form EIA-930 Hourly Electric Grid Monitor | Hourly demand (all published results) | U.S. government work, public domain |
| FERC Form 714, via Catalyst Cooperative [PUDL](https://catalyst.coop/pudl/) | Benchmark for annual EIA-930 totals (`data/reference/annual_benchmarks.csv`) | PUDL data: CC-BY-4.0 (attribution: Catalyst Cooperative). FERC data: public domain |
| EIA Form 861 (service territory, sales, BA association), via Catalyst Cooperative PUDL | BA → county map (`data/reference/ba_county_map.csv`) | PUDL data: CC-BY-4.0 (attribution: Catalyst Cooperative). EIA data: public domain |
| U.S. Census Bureau county population estimates (Vintages 2019 and 2025) and 2020 cartographic boundary files | County population weights (`data/reference/county_population.csv`), county shapes | Public domain |
| Copernicus Climate Change Service, ERA5 hourly single levels | Hourly temperature and dewpoint | Copernicus licence: free use with attribution ("Contains modified Copernicus Climate Change Service information [year]") |
| NOAA NCEI Integrated Surface Database (Global Hourly), via NOAA Open Data Dissemination | Sanity check of ERA5 at 11 airports only | U.S. government work, public domain |
| U.S. BLS Current Employment Statistics (state total nonfarm, SA), via FRED (Federal Reserve Bank of St. Louis) | Optional economic-activity control (`emp_index`) | BLS data: public domain. FRED: cite "retrieved from FRED, Federal Reserve Bank of St. Louis" |
| U.S. EIA, Form EIA-860M Preliminary Monthly Electric Generator Inventory (Sept 2026), and Electric Power Monthly Tables 6.07.A/B | Generation additions and retirements; capacity factors (Prompt 11) | U.S. government work, public domain |
| Lawrence Berkeley National Laboratory and GridTracker, *Queued Up: 2026 Edition* data file (through 2025) | Interconnection queues (Prompt 12, chart 5) | CC BY 4.0: attribute "Lawrence Berkeley National Laboratory and GridTracker" |
| PJM Interconnection, Base Residual Auction reports (2025/26–2028/29) | Capacity clearing prices, RTO and DOM (`data/reference/pjm_bra_prices.csv`, chart 6) | Public reports, cited; prices are facts transcribed with attribution to PJM |
| Monitoring Analytics (PJM Independent Market Monitor), State of the Market reports, vol. 2 (2017–2025) | Zonal real-time load-weighted LMP, 2016–2025 | © Monitoring Analytics; public reports, figures cited with attribution |
| ERCOT Historical RTM Load Zone and Hub Prices, via [gridstatus](https://github.com/gridstatus/gridstatus) | ERCOT hub and load-zone prices | ERCOT public market data; gridstatus: BSD-3-Clause |
| U.S. EIA Wholesale Electricity Market Data (ICE day-ahead hub prices) | Palo Verde, Mid-C, PJM West on-peak prices | EIA republishes with permission from ICE; cite EIA and ICE |
| U.S. EIA electricity retail sales (EIA-861/861M) via EIA API v2; BLS CPI-U (CPIAUCSL) via FRED | Retail prices by state and sector, inflation adjustment | U.S. government work, public domain; FRED: cite "retrieved from FRED, Federal Reserve Bank of St. Louis" |
| Dominion Energy, ERCOT, Arizona Public Service, Salt River Project, Grant County PUD and Southern Company public filings, presentations and earnings materials | Data-center disclosures (`data/reference/utility_reported_load.csv`, `datacenter_announcements.csv`, `dominion_dc_demand.csv`; chart 8, hero annotations) | Public documents; figures are facts cited row by row with source URLs |
| Source Sans 3 (Google Fonts), loaded by the report and explorer pages | Typography | SIL Open Font License 1.1 |
| Observable Plot 0.6.16 and D3 7.9.0, loaded from jsDelivr by the explorer | Explorer charts | ISC licence (both) |
| PJM Data Miner 2, Hourly Load: Metered (DOM) | Private check of the Dominion baseline only; nothing derived from it is published | PJM terms: internal use only; redistribution needs a PJM licence |

_Further sources are added as they're used (see DATA.md)._
