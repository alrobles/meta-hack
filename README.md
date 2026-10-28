# WildSeek — wildlife susceptibility monitor

Ecological surveillance for wildlife professionals — not medical advice.

**Meta Global AI Developer Hackathon — Indie track** · live demo: `wildseek.ecoseek.org`

A living host-susceptibility + supply-chain risk monitoring system.

> *WildSeek is an ecological surveillance research tool for wildlife
> professionals. Not medical advice, not for personal health decisions.*
Extends the three-distance ML framework from
[Robles-Fernández et al. 2022, PNAS](https://doi.org/10.1073/pnas.2122851119)
— geographic, environmental, and phylogenetic distances — into a
continuously-updated surveillance tool.

Flagship system: **HPAI H5N1 (clade 2.3.4.4b)** — mammal spillover
(74 species infected and growing ~50%/year) × U.S. poultry/dairy
supply-chain exposure, county-level.

## Architecture

```
GBIF occurrences (1/120° grid)   USDA APHIS hub (ArcGIS, no key)
        │                        wild-bird + mammal detections
        ▼                        (species-level) + flock events +
  pipeline/ ── species pools +   flyway polygons
        │   ecoregion accessible area (M, 95% of presences)
        │   + ERA5 30-year normals → env_occ / env_m
        ▼
  xnicher campaign ── 88 bio-var pairs × 1,148 species
        │   = 101,376 fits, all accepted; AIC winners
        ▼
  agent/ ── Muse Spark typed triage over fit diagnostics
        │   ambiguous tail → 49 flag_review w/ substantive reasons
        ▼
  pipeline/ ── suitability rasters MASKED to each species' M
        │   (EPSG:3857 PNGs; fix: never project outside the
        │   accessible area — CONUS-wide bleed was wrong)
        ▼
  susceptibility model (PNAS three-axis, reimplemented):
        │   env  = Jaccard over occupied 19-bioclim cells
        │   geo  = Jaccard over M ecoregion sets
        │   phy  = patristic distance, phyloseek v6 dated tree
        │          (mammals; birds = taxa proxy until BirdTree)
        │   labels = 159 APHIS-confirmed H5N1 host species
        │   model = logistic ×2-interactions vs HGB;
        │           winner logreg ROC-AUC 0.876, bootstrap CIs
        │           → P(host) per species, live at /susceptibility
        ▼
  risk = detection kernel × mean aves suitability → HUC8
        → county exposure (flock confirmations × birds affected)
        ▼
  api/ + app/ ── wildseek.ecoseek.org: risk-first monitor,
             click→watershed/county/detection cards,
             /explain + /briefing with baked agent outputs
```

Two parallel views of the same score: which wild vertebrates are
climatically suitable hosts where, and which watersheds sit inside
the risk zone.

## Hackathon delta

- Meta Model API throughout: muse-spark-1.3 for typed triage and
  /explain, muse-wonder-1.0 for the narrative /briefing,
  muse-spark-1.2-contributor for bulk extraction; muse-glimmer
  self-hosted for free offload.
- Agentic decision layer with real teeth: an ambiguous-tail selector
  surfaces only suspicious fits; the agent's `switch_to` verdicts are
  executed by `pipeline/refit_switched.R` and both AICs land in
  `decisions_delta.csv`.
- Living host list: agentic literature mining → provenance-tracked
  evidence (OpenAlex → typed JSON).

Pre-hackathon baseline lives in upstream repos predating the event
(xnicher v1.0.0 released 2026-09-06, oneearthr v1.0.0 released
2026-09-16; phyloseek-devel/geoseek-devel are unversioned lab code —
see NEW.md); work submitted here was built during Oct 26 – Nov 4, 2026.

## Layout

| Dir | What |
|---|---|
| `pipeline/` | env/jaccard + phy distances, dist_frame, suscept_model, M-masked predict, raster→serve chain |
| `agent/` | Python — Meta Model API client, extraction prompts, APHIS hub client |
| `api/` | FastAPI monitor service |
| `app/` | Map frontend (geoseek pattern) |
| `data/` | Local artifacts (gitignored) |
| `docs/` | `meta-api.md` (API facts/pricing), `data-sources.md` (endpoints) |

## The Factory (nightly autonomous loop)

WildSeek runs as a risk-intelligence factory (`pipeline/nightly.py`,
constitution in `docs/FACTORY.md`):

```
ENTRADA    fetch_aphis -> fresh APHIS detections/species/flocks
SPEC       agent/planner.py -> run_spec.json (delta: new species -> refit)
REFIT      (condicional) labels -> dist_frame -> suscept_model
BUILDER    kuhpc: build_risk -> watershed -> county_exposure -> render -> export
APPROVER   agent/qa.py: quorum 3/4 (codigo/seguridad/ux/brain=spark)
PRODUCCION rsync -> terminal -> wildseek.ecoseek.org + /health
BRAIN      run manifest + glimmer (MI210, local) shift log
SELF-HEAL  stage failure -> `muse exec` patches -> one retry
```

Routine refreshes deploy direct; susceptibility refits flag
`needs_human_release` (PR gate -> public repo via `scripts/release.sh`).
