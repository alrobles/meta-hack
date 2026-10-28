"""Resolve APHIS wild-bird common names -> GBIF taxon keys -> labels.

    python3 pipeline/species_labels.py        (run from repo root)

GBIF's /species/match only indexes scientific names, so common names are
resolved via /species/search (full-text, includes vernacular names)
restricted to Aves (highertaxon_key=212) and verified by exact
vernacular-name equality after normalization (case / possessive 's /
punctuation-insensitive -- handles "Harris hawk" vs "Harris's Hawk",
"Ross's goose", hyphenated names).

Candidate ranking for species names:
  extant accepted species > other species-level (synonym/subspecies ->
  speciesKey) > higher taxa.  Extinct (fossil) taxa are rejected and
  hybrids deprioritised (GBIF carries fossil lookalikes, e.g. Aquila
  bivia "golden eagle", Pandion lovensis "osprey", and hybrid taxa such
  as Anas rubripes x platyrhynchos "American black duck").

"X (unidentified)" names are mapped to the matching extant
genus/family/order rather than guessing a species; if several genera
match, the family/order wins.

Writes data/host_labels.parquet:
  common_name, specieskey, species (gbif canonical), class, confidence,
  match_type, n_detections
(Unresolved names are kept with specieskey=NA and reported on stdout.)
"""
import re
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests

SEARCH = "https://api.gbif.org/v1/species/search"
USAGE = "https://api.gbif.org/v1/species"
AVES_KEY = 212      # highertaxon_key restricting search to birds
MAMMALIA_KEY = 359  # mammals (HPAI mammal-detections layer)
GROUP_RANKS = ("GENUS", "FAMILY", "ORDER")
UNID = re.compile(r"\s*\(unidentified\)\s*$", re.IGNORECASE)


def norm(s):
    """Lowercase, drop possessive 's and every non-alphanumeric char."""
    s = (s or "").lower().replace("\u2019", "'")
    s = re.sub(r"'s\b", "", s)
    return re.sub(r"[^a-z0-9]", "", s)


def variants(name):
    """Fallback query strings: stripped base, drop 1st word, drop last."""
    vs = [name]
    base = re.sub(r"\s*\(.*?\)\s*$", "", name).strip()
    if base != name:
        vs.append(base)
    w = base.split()
    if len(w) >= 3:
        vs.append(" ".join(w[1:]))      # "american wood stork" -> "wood stork"
    if len(w) >= 2:
        vs.append(" ".join(w[:-1]))     # "redhead duck" -> "redhead"
    return vs


def hybridish(x):
    sn = f"{x.get('scientificName') or ''} {x.get('canonicalName') or ''}"
    return bool(re.search(r"\s(?:x|×)\s|\bhybrid\b", sn, re.IGNORECASE))


def get_json(url, params=None, tries=3):
    for i in range(tries):
        try:
            r = requests.get(url, params=params, timeout=20)
            if r.status_code == 200:
                return r.json()
        except requests.RequestException:
            pass
        time.sleep(0.5 * (i + 1))
    return {}


def candidates(query, clade_key=AVES_KEY):
    """Clade taxa whose vernacular names include *query* exactly (normed)."""
    j = get_json(SEARCH, {"q": query, "highertaxon_key": clade_key,
                          "limit": 50})
    nq = norm(query)
    return [x for x in j.get("results", [])
            if any(norm(v.get("vernacularName")) == nq
                   for v in x.get("vernacularNames", []))]


def species_score(x):
    """Lower is better; extinct taxa are never acceptable."""
    if x.get("extinct"):
        return 99
    if hybridish(x):
        return 8
    rk, st = x.get("rank", ""), x.get("taxonomicStatus", "")
    if rk == "SPECIES" and st == "ACCEPTED":
        return 0
    if rk == "SPECIES":
        return 1
    if rk in ("SUBSPECIES", "VARIETY", "FORM"):
        return 2
    return 3  # higher ranks: last resort


