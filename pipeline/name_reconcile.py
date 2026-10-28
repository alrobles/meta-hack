"""Reconcile GBIF species names -> tree tip labels.

The susceptibility catalog uses GBIF backbone names; the phylogenies use
their own taxonomies (BirdTree/Ericson for birds, MDD-style tips on the
v6 dated mammal tree). Genus transfers and splits/lumps leave species
unmapped -> they fall back to phy_source="taxa_proxy".

This script resolves what it can and writes the synonym maps consumed by
pipeline/phy_dist_birds.R (data/bird_syn_map.csv: ,tip keyed on our name)
and pipeline/phy_dist.R (data/mammal_syn_map.csv: species,tip).

Resolution order, birds:
  1. direct match on BLIOCPhyloMasterTax Scientific / TipLabel
  2. AVONET chain: name -> eBird Species2 / BirdLife Species1
     -> BirdTree Species3 (crosswalk sheets of AVONET Suppl. 1)
  3. multiple candidates -> prefer the one sharing our epithet
  4. MANUAL_BIRD dict for residual splits/lumps
Mammals:
  1. direct match on v6 tips
  2. unique-epithet match among tips
  3. MANUAL_MAMMAL dict

Hybrids ("x" in name) and unverifiable entities stay proxy on purpose.

Usage: .venv/bin/python pipeline/name_reconcile.py
Outputs + a coverage report on stdout.
"""
import os
import re
import sys
from collections import defaultdict

import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = f"{REPO}/data"
BIRD_DIR = os.environ.get("WILDSEEK_BIRDTREE_DIR", f"{DATA}/birdtree")
AVONET = os.environ.get("WILDSEEK_AVONET", f"{BIRD_DIR}/avonet1.xlsx")
MAMMAL_TREE = os.environ.get("WILDSEEK_MAMMAL_TREE", f"{DATA}/v6_point.nwk")

# splits/lumps the crosswalks cannot resolve 1:1 -> chosen tip on BirdTree
# (post-2014 splits map to the pre-split lump; CONUS Yellow Warbler = aestiva)
MANUAL_BIRD = {
    "Tyto furcata": "Tyto_alba",
    "Saurothera merlini": "Coccyzus_merlini",
    "Numenius hudsonicus": "Numenius_phaeopus",
    "Setophaga aestiva": "Dendroica_aestiva",
    "Setophaga petechia": "Dendroica_aestiva",
    "Thalasseus acuflavidus": "Sterna_sandvicensis",
    "Pyrocephalus obscurus": "Pyrocephalus_rubinus",
    "Sturnella lilianae": "Sturnella_magna",
    "Oceanodroma microsoma": "Halocyptena_microsoma",
    "Aphelocoma woodhouseii": "Aphelocoma_californica",
    "Acanthis flammea": "Carduelis_flammea",
    "Acanthis hornemanni": "Carduelis_hornemanni",
}

# name variants on the v6 mammal tree (MDD reclassifications, orthography,
# pre-split lumps). Absent species (Cryptotis parva, Ammospermophilus
# nelsoni, Sciurus alleni) stay proxy - distant congeners would mislead.
MANUAL_MAMMAL = {
    "Alces americanus": "Alces_alces",
    "Martes caurina": "Martes_americana",
    "Canis lycaon": "Canis_lupus",
    "Tursiops erebennus": "Tursiops_truncatus",
    "Erethizon dorsatus": "Erethizon_dorsatum",
    "Sciurus griseus": "Hesperosciurus_griseus",
    "Tamias speciosus": "Neotamias_speciosus",
    "Thomomys bottae": "Megascapheus_bottae",
    "Aeorestes cinereus": "Lasiurus_cinereus",
    "Mustela frenata": "Neogale_frenata",
}


def proxy_species(cls):
    df = pd.read_parquet(f"{DATA}/serve/susceptibility.parquet")
    tx = pd.read_parquet(f"{DATA}/taxonomy.parquet")
    m = df.merge(tx[["specieskey", "class"]], on="specieskey", how="left")
    return m[(m.phy_source == "taxa_proxy") & (m["class"] == cls)][
        "species"
    ].tolist()


def by_epithet(tips):
    ep = defaultdict(list)
    for t in tips:
        p = t.split("_")
        if len(p) >= 2:
            ep[p[-1].lower()].append(t)
    return ep


def avonet_maps():
    """(eBird->BirdLife, BirdLife->[BirdTree]) from AVONET Suppl. 1."""
    xl = pd.ExcelFile(AVONET)
    bleb = pd.read_excel(xl, "BirdLife-eBird crosswalk")
    blbt = pd.read_excel(xl, "BirdLife–BirdTree crosswalk")
    eb2bl = dict(zip(bleb.Species2, bleb.Species1))
    bl2bt = defaultdict(set)
    for _, r in blbt.iterrows():
        bl2bt[r.Species1].add(r.Species3)
    return eb2bl, bl2bt


