"""Render GeoTIFF -> color PNG + bbox meta for the app (cluster, rasterio).

Usage:
  python render_png.py risk.tif risk.png --cmap turbo --vmax 1
  python render_png.py suit/suit_123.tif suit_123.png
Writes <png>.meta.json {"bbox": [[w,n],[e,n],[e,s],[w,s]]} next to the PNG.
"""

import argparse
import json
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image


def cmap_ramp(v: np.ndarray, name: str) -> np.ndarray:
    from matplotlib import cm
    return (cm.get_cmap(name)(v) * 255).astype("uint8")


def render(tif, png, cmap, vmax):
    with rasterio.open(tif) as ds:
        a = ds.read(1, masked=True).astype("float32")
        b = ds.bounds
    lo = np.nanmin(a) if a.count() else 0
    hi = vmax or (np.nanmax(a) if a.count() else 1)
    v = np.clip((a - lo) / (hi - lo + 1e-9), 0, 1).filled(0)
    rgba = cmap_ramp(v, cmap)
    rgba[..., 3] = np.where(a.mask, 0, 220).astype("uint8")
    Image.fromarray(rgba).save(png)
    meta = {"bbox": [[b.left, b.top], [b.right, b.top],
                     [b.right, b.bottom], [b.left, b.bottom]]}
    Path(png + ".meta.json").write_text(json.dumps(meta))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("tif")
    p.add_argument("png")
    p.add_argument("--cmap", default="inferno")
    p.add_argument("--vmax", type=float, default=None)
    a = p.parse_args()
    render(a.tif, a.png, a.cmap, a.vmax)
