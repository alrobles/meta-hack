"""Render GeoTIFF -> Web-Mercator-aligned PNG + lat/lon corner meta.

MapLibre image sources stretch the image linearly between four corner
coordinates — it does not reproject the interior. Feeding an EPSG:4326
raster therefore misaligns by up to ~1-2% inside the extent. Warping to
EPSG:3857 first makes the linear stretch exact against the Mercator
basemap.

Usage:
  python render_3857.py in.tif out.png --cmap turbo --vmax 1
Writes <png>.meta.json {"bbox": [TL,TR,BR,BL] in EPSG:4326 degrees}.
"""
import argparse
import json
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.crs import CRS
from rasterio.transform import array_bounds
from rasterio.warp import (
    Resampling,
    calculate_default_transform,
    reproject,
    transform_bounds,
)


def render(tif, png, cmap, vmax):
    from matplotlib import cm
    with rasterio.open(tif) as src:
        dst_crs = CRS.from_epsg(3857)
        tr, w, h = calculate_default_transform(
            src.crs, dst_crs, src.width, src.height, *src.bounds)
        data = np.full((h, w), np.nan, dtype="float32")
        reproject(
            rasterio.band(src, 1), data,
            src_transform=src.transform, src_crs=src.crs,
            dst_transform=tr, dst_crs=dst_crs,
            resampling=Resampling.bilinear,
            src_nodata=src.nodata, dst_nodata=np.nan)
        left, bottom, right, top = array_bounds(h, w, tr)

    lo = np.nanmin(data) if np.isfinite(data).any() else 0
    hi = vmax or (np.nanmax(data) if np.isfinite(data).any() else 1)
    v = np.clip((data - lo) / (hi - lo + 1e-9), 0, 1)
    rgba = (cm.get_cmap(cmap)(np.nan_to_num(v)) * 255).astype("uint8")
    rgba[..., 3] = np.where(np.isfinite(data), 220, 0).astype("uint8")
    Image.fromarray(rgba).save(png)

    # corners back to lat/lon for the MapLibre image source
    w0, s0, e0, n0 = transform_bounds(
        CRS.from_epsg(3857), CRS.from_epsg(4326),
        left, bottom, right, top)
    meta = {"bbox": [[w0, n0], [e0, n0], [e0, s0], [w0, s0]]}
    Path(png + ".meta.json").write_text(json.dumps(meta))
    print(png, "shape", data.shape, "max %.3g" % hi)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("tif"); p.add_argument("png")
    p.add_argument("--cmap", default="inferno")
    p.add_argument("--vmax", type=float, default=None)
    a = p.parse_args()
    render(a.tif, a.png, a.cmap, a.vmax)
