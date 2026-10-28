"""eco_id raster on the CONUS 1/120-deg grid, from the 1/30-deg
eco_raster_130 bins -> int32 tif aligned to env_conus_normals_1120.

Bin lookup via direct array indexing (no merge): bins are small ints
(lon_bin < 10800, lat_bin < 5400), so a dense int32 LUT is ~230MB and
O(1) per query.
"""
import os
import sys

import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import from_bounds

OUT = os.environ.get("WILDSEEK_BEEG", ".")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import domain

DOM = domain.name()


def main():
    eco = pd.read_csv(f"{OUT}/eco_raster_130.csv.gz",
                      usecols=["lon_bin", "lat_bin", "eco_id"])
    lut = np.full((10801, 5401), -1, np.int32)
    lut[eco.lon_bin.to_numpy(), eco.lat_bin.to_numpy()] = \
        eco.eco_id.to_numpy()
    print("eco cells:", int((lut >= 0).sum()))

    with rasterio.open(f"{OUT}/env_{DOM}_normals_1120.tif") as ds:
        H, W, bounds = ds.height, ds.width, ds.bounds
        prof = ds.profile.copy()

    tr = from_bounds(*bounds, W, H)
    rows, cols = np.mgrid[0:H, 0:W]
    lons = tr.c + (cols + 0.5) * tr.a
    lats = tr.f + (rows + 0.5) * tr.e   # tr.e negative
    # floor, matching eco_raster_130 convention floor((coord+90)*30) on
    # cell edges — round() on centers pushes the top fine-cell of each
    # 1/30 band into the next bin
    lb = np.clip(np.floor((lats + 90) * 30).astype(np.int32), 0, 5400)
    ob = np.clip(np.floor((lons + 180) * 30).astype(np.int32), 0, 10800)
    out = lut[ob, lb]
    print("matched frac:", float((out >= 0).mean()))

    prof.update(count=1, dtype="int32", nodata=-1)
    with rasterio.open(f"{OUT}/eco_{DOM}_1120.tif", "w", **prof) as ds:
        ds.write(out, 1)
    print(f"wrote eco_{DOM}_1120.tif", out.shape)


if __name__ == "__main__":
    main()
