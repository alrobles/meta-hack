"""Spec writer — turns the nightly data delta into a run spec.

Factory role: reads what changed since the last run (new APHIS
detections, new species, new flock confirmations) and emits
run_spec.json telling the builder which stages to execute:

  stages.risk      — always: fresh detections re-rasterize risk
  stages.labels    — new host species appeared -> re-resolve GBIF labels
  stages.refit     — label set changed -> re-run dist_frame+suscept_model
                     (+ reservoir + risk chain downstream)

Delta is computed against data/serve/detections.geojson from the last
export (the shipped state), not the raw parquet — "what the public map
currently shows" is the baseline that matters.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd


@dataclass
class RunSpec:
    stages: dict = field(default_factory=lambda: {
        "risk": True, "labels": False, "refit": False})
    new_detections: int = 0
    new_species: list = field(default_factory=list)
    new_counties: int = 0
    reasons: list = field(default_factory=list)


def plan(data_dir: Path | str = "data",
         serve_dir: Path | str = "data/serve") -> RunSpec:
    data, serve = Path(data_dir), Path(serve_dir)
    spec = RunSpec()

    sp = data / "aphis_wild_birds_species.parquet"
    mam = data / "aphis_mammals_species.parquet"
    if not sp.exists():
        spec.reasons.append("no species parquet — full build")
        spec.stages["labels"] = spec.stages["refit"] = True
        return spec

    det = pd.concat([
        pd.read_parquet(sp, columns=["state", "county", "bird_species",
                                     "date_detected"]),
        pd.read_parquet(mam, columns=["State", "County", "Species",
                                     "Date_Detected"])
        .rename(columns={"State": "state", "County": "county",
                         "Species": "bird_species",
                         "Date_Detected": "date_detected"})
        if mam.exists() else pd.DataFrame(),
    ])

    # baseline = snapshot of what was seen last run (common names —
    # the layer's native namespace; GBIF names live in host_labels)
    seen_p = data / "detect_seen.json"
    if seen_p.exists():
        seen = set(json.loads(seen_p.read_text()).get("species", []))
        new_sp = sorted(set(det["bird_species"].dropna()) - seen)
    else:
        # first run — snapshot quietly, nothing is "new"
        new_sp = []
    observed = set(det["bird_species"].dropna())
    spec.new_species = new_sp
    spec.new_detections = len(det)

    geo = serve / "detections.geojson"
    if geo.exists():
        old = json.loads(geo.read_text())
        old_n = len(old.get("features", []))
        spec.new_detections = max(0, len(det) - old_n)
        if spec.new_detections > 0:
            spec.reasons.append(f"{spec.new_detections} new detection rows")

    # persist snapshot for the next nightly diff
    seen_p.write_text(json.dumps({
        "n": len(det), "species": sorted(observed)}, indent=1))

    if new_sp:
        spec.stages["labels"] = spec.stages["refit"] = True
        spec.reasons.append(
            f"{len(new_sp)} new host species: {', '.join(new_sp[:6])}")
    if not spec.reasons:
        spec.reasons.append("routine refresh")
    return spec


def main() -> None:
    spec = plan()
    Path("data/run_spec.json").write_text(
        json.dumps(asdict(spec), indent=1, default=str))
    print(json.dumps(asdict(spec), indent=1, default=str))


if __name__ == "__main__":
    main()
