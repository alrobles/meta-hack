"""30-yr bioclim normals at 1/120 deg from publish/_YYYY_split.

Reads the per-var global 1/120-deg GeoTIFFs windowed to the active
domain bbox (pipeline/domain.py; WILDSEEK_DOMAIN), accumulates
1996-2025 means per cell, writes:
  env_<dom>_normals_1120.tif      19-band float32 GeoTIFF (predict input)
  env_<dom>_normals_1120.parquet  cell-level table (DuckDB joins)

Grid: 0.0083333 deg, x from -180.0001 (global product).
conus window lon [-125,-66], lat [24,49.5] => ~7080x3060 px;
na_ca (Canada->Panama) => ~14160x9240 px — accumulated per band so
peak memory stays ~1 GB. NaN stays NaN. Runs under SLURM.
"""

import glob
import os
import sys

import numpy as np
import rasterio
from rasterio.windows import from_bounds

OUT = os.environ.get("WILDSEEK_BEEG", ".")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import domain

DOM = domain.name()
SUF = domain.suffix()
_B = domain.get()["bbox"]
LON_MIN, LAT_MIN, LON_MAX, LAT_MAX = _B

_ERA5 = os.environ.get(
    "WILDSEEK_ERA5",
    os.path.join(os.path.dirname(os.path.abspath(OUT)), "era5_bioclim"))
SRC = f"{_ERA5}/publish/_{{y}}_split/bio{{v:02d}}_{{y}}.tif"
YEARS = range(1996, 2026)


def window_for(ref_path: str):
    with rasterio.open(ref_path) as r:
        w = from_bounds(LON_MIN, LAT_MIN, LON_MAX, LAT_MAX, r.transform)
        w = w.round_offsets().round_lengths()
        tr = r.window_transform(w)
        return w, tr


def main() -> None:
    ref = SRC.format(y=YEARS[0], v=1)
    if not os.path.exists(ref):
        cand = sorted(glob.glob(f"{_ERA5}/publish/_*_split/bio01_*.tif"))
        if not cand:
            sys.exit(f"no era5 bioclim tifs under {_ERA5}/publish/")
        ref = cand[0]
    win, tr = window_for(ref)

    profile = dict(
        driver="GTiff", height=win.height, width=win.width, count=19,
        dtype="float32", crs="EPSG:4326", transform=tr,
        nodata=np.nan, compress="deflate",
    )
    tif = f"{OUT}/env_{DOM}_normals_1120.tif"
    # per-band accumulation -> peak memory ~= one window, not 19
    with rasterio.open(tif, "w", **profile) as dst:
        for v in range(1, 20):
            acc = np.zeros((win.height, win.width), np.float64)
            cnt = np.zeros((win.height, win.width), np.int32)
            for y in YEARS:
                f = SRC.format(y=y, v=v)
                if not os.path.exists(f):
                    continue
                with rasterio.open(f) as r:
                    arr = r.read(1, window=win)
                ok = np.isfinite(arr)
                acc[ok] += arr[ok]
                cnt[ok] += 1
            with np.errstate(invalid="ignore"):
                mean = (acc / cnt).astype(np.float32)
            dst.write(mean, v)
            print("bio", v, "done", flush=True)
        dst.descriptions = tuple(f"bio{i:02d}" for i in range(1, 20))
    print("wrote", tif, (19, win.height, win.width))


if __name__ == "__main__":
    main()
