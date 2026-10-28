"""County supply-chain exposure: mean risk raster per county x
APHIS flock-confirmation severity -> exposure table + geojson.

Runs under SLURM (reads the 1/120-deg risk grid on beegfs).
"""
import json
import os

import numpy as np
import pandas as pd
import rasterio
from rasterio.features import rasterize

OUT = os.environ.get("WILDSEEK_BEEG", ".")


def main():
    risk_src = (f"{OUT}/risk_1120.tif" if os.path.exists(f"{OUT}/risk_1120.tif")
                else f"{OUT}/risk_detect_1120.tif")
    with rasterio.open(risk_src) as ds:
        risk = ds.read(1)
        tr = ds.transform
    risk = np.where(np.isfinite(risk), risk, 0.0)
    print("risk source:", risk_src)

    gj = json.load(open(f"{OUT}/cb_counties_conus.geojson"))
    shapes = [(f["geometry"], i) for i, f in enumerate(gj["features"])
              if f.get("geometry")]
    idx = rasterize(shapes, out_shape=risk.shape, transform=tr,
                    fill=-1, dtype="int32")
    ok = idx >= 0
    sums = np.bincount(idx[ok].ravel(), weights=risk[ok].ravel(),
                       minlength=len(shapes))
    cnts = np.bincount(idx[ok].ravel(), minlength=len(shapes))
    maxs = np.zeros(len(shapes))
    np.maximum.at(maxs, idx[ok].ravel(), risk[ok].ravel())

    rows = []
    for i, f in enumerate(gj["features"]):
        p = f["properties"]
        rows.append({
            "geoid": p["GEOID"], "name": p["NAME"],
            "state_fips": p["STATEFP"], "state": p.get("STUSPS", ""),
            "mean_risk": float(sums[i] / max(cnts[i], 1)),
            "max_risk": float(maxs[i]),
            "n_cells": int(cnts[i]),
        })
    cty = pd.DataFrame(rows)

    fl = pd.read_parquet(f"{OUT}/aphis_poultry.parquet")
    agg = (fl.groupby("county_fips")
             .agg(events=("birds_affected", "size"),
                  birds_affected=("birds_affected", "sum"),
                  last_confirmed=("confirmed", "max"))
             .reset_index())
    agg["geoid"] = agg["county_fips"].astype("Int64").astype(str).str.zfill(5)
    cty = cty.merge(agg, on="geoid", how="left")
    cty["events"] = cty["events"].fillna(0).astype(int)
    cty["birds_affected"] = cty["birds_affected"].fillna(0)
    # exposure = mean risk x log severity of past flock impact
    cty["exposure"] = cty["mean_risk"] * np.log1p(cty["birds_affected"])
    cty.to_parquet(f"{OUT}/county_exposure.parquet", index=False)
    print(cty.nlargest(10, "exposure")[
        ["name", "state", "mean_risk", "birds_affected", "exposure"]]
        .to_string(index=False))

    # joined geojson for the choropleth
    keep = cty.set_index("geoid")
    feats = []
    for f in gj["features"]:
        p = f["properties"]
        g = p["GEOID"]
        if g not in keep.index:
            continue
        r = keep.loc[g]
        feats.append({"type": "Feature",
                      "properties": {
                          "geoid": g, "name": p["NAME"], "state": r["state"],
                          "mean_risk": round(float(r["mean_risk"]), 5),
                          "events": int(r["events"]),
                          "birds_affected": float(r["birds_affected"]),
                          "exposure": round(float(r["exposure"]), 5)},
                      "geometry": f["geometry"]})
    with open(f"{OUT}/county_exposure.geojson", "w") as fh:
        json.dump({"type": "FeatureCollection", "features": feats}, fh)
    print("counties:", len(feats), "->", f"{OUT}/county_exposure.geojson")


if __name__ == "__main__":
    main()
