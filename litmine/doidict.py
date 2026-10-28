"""Persistent DOI<->PMID dictionary for litmine.

Store: a CSV (default data/litmine/doi_pmid_map.csv) accumulating
resolutions across runs — lookup hits never re-hit the API.

    from doidict import DoiDict
    dd = DoiDict("data/litmine/doi_pmid_map.csv")
    pmids = dd.resolve_list(dois)      # fills missing via NCBI idconv
    dd.pmid_for("10.x/yy")             # single lookup (None if absent)

CLI sync: python litmine/doidict.py corpus.csv --col doi \
             --dict data/litmine/doi_pmid_map.csv
"""
import argparse
import csv
import json
import os
import sys
import urllib.request

IDCONV = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/?format=json&ids="
KEY = os.environ.get("NCBI_API_KEY", "")

FIELDS = ["doi", "pmid", "pmcid", "source"]


class DoiDict:
    def __init__(self, path):
        self.path = path
        self.by_doi = {}
        self.by_pmid = {}
        self.meta = {}
        if os.path.exists(path):
            for r in csv.DictReader(open(path)):
                self._put(r["doi"], r["pmid"], r.get("pmcid", ""),
                          r.get("source", "idconv"))

    def _put(self, doi, pmid, pmcid="", source="idconv"):
        self.by_doi[doi] = pmid
        self.by_pmid[pmid] = doi
        self.meta[doi] = {"pmcid": pmcid, "source": source}

    def pmid_for(self, doi):
        return self.by_doi.get(doi)

    def doi_for(self, pmid):
        return self.by_pmid.get(pmid)

    def resolve_list(self, dois, source="idconv", batch=200):
        """Return {doi: pmid}; resolves unknowns via idconv, persists."""
        missing = [d for d in dois if d not in self.by_doi]
        print(f"dict: {len(dois)-len(missing)} hits, "
              f"{len(missing)} to resolve", file=sys.stderr)
        for i in range(0, len(missing), batch):
            chunk = missing[i:i + batch]
            url = IDCONV + ",".join(chunk)
            if KEY:
                url += "&api_key=" + KEY
            try:
                req = urllib.request.Request(url)
                req.add_header("User-Agent", "litmine/0.1 (research)")
                d = json.loads(
                    urllib.request.urlopen(req, timeout=60).read())
            except Exception as e:
                print("idconv fail", e, file=sys.stderr)
                continue
            for rec in d.get("records", []):
                if rec.get("pmid"):
                    self._put(rec["doi"], str(rec["pmid"]),
                              rec.get("pmcid", ""), source)
            self.save()
        return {d: self.by_doi[d] for d in dois if d in self.by_doi}

    def save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            for doi, pmid in sorted(self.by_doi.items()):
                m = self.meta.get(doi, {})
                w.writerow({"doi": doi, "pmid": pmid,
                            "pmcid": m.get("pmcid", ""),
                            "source": m.get("source", "idconv")})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--col", default="doi")
    ap.add_argument("--dict", required=True)
    args = ap.parse_args()
    rows = list(csv.DictReader(open(args.csv)))
    dois = [r[args.col].strip().lstrip("\ufeff") for r in rows
            if r.get(args.col) and r[args.col].strip() != "N/A"]
    dd = DoiDict(args.dict)
    m = dd.resolve_list(dois)
    print(f"dict now holds {len(dd.by_doi)} dois "
          f"({len(m)} resolved this corpus)", file=sys.stderr)


if __name__ == "__main__":
    main()
