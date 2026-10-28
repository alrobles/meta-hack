# Data sources — verified endpoints

Flagship pathogen: **HPAI H5N1 (clade 2.3.4.4b)**. Dual track:
wildlife susceptibility (global, literature-driven) + U.S. supply-chain
exposure (county-level, detection-driven).

## USDA APHIS Geospatial Hub — primary, no key

Base: `https://services7.arcgis.com/2C1NQ7u6M6SXoa8p/arcgis/rest/services`
(91 public FeatureServers, `f=json`, `resultRecordCount` cap 2000,
exports GeoJSON/CSV). Client: `agent/aphis.py`.

| Service | Layer | Content |
|---|---|---|
| `VS_Avian_Influenza_Wild_Bird_Surveillance_Dashboard_data_view_feature_layer_` | 0 | ~174k wild-bird samples, county-level, since 2021-07 |
| `VS_HPAI_Livestock_Events_Feature_Layer` | 11 | HPAI-in-cattle events, state-month aggregates |
| `VS_Wild_Bird_Migratory_Flyways_US_Admin_and_Global` | 0/1 | 4 N. American flyways + global flyways (spread priors) |

Wild-bird fields: `State, County, Watershed, Date_Collected` (epoch ms),
`Final_IAV / Final_H5 / Final_H7` ∈ {Detected, Not Detected, ""},
`Final_Pathogenicity` = `'High Path AI'` for confirmed HPAI (~9.5k).

Dates in `where`: `Date_Collected >= TIMESTAMP 'YYYY-MM-DD 00:00:00'`.

Also on the hub (unused for now): NWS screwworm layers, RHDV2 county
counts, NASS 2017 ag layers, rail-network commodity tons.

## Not in the hub — mammal detections

APHIS' [HPAI in mammals](https://www.aphis.usda.gov/livestock-poultry-disease/avian/avian-influenza/hpai-detections/mammals)
page is an HTML table (species × state, strain EA/AM/reassortant;
locations rounded to state capitol). Scrape or cross-check against the
literature-mined host list.

## NASS QuickStats — poultry inventory (risk denominator)

`https://quickstats.nass.usda.gov/api` — free api.data.gov key.
`CHICKENS, LAYERS, INVENTORY` and `TURKEYS` by county. Context: ~378.5M
U.S. layers; 9.4B broilers + 218M turkeys processed in 2023.

## WAHIS (WOAH) — global official reports

Semi-official JSON API: `POST https://wahis.woah.org/pi/getReportList`
(payload filters by disease/country/date; see
loicleray/WOAH_WAHIS.ReportRetriever). Weekly Excel extract via support
request. Coarse resolution — use for non-U.S. wildlife/domestic reports.

## Literature + taxonomy — already wired

- OpenAlex (`agent/lit_search.py`): no key; `OPENALEX_MAILTO` for polite pool.
- GBIF (`agent/resolve.py`): species match + vernacular search, no key.

## Optional depth

- NCBI Virus / BV-BRC: H5N1 genotypes (B3.13 dairy lineage, reassortants).
- FAO EMPRES-i: global event dashboard (validation cross-check).
- USDA AMS egg-market reports: supply-chain price signal for the demo.