def pick_species(query, clade_key=AVES_KEY):
    cands = candidates(query, clade_key)
    if not cands:
        return None
    cands.sort(key=species_score)
    x = cands[0]
    if species_score(x) >= 99:
        return None  # only fossils/hybrids matched
    # synonyms & infraspecies -> the species they belong to
    key = x.get("speciesKey") or x.get("acceptedUsageKey") or x.get("key")
    sc = species_score(x)
    conf = {0: 100, 1: 95, 2: 95, 3: 70}.get(sc, 60)
    return key, conf


def pick_group(query, clade_key=AVES_KEY):
    """'X (unidentified)' -> extant genus/family/order, never a species."""
    cands = [x for x in candidates(query, clade_key)
             if not x.get("extinct") and x.get("rank") in GROUP_RANKS]
    if not cands:
        return None
    accepted = [x for x in cands if x.get("taxonomicStatus") == "ACCEPTED"]
    genera = [x for x in accepted if x.get("rank") == "GENUS"]
    if len({x.get("key") for x in genera}) == 1:
        return genera[0].get("key"), 80
    if len(genera) > 1:  # ambiguous across genera -> coarser rank
        for rk in ("FAMILY", "ORDER"):
            pool = [x for x in accepted if x.get("rank") == rk]
            if pool:
                return pool[0].get("key"), 75
        return genera[0].get("key"), 75
    for rk in GROUP_RANKS:  # no accepted genus: any accepted, then doubtful
        pool = [x for x in accepted if x.get("rank") == rk]
        if pool:
            return pool[0].get("key"), 75
    return cands[0].get("key"), 60


def resolve(name, clade_key=AVES_KEY):
    unid = bool(UNID.search(name))
    base = UNID.sub("", name).strip()
    queries = [base] if unid else variants(name)
    for i, q in enumerate(queries):
        hit = (pick_group(q, clade_key) if unid
               else pick_species(q, clade_key))
        if hit:
            key, conf = hit
            info = get_json(f"{USAGE}/{key}")
            return {
                "common_name": name,
                "specieskey": key,
                "species": info.get("scientificName"),
                "class": info.get("class"),
                "confidence": conf if i == 0 else conf - 5,
                "match_type": ("GROUP" if unid else
                               "VERNACULAR_EXACT" if i == 0 else
                               "VERNACULAR_VARIANT"),
            }
    return {"common_name": name, "specieskey": pd.NA, "species": None,
            "class": None, "confidence": 0, "match_type": "NONE"}


def main():
    det = pd.read_parquet("data/aphis_wild_birds_species.parquet")
    names = det.bird_species.value_counts()
    jobs = [(n, AVES_KEY) for n in names.index]
    mam = pd.read_parquet("data/aphis_mammals_species.parquet") \
        if pd.io.common.file_exists("data/aphis_mammals_species.parquet") \
        else None
    if mam is not None:
        mnames = mam.Species.value_counts()
        jobs += [(n, MAMMALIA_KEY) for n in mnames.index]
        names = pd.concat([names, mnames])
    with ThreadPoolExecutor(8) as ex:
        rows = list(ex.map(lambda nj: resolve(*nj), jobs))
    lab = pd.DataFrame(rows)
    lab["n_detections"] = lab.common_name.map(names).astype("int64")
    lab["specieskey"] = lab.specieskey.astype("Int64")
    lab.to_parquet("data/host_labels.parquet", index=False)

    resolved = lab[lab.specieskey.notna()]
    print(f"resolved: {len(resolved)} / {len(lab)}")
    print("classes:", resolved["class"].value_counts().to_dict())
    un = lab[lab.specieskey.isna()]
    print(f"unresolved ({len(un)}):")
    for _, r in un.iterrows():
        print(f"  - {r.common_name} (n_detections={r.n_detections})")

    win = pd.read_csv("data/species_winner.csv")
    hit = resolved[resolved.specieskey.isin(win.specieskey)]
    print("positives inside fitted universe:", hit.specieskey.nunique(),
          "unique specieskeys /", len(hit), "names")


if __name__ == "__main__":
    main()
