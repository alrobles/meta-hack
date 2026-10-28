"""GBIF backbone taxonomy for our species list.

    python3 fetch_taxonomy.py

Reads data/species_winner.csv (specieskey already = GBIF key), hits
/v1/species/{key}, writes data/taxonomy.parquet:
specieskey, species, class, order, family, genus
"""
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests

W = "data/species_winner.csv"
OUTF = "data/taxonomy.parquet"
API = "https://api.gbif.org/v1/species/{}"

RANKS = ("class", "order", "family", "genus")


def fetch(key):
    try:
        r = requests.get(API.format(int(key)), timeout=15)
        j = r.json()
        return {r_: j.get(r_) for r_ in RANKS} | {
            "specieskey": int(key), "status": j.get("status")}
    except Exception:
        return {"specieskey": int(key), "status": "ERR"}


def main():
    w = pd.read_csv(W)
    keys = w.specieskey.dropna().unique()
    with ThreadPoolExecutor(8) as ex:
        rows = list(ex.map(fetch, keys))
    tax = pd.DataFrame(rows).merge(
        w[["specieskey", "species"]], on="specieskey", how="left")
    tax.to_parquet(OUTF, index=False)
    print(len(tax), "species;",
          tax.status.value_counts().to_dict(),
          "| classes:", tax["class"].value_counts().to_dict())


if __name__ == "__main__":
    main()
