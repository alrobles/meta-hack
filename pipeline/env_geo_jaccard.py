"""Jaccard similarities between species on two axes:

  env  — footprint = set of occupied cells in binned 19-bioclim space
         (each axis -> N_BINS quantile bins over the CONUS cube; a species'
         occurrence env values map to bin-tuples, hashed to ints)
  geo  — footprint = set of M ecoregion ids (95% of presences)

Outputs species_x_species similarity frames (long format, only pairs with
J>0 to keep size sane) + per-species footprint sizes. Runs on beegfs.

    python3 env_geo_jaccard.py            # both axes
    python3 env_geo_jaccard.py geo        # geo only (fast)
"""
import os
import sys

import numpy as np
import pandas as pd

OUT = os.environ.get("WILDSEEK_BEEG", ".")
N_BINS = 16          # quantile bins per bioclim axis
MIN_PAIR_J = 0.0     # keep all pairs; threshold later


def bio_edges():
    """Quantile edges for bio01..19 over the CONUS normals cube."""
    qs = np.linspace(0.0625, 0.9375, N_BINS - 1)
    edges = {}
    cols = [f"bio{i:02d}" for i in range(1, 20)]
    df = pd.read_parquet(f"{OUT}/env_normals_96_25.parquet", columns=cols)
    for c in cols:
        edges[c] = np.asarray(df[c].quantile(qs), dtype=float)
    return edges


def env_footprints(cls, edges):
    """specieskey -> frozenset of occupied env-cell hashes."""
    bios = [f"bio{i:02d}" for i in range(1, 20)]
    df = pd.read_parquet(f"{OUT}/env_occ_{cls}.parquet",
                         columns=["specieskey"] + bios)
    e = np.stack([edges[b] for b in bios])          # (19, 15)
    x = df[bios].to_numpy(dtype=float)
    codes = np.empty(x.shape, np.int16)
    for j in range(19):
        codes[:, j] = np.clip(np.searchsorted(e[j], x[:, j]), 0, N_BINS - 1)
    keys = codes[:, 0].astype(np.int64)
    for j in range(1, 19):
        keys = keys * N_BINS + codes[:, j]
    df["cell"] = keys
    return df.groupby("specieskey")["cell"].agg(set).rename("env")


def geo_footprints():
    """specieskey -> frozenset of M ecoregion ids."""
    m = pd.read_parquet(f"{OUT}/m_ecoregions.parquet",
                        columns=["specieskey", "eco_id"])
    return m.groupby("specieskey")["eco_id"].agg(set).rename("geo")


def jaccard_pairs(footprints, name):
    """Long-format Jaccard over all species pairs (brute force is fine
    for ~1.1k species with set ops)."""
    sp = list(footprints.index)
    sets = footprints.tolist()
    n = len(sp)
    rows = []
    for i in range(n):
        a = sets[i]
        if not a:
            continue
        for j in range(i + 1, n):
            b = sets[j]
            if not b:
                continue
            inter = len(a & b)
            if inter == 0:
                continue
            jac = inter / (len(a) + len(b) - inter)
            if jac > MIN_PAIR_J:
                rows.append((sp[i], sp[j], jac))
    out = pd.DataFrame(rows, columns=["key_i", "key_j", f"j_{name}"])
    print(f"j_{name}: {len(out)} nonzero pairs over {n} species")
    return out


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "both"

    if which in ("geo", "both"):
        geo = geo_footprints()
        geo.to_frame().to_parquet(f"{OUT}/geo_footprints.parquet")
        jaccard_pairs(geo, "geo").to_parquet(
            f"{OUT}/jaccard_geo.parquet", index=False)

    if which in ("env", "both"):
        edges = bio_edges()
        np.save(f"{OUT}/bio_edges.npy", edges)
        fps = []
        for cls in ("aves", "mammalia"):
            fps.append(env_footprints(cls, edges))
        envf = pd.concat(fps)
        envf.to_frame().to_parquet(f"{OUT}/env_footprints.parquet")
        jaccard_pairs(envf, "env").to_parquet(
            f"{OUT}/jaccard_env.parquet", index=False)


if __name__ == "__main__":
    main()