def reconcile_birds(unmapped):
    bt = pd.read_csv(f"{BIRD_DIR}/BLIOCPhyloMasterTax.csv")
    tips = set(bt.TipLabel)
    sci2tip = dict(zip(bt.Scientific, bt.TipLabel))
    eb2bl, bl2bt = avonet_maps()
    epi = by_epithet(tips)
    out, report = {}, []
    for name in unmapped:
        if " x " in name or name.startswith("x "):
            report.append((name, "-", "hybrid")); continue
        if name in MANUAL_BIRD:
            t = MANUAL_BIRD[name]
            ok = t in tips
            if ok: out[name] = t
            report.append((name, t if ok else "-", "manual" if ok else "manual-miss"))
            continue
        cand = set()
        if name in sci2tip:
            cand.add(sci2tip[name])
        t0 = name.replace(" ", "_")
        if t0 in tips:
            cand.add(t0)
        bls = {eb2bl[name]} if name in eb2bl else set()
        if name in bl2bt:
            bls.add(name)
        for b in bls:
            for s3 in bl2bt.get(b, ()):  # BirdLife -> BirdTree species
                t = s3.replace(" ", "_")
                if t in tips:
                    cand.add(t)
        if not cand:
            cand.update(epi.get(name.split()[-1].lower(), ()))
        if len(cand) > 1:
            pref = [c for c in cand if c.split("_")[-1].lower()
                    == name.split()[-1].lower()]
            if len(pref) == 1:
                cand = set(pref)
        if len(cand) == 1:
            out[name] = cand.pop()
            report.append((name, out[name], "auto"))
        elif cand:
            report.append((name, "|".join(sorted(cand)), "ambiguous"))
        else:
            report.append((name, "-", "unresolved"))
    return out, report


def reconcile_mammals(unmapped):
    raw = open(MAMMAL_TREE).read()
    tips = set(re.findall(r"([A-Z][a-z]+_[a-z_]+)(?=:)", raw))
    epi = by_epithet(tips)
    out, report = {}, []
    for name in unmapped:
        if name in MANUAL_MAMMAL:
            t = MANUAL_MAMMAL[name]
            ok = t in tips
            if ok: out[name] = t
            report.append((name, t if ok else "-", "manual" if ok else "manual-miss"))
            continue
        t0 = name.replace(" ", "_")
        if t0 in tips:
            out[name] = t0
            report.append((name, t0, "auto"))
            continue
        cand = epi.get(name.split()[-1].lower(), [])
        if len(cand) == 1:
            out[name] = cand[0]
            report.append((name, cand[0], "auto-epithet"))
        elif cand:
            report.append((name, "|".join(sorted(cand)), "ambiguous"))
        else:
            report.append((name, "-", "unresolved"))
    return out, report


def write_bird_map(resolved):
    path = f"{DATA}/bird_syn_map.csv"
    old = pd.read_csv(path, index_col=0) if os.path.exists(path) \
        else pd.DataFrame(columns=["tip"])
    new = pd.Series(resolved, name="tip").to_frame()
    merged = pd.concat([old[~old.index.isin(new.index)], new])
    merged.sort_index().to_csv(path, index_label="")
    return path, len(merged)


def write_mammal_map(resolved):
    path = f"{DATA}/mammal_syn_map.csv"
    pd.Series(resolved, name="tip").rename_axis("species") \
        .reset_index().to_csv(path, index=False)
    return path, len(resolved)


def main():
    birds = proxy_species("Aves")
    mam = proxy_species("Mammalia")
    print(f"proxy: {len(birds)} aves, {len(mam)} mammalia")

    rb, rep_b = reconcile_birds(birds)
    rm, rep_m = reconcile_mammals(mam)

    p, n = write_bird_map(rb)
    print(f"\nwrote {p} ({n} rows)")
    p, n = write_mammal_map(rm)
    print(f"wrote {p} ({n} rows)")

    for label, rep in [("AVES", rep_b), ("MAMMALIA", rep_m)]:
        bad = [r for r in rep if r[2] not in ("auto", "auto-epithet", "manual")]
        if bad:
            print(f"\n{label} pendientes ({len(bad)}):")
            for name, tip, how in bad:
                print(f"  {name:35s} {tip:40s} {how}")
    fixed = len(rb) + len(rm)
    print(f"\nresueltos: {fixed}/{len(birds)+len(mam)} "
          f"-> re-run phy_dist*.R, dist_frame, suscept_model")


if __name__ == "__main__":
    sys.exit(main())
