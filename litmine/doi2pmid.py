"""DOI -> PMID resolution — thin CLI over the persistent DoiDict.

    python litmine/doi2pmid.py corpus.csv --col doi --out pmids.csv

Writes pmid,doi rows for every resolvable DOI, caching all
resolutions in data/litmine/doi_pmid_map.csv.
"""
import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from doidict import DoiDict  # noqa: E402

DICT = os.environ.get("LITMINE_DICT", "data/litmine/doi_pmid_map.csv")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--col", default="doi")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    rows = list(csv.DictReader(open(args.csv)))
    dois = [r[args.col].strip().lstrip("\ufeff") for r in rows
            if r.get(args.col) and r[args.col].strip() != "N/A"]
    print(f"dois: {len(dois)}", file=sys.stderr)
    m = DoiDict(DICT).resolve_list(dois)
    with open(args.out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["pmid", "doi"])
        w.writerows((m[d], d) for d in dois if d in m)
    print(f"wrote {args.out}: {len(m)} pmids", file=sys.stderr)


if __name__ == "__main__":
    main()
