"""PNAS-style incidence feature table for the susceptibility model.

One row per species in the universe (species_winner.csv).  For each
species we summarise its relationship to the *positive* (labelled host)
set on three axes:

  env  — max / mean-top-5 Jaccard similarity of occupied binned
         19-bioclim env cells        (jaccard_env.parquet)
  geo  — max / mean-top-5 Jaccard similarity of M-ecoregion id sets
         (jaccard_geo.parquet)
  phy  — minimum phylogenetic distance to a positive.  Real patristic
         distances come from phy_dist.csv (v6_dated mammal tree, keyed
         by species *names*).  Species with no real pair reaching any
         positive fall back to a taxonomic proxy (deepest shared rank
         / 5: genus .2, family .4, order .6, class .8, else 1.0) and
         are flagged phy_source='taxa_proxy'.

All axes exclude self-pairs, so a lone positive has NaN features (its
own distance/similarity to itself never counts).  Species with no
positive reachable on an axis get NaN + coverage flag False.

Inputs are resolved under --base (default 'data') so the same code runs
against data/ or the beegfs staging dir; every input except the
universe is optional — missing files degrade to empty coverage with a
warning.

    python pipeline/dist_frame.py                       # data/ -> data/dist_frame.parquet
    python pipeline/dist_frame.py --base "$WILDSEEK_BEEG"
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

TOPK = 5
RANKS = ("genus", "family", "order", "class")   # deepest-first
PHY_REAL = "patristic"
PHY_PROXY = "taxa_proxy"
PHY_NONE = "none"


# ---------------------------------------------------------------- io
def _read(path, required=False):
    """Read csv/parquet if present; None + warning otherwise."""
    p = Path(path)
    if not p.exists():
        if required:
            sys.exit(f"[dist_frame] MISSING {p} — required input")
        print(f"[dist_frame] MISSING {p} — axis disabled")
        return None
    df = pd.read_parquet(p) if p.suffix == ".parquet" else pd.read_csv(p)
    print(f"[dist_frame] {p.name}: {len(df)} rows")
    return df


def sym_lookup(df, ki, kj, val, how):
    """Long pair frame -> dict[key][other] = value, both directions.

    `how` 'max' keeps the larger value on duplicate/conflicting pairs
    (similarities), 'min' the smaller (distances).  Self-loops are
    dropped here AND rechecked at query time.
    """
    out = {}
    if df is None:
        return out
    agg = max if how == "max" else min
    for a, b, v in df[[ki, kj, val]].itertuples(index=False):
        if a == b or pd.isna(v):
            continue
        da = out.setdefault(a, {})
        db = out.setdefault(b, {})
        da[b] = agg(v, da[b]) if b in da else v
        db[a] = agg(v, db[a]) if a in db else v
    return out


# ------------------------------------------------------------- inputs
def load_universe(base):
    df = _read(Path(base) / "species_winner.csv", required=True)
    uni = df[["specieskey", "species"]].dropna(subset=["specieskey"]).copy()
    uni["specieskey"] = uni["specieskey"].astype(np.int64)
    return uni.drop_duplicates("specieskey").reset_index(drop=True)


def load_positives(base, uni_keys):
    df = _read(Path(base) / "host_labels.parquet")
    if df is None or "specieskey" not in df:
        return set()
    pos = set(df["specieskey"].dropna().astype(np.int64)) & uni_keys
    print(f"[dist_frame] positives: {len(pos)} "
          f"(of {int(df['specieskey'].notna().sum())} labelled, "
          f"{len(uni_keys)} universe)")
    return pos


def load_phy(base, name2key):
    """phy_dist.csv (species names) -> symmetric dict on specieskeys."""
    df = _read(Path(base) / "phy_dist.csv")
    if df is None:
        return {}
    df = df.copy()
    df["ki"] = df["item1"].map(name2key)
    df["kj"] = df["item2"].map(name2key)
    n_miss = int((df["ki"].isna() | df["kj"].isna()).sum())
    if n_miss:
        print(f"[dist_frame] phy_dist: {n_miss} pairs dropped "
              f"(endpoint not in universe)")
    return sym_lookup(df.dropna(subset=["ki", "kj"]),
                      "ki", "kj", "phylo_distance", "min")


def taxa_proxy_dist(t1, t2):
    """Deepest shared rank index / 5; no shared rank (phylum only) -> 1."""
    for i, (a, b) in enumerate(zip(t1, t2)):          # genus..class
        if a is not None and a == b:
            return (i + 1) / 5.0
    return 1.0


# -------------------------------------------------------------- frame
def build(base, out=None):
    base = Path(base)
    uni = load_universe(base)
    uni_keys = set(uni.specieskey)
    pos = load_positives(base, uni_keys)

    taxa_df = _read(base / "taxonomy.parquet")

    # species NAME -> specieskey, for phy_dist (universe first, taxonomy
    # as backup so a positive outside the name list still resolves)
    nm = uni[["specieskey", "species"]]
    if taxa_df is not None and "species" in taxa_df:
        nm = pd.concat([nm, taxa_df[["specieskey", "species"]]])
    name2key = (nm.dropna()
                  .assign(specieskey=lambda d: d.specieskey.astype(np.int64))
                  .drop_duplicates("species")
                  .set_index("species").specieskey.to_dict())

    env = sym_lookup(_read(base / "jaccard_env.parquet"),
                     "key_i", "key_j", "j_env", "max")
    geo = sym_lookup(_read(base / "jaccard_geo.parquet"),
                     "key_i", "key_j", "j_geo", "max")
    phy = load_phy(base, name2key)

    # specieskey -> (genus, family, order, class), lowercased, NaN -> None
    taxa = {}
    if taxa_df is not None:
        t = taxa_df[["specieskey"] + list(RANKS)].copy()
        t.columns = ["specieskey"] + list(RANKS)
        for r in t.itertuples(index=False):
            if pd.isna(r.specieskey):
                continue
            taxa[np.int64(r.specieskey)] = tuple(
                None if pd.isna(v) else str(v).strip().lower()
                for v in r[1:])

    def sim_feats(key, lookup, others):
        vs = [lookup[key][p] for p in others
              if key in lookup and p in lookup[key]]
        if not vs:
            return np.nan, np.nan, False
        vs.sort(reverse=True)
        return vs[0], float(np.mean(vs[:TOPK])), True

    rows = []
    for key, sp in uni[["specieskey", "species"]].itertuples(index=False):
        others = pos - {key}                       # self-exclusion
        label = int(key in pos)

        max_env, m5_env, has_env = sim_feats(key, env, others)
        max_geo, m5_geo, has_geo = sim_feats(key, geo, others)

        # phy: real pairs win; proxy only when no real pair reaches a
        # positive (birds, mammals missing from the v6 tree).
        real = [phy[key][p] for p in others
                if key in phy and p in phy[key]]
        if real:
            d_phy, phy_src, has_phy = min(real), PHY_REAL, True
        else:
            t1 = taxa.get(key)
            prox = [taxa_proxy_dist(t1, taxa[p]) for p in others
                    if t1 is not None and p in taxa]
            if prox:
                d_phy, phy_src, has_phy = min(prox), PHY_PROXY, True
            else:
                d_phy, phy_src, has_phy = np.nan, PHY_NONE, False

        rows.append((key, sp, label, max_env, max_geo, d_phy,
                     m5_env, m5_geo, phy_src, has_env, has_geo, has_phy))

    frame = pd.DataFrame(rows, columns=[
        "specieskey", "species", "label",
        "max_j_env_pos", "max_j_geo_pos", "min_d_phy_pos",
        "mean_j_env_pos5", "mean_j_geo_pos5",
        "phy_source", "has_env", "has_geo", "has_phy"])

    # ---- sanity: no feature may be a self-reference
    pos_rows = frame[frame.label == 1]
    if len(pos_rows) == 1:
        r = pos_rows.iloc[0]
        assert r[["max_j_env_pos", "max_j_geo_pos", "min_d_phy_pos"]].isna().all(), \
            "lone positive has non-NaN feature — self-pair leaked"
    if len(pos) <= 1:
        assert not (frame.min_d_phy_pos == 0).any(), \
            "min_d_phy_pos == 0 — self-distance leaked"

    out = Path(out) if out else base / "dist_frame.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(out, index=False)

    print(f"[dist_frame] wrote {out}  ({len(frame)} rows)")
    print(f"  label=1: {int(frame.label.sum())}   "
          f"has_env: {int(frame.has_env.sum())}  "
          f"has_geo: {int(frame.has_geo.sum())}  "
          f"has_phy: {int(frame.has_phy.sum())}")
    print(f"  phy_source: {frame.phy_source.value_counts().to_dict()}")
    for c in ("max_j_env_pos", "max_j_geo_pos", "min_d_phy_pos"):
        s = frame[c]
        print(f"  {c}: min/med/max = {s.min():.3f} / {s.median():.3f} / "
              f"{s.max():.3f}  (NaN {int(s.isna().sum())})")
    return frame


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="data",
                    help="input dir (data/ or beegfs staging dir)")
    ap.add_argument("--out", default=None,
                    help="output parquet (default <base>/dist_frame.parquet)")
    a = ap.parse_args()
    build(a.base, a.out)


if __name__ == "__main__":
    main()
