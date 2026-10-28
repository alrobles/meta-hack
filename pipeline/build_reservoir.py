"""Combine per-species suitability x p_host into reservoir-community maps.

Three accumulation rules over the M-masked suit_<key>.tif stack:

  sum  : E[# susceptible hosts]        R(x) = sum_i  p_i * s_i(x)
  max  : strongest single reservoir    R(x) = max_i  p_i * s_i(x)
  prob : P(>=1 susceptible reservoir)  R(x) = 1 - prod_i (1 - p_i*s_i(x))
         computed as 1 - exp( sum_i log1p(-p_i*s_i) )

Pixels outside every species' M stay NaN.
"""
import os
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio

ROOT = Path(os.environ.get("WILDSEEK_BEEG", "."))
SUIT, OUT = ROOT / "suit", ROOT / "reservoir"
OUT.mkdir(exist_ok=True)

p_host = pd.read_parquet(ROOT / "susceptibility.parquet") \
           .set_index("specieskey")["p_host"]
print("species with p_host:", len(p_host), flush=True)

ref = acc_sum = acc_max = acc_log = valid = None
n = skipped = 0
for tif in sorted(SUIT.glob("suit_*.tif")):
    key = int(tif.stem.split("_")[1])
    if key not in p_host.index:
        skipped += 1
        continue
    with rasterio.open(tif) as src:
        s = src.read(1).astype("float32")
        if ref is None:
            ref = src.profile.copy()
            acc_sum = np.zeros(s.shape, "float64")
            acc_max = np.zeros(s.shape, "float32")
            acc_log = np.zeros(s.shape, "float64")
            valid = np.zeros(s.shape, bool)
    v = np.isfinite(s)
    ps = np.where(v, s * p_host.loc[key], 0.0).astype("float32")
    acc_sum += ps
    np.maximum(acc_max, ps, out=acc_max)
    acc_log += np.where(v, np.log1p(-np.clip(ps, 0, 0.999999)), 0.0)
    valid |= v
    n += 1
    if n % 100 == 0:
        print("read", n, flush=True)

prob = 1.0 - np.exp(acc_log)
prof = dict(ref, dtype="float32", nodata=np.nan, compress="deflate")
for arr, name in ((acc_sum, "reservoir_sum.tif"),
                  (acc_max, "reservoir_max.tif"),
                  (prob, "reservoir_prob.tif")):
    out = np.where(valid, arr, np.nan).astype("float32")
    with rasterio.open(OUT / name, "w", **prof) as d:
        d.write(out, 1)
    finite = np.isfinite(out)
    print(name, "min/med/max:",
          round(float(np.nanmin(out)), 3),
          round(float(np.nanmedian(out)), 3),
          round(float(np.nanmax(out)), 3),
          f"({finite.mean() * 100:.1f}% cover)", flush=True)
print("done:", n, "species stacked,", skipped, "skipped (no p_host)")
