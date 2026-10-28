"""Sub-county HPAI risk surface (1/120-deg CONUS raster).

risk_cell = recency-weighted detection density (Gaussian kernel over
HPAI-positive wild-bird detections) x susceptible-species suitability
sum (once the suitability stack exists). Output is continuous at
1/120 deg — strictly finer than county; county/FIPS aggregates are
derived, not primary.

Inputs:
  aphis_wild_birds.parquet  (local fetch, has lat/lon + date + result)
  env_conus_normals_1120.tif (grid definition)
  suit_stack (optional: dir of per-species suitability parquet/tif)

Runs under SLURM (grid math, not login node).
"""

import os
import sys

import numpy as np
import pandas as pd
import rasterio
from scipy.ndimage import gaussian_filter

OUT = os.environ.get("WILDSEEK_BEEG", ".")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import domain

DOM = domain.name()
HALF_LIFE_D = 90.0          # detection recency half-life (days)
SIGMA_CELLS = 12.0          # ~10 km at 1/120 deg


def main() -> None:
    det = pd.read_parquet(f"{OUT}/aphis_wild_birds.parquet")
    det = det[det["pathogenicity"] == "High Path AI"].dropna(
        subset=["lat", "lon"])
    det["date_collected"] = pd.to_datetime(det["date_collected"])
    age = (det["date_collected"].max() - det["date_collected"]).dt.days
    det["w"] = 0.5 ** (age / HALF_LIFE_D)

    with rasterio.open(f"{OUT}/env_{DOM}_normals_1120.tif") as r:
        tr, H, W = r.transform, r.height, r.width
        nodata_mask = ~np.isfinite(r.read(1))
    col = ((det["lon"] - tr.c) / tr.a).astype(int)
    row = ((det["lat"] - tr.f) / tr.e).astype(int)
    ok = (row >= 0) & (row < H) & (col >= 0) & (col < W)
    surf = np.zeros((H, W), np.float32)
    np.add.at(surf, (row[ok], col[ok]), det["w"].to_numpy()[ok])
    surf = gaussian_filter(surf, SIGMA_CELLS)
    surf[nodata_mask] = np.nan
    if np.nanmax(surf) > 0:
        surf = surf / np.nanmax(surf)          # 0-1 detection pressure

    np.save(f"{OUT}/risk_detect_1120.npy", surf.astype(np.float32))
    prof = dict(driver="GTiff", height=H, width=W, count=1,
                dtype="float32", crs="EPSG:4326", transform=tr,
                nodata=np.nan, compress="deflate")
    with rasterio.open(f"{OUT}/risk_detect_1120.tif", "w", **prof) as d:
        d.write(surf, 1)
    print("risk_detect_1120:", int(ok.sum()), "detections rasterized,",
          "max", float(np.nanmax(surf)))

    # Stage 2: detection pressure x reservoir community.
    # reservoir_sum = sum_i p_host_i * suit_i (all classes, M-masked,
    # built by build_reservoir.py) — replaces the older unweighted
    # Aves-mean suitability. Absent the reservoir map (fresh staging,
    # non-refit chain), fall back to detection-only risk rather than
    # kill the whole stage.
    rsv_path = f"{OUT}/reservoir/reservoir_sum.tif"
    if os.path.exists(rsv_path):
        with rasterio.open(rsv_path) as ds:
            rsv = ds.read(1).astype(np.float32)
            assert rsv.shape == (H, W), \
                f"reservoir grid mismatch {rsv.shape}"
        rsv_norm = rsv / np.nanmax(rsv) if np.nanmax(rsv) > 0 else rsv
        risk = surf * np.where(np.isfinite(rsv_norm), rsv_norm, 0)
    else:
        print("WARN: reservoir_sum.tif absent — risk = detection only "
              "(run build_reservoir.py to enable stage 2)")
        rsv_norm = np.full((H, W), np.nan, np.float32)
        risk = surf.copy()
    if np.nanmax(risk) > 0:
        risk = risk / np.nanmax(risk)
    np.save(f"{OUT}/reservoir_norm_1120.npy", rsv_norm)
    with rasterio.open(f"{OUT}/risk_1120.tif", "w", **prof) as d:
        d.write(risk.astype(np.float32), 1)
    print("risk_1120: detection x reservoir_sum; max",
          float(np.nanmax(risk)))


if __name__ == "__main__":
    main()
