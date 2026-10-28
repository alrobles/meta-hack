"""Aggregate risk_detect_1120.tif to HUC8 watersheds -> risk_watershed.parquet.

Zonal stats via rasterize + bincount (no rasterstats dep). Also counts
weighted detections per watershed for the briefing layer.
"""

import json
import os

import numpy as np
import pandas as pd
import rasterio
from rasterio.features import rasterize

OUT = os.environ.get("WILDSEEK_BEEG", ".")


def main() -> None:
    gj = json.load(open(f"{OUT}/wbd_huc8_conus.geojson"))
    feats = gj["features"]
    shapes = [(f["geometry"], i) for i, f in enumerate(feats)
              if f.get("geometry")]

    src = (f"{OUT}/risk_1120.tif" if os.path.exists(f"{OUT}/risk_1120.tif")
           else f"{OUT}/risk_detect_1120.tif")
    with rasterio.open(src) as ds:
        risk = ds.read(1)
        tr, H, W = ds.transform, ds.height, ds.width
    print("risk source:", src)

    idx = rasterize(shapes, out_shape=(H, W), transform=tr,
                    fill=-1, dtype="int32")
    ok = idx >= 0
    r = np.where(np.isfinite(risk), risk, np.nan)

    code = idx[ok]
    vals = r[ok]
    finite = np.isfinite(vals)
    code_f, vals_f = code[finite], vals[finite]

    n = len(feats)
    cnt = np.bincount(code_f, minlength=n)
    ssum = np.bincount(code_f, weights=vals_f, minlength=n)
    smax = np.full(n, -np.inf)
    np.maximum.at(smax, code_f, vals_f)

    det = pd.read_parquet(f"{OUT}/aphis_wild_birds.parquet")
    det = det[det["pathogenicity"] == "High Path AI"].dropna(
        subset=["lat", "lon"])
    dcol = ((det["lon"] - tr.c) / tr.a).astype(int)
    drow = ((det["lat"] - tr.f) / tr.e).astype(int)
    din = (drow >= 0) & (drow < H) & (dcol >= 0) & (dcol < W)
    dc = idx[drow[din].to_numpy(), dcol[din].to_numpy()]
    detn = np.bincount(dc[dc >= 0], minlength=n)

    rows = []
    for i, f in enumerate(feats):
        p = f["properties"]
        rows.append({
            "huc8": p.get("huc8"), "name": p.get("name"),
            "states": p.get("states"),
            "mean_risk": float(ssum[i] / cnt[i]) if cnt[i] else None,
            "max_risk": float(smax[i]) if np.isfinite(smax[i]) else None,
            "n_det": int(detn[i]),
            "n_cells": int(cnt[i]),
        })
    df = pd.DataFrame(rows)
    df["risk"] = (df["mean_risk"].fillna(0)
                  * np.log1p(df["n_det"].clip(upper=500)))
    df["risk"] = df["risk"] / df["risk"].max()
    df.to_parquet(f"{OUT}/risk_watershed.parquet", index=False)
    print(df.sort_values("risk", ascending=False).head(10)[
        ["name", "states", "mean_risk", "n_det", "risk"]])
    print("wrote", len(df))


if __name__ == "__main__":
    main()
