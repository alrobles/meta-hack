# New work ledger — WildSeek (meta-hack)

Every commit in this repository is inside the hackathon window
(first commit 2026-10-26; see `git log --format='%ad %h'`). What the
project *reuses* is infrastructure and packages that predate the event
and live **outside** this repo — listed here explicitly.

## Built during the event (this repo)

| Path | What it is |
|---|---|
| `pipeline/` | The whole data build: GBIF pools, ecoregion raster join (M), env cube, xnicher 88-pair campaign runner/collector, CONUS normals, detection-pressure + HUC8 risk, PNG render, serve export, T1 + ambiguous-tail decision drivers |
| `agent/` | Meta API client, typed fit decisions, watershed/species explainers, muse-wonder briefing, APHIS fetchers, literature extraction |
| `api/`, `app/`, `serve/` | FastAPI+DuckDB monitor API, MapLibre front-end, Docker stack |
| `docs/` | plan, data-sources, night-run log, audit |
| `data/` artifacts (gitignored, reproducible) | species pools, `m_ecoregions`, env cube + normals, `fit_catalog` (101k fits), `species_winner`, `suit/` rasters, `risk_*`, `decisions_*.jsonl` |

## Pre-existing dependencies (NOT built here)

| Dependency | Where it lives | Role |
|---|---|---|
| `xnicher` R package | CRAN (`alrobles/xnicher`) | ellipsoid niche optimizer — the modelling engine we campaign over |
| `oneearthr` R package | `alrobles/oneearthr` | RESOLVE ecoregion geometries used to build each species' M |
| GBIF mirror (`gbif_thinned_120.duckdb`) | lab beegfs | occurrence backbone (pre-built 1/120° dedup) |
| `era5_bioclim` collection | lab beegfs | annual bioclim GeoTIFFs the env cube + normals derive from |
| ecoseek stack + geoseek | `ecoseek.org` | deployment platform WildSeek runs on; own EO embeddings (Clay-style leg) |
| `xsdm_pipeline` `.sif` | KU HPC | R/terra/duckdb container the campaign runs under |
| mammal tree (`v6_point.nwk`, phyloseek) | lab infra | dated mammal phylogeny — phylo leg of the susceptibility score |
| BirdTree Stage2 Ericson posterior | data.vertlife.org (public) | bird phylogeny — 1,048-tree set, consensus for patristic distances |
| `BLIOCPhyloMasterTax.csv` / AVONET Suppl. 1 | birdtree.org / figshare (public) | tree taxonomy + BirdLife↔eBird↔BirdTree crosswalks for name reconciliation |
| 3-distance susceptibility concept | lab PNAS framework (doi.org/10.1073/pnas.2122851119) | the scientific framing the product implements |
| APHIS / USGS WBD / OpenAlex | public services | detection points, watershed boundaries, literature |

The hackathon contribution is the **assembly**: M construction per
species, the two-tier env cube, the 88-pair campaign + AIC table, the
sub-county risk layer, the decision/explain/briefing agent layer, and
the WildSeek product itself.
