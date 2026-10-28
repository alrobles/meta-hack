"""PMC full-text fetch (Capa 0): PMID -> PMCID -> BioC sections -> parquet.

Keep: METHODS, RESULTS, TABLE, FIG (legends), SUPP — plus TITLE/ABSTRACT
for context. Discard: INTRO, DISCUSS, REF, ACK (noise + tokens).

    python litmine/pmc_fetch.py --pmids-csv gold.csv --pmid-col pmid \
        --out data/litmine/sections.parquet

Cache: data/litmine/pmc_cache/{pmcid}.json — never re-fetch.
Resume: PMCIDs already in --out are skipped.
"""
import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.request

KEEP = {"TITLE", "ABSTRACT", "METHODS", "RESULTS", "TABLE", "FIG", "SUPP"}
IDCONV = ("https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/"
          "?format=json&ids=")
BIOC = ("https://www.ncbi.nlm.nih.gov/research/bionlp/RESTful/pmcoa.cgi/"
        "BioC_JSON/{}/unicode")
KEY = os.environ.get("NCBI_API_KEY", "")
UA = {"User-Agent": "litmine/0.1 (research)", "tool": "litmine",
      "email": os.environ.get("NCBI_EMAIL", "")}


def _get(url, timeout=60):
    if KEY:
        url += ("&" if "?" in url else "?") + "api_key=" + KEY
    req = urllib.request.Request(url)
    for k, v in UA.items():
        req.add_header(k, v)
    return urllib.request.urlopen(req, timeout=timeout).read()


def resolve_pmcids(pmids, batch=200):
    """PMID list -> {pmid: pmcid} via idconv."""
    out = {}
    for i in range(0, len(pmids), batch):
        try:
            d = json.loads(_get(IDCONV + ",".join(pmids[i:i + batch])))
            for r in d.get("records", []):
                if r.get("pmcid"):
                    out[str(r.get("pmid", "")).strip()] = r["pmcid"]
        except Exception as e:
            print("idconv fail:", e, file=sys.stderr)
        time.sleep(0.34 if not KEY else 0.11)
    return out


def fetch_bioc(pmcid, cache_dir):
    path = os.path.join(cache_dir, pmcid + ".json")
    if os.path.exists(path):
        return json.load(open(path))
    try:
        raw = _get(BIOC.format(pmcid))
    except Exception as e:
        print(f"  {pmcid} fetch fail: {e}", file=sys.stderr)
        return None
    try:
        d = json.loads(raw)
    except json.JSONDecodeError:
        print(f"  {pmcid}: not OA / no BioC", file=sys.stderr)
        return None
    os.makedirs(cache_dir, exist_ok=True)
    json.dump(d, open(path, "w"))
    return d


def passages(doc):
    """BioC doc -> kept section rows."""
    rows = []
    for d in (doc if isinstance(doc, list) else [doc]):
        for dd in d.get("documents", []):
            pmid = str(dd.get("id", ""))
            for p in dd.get("passages", []):
                inf = p.get("infons", {})
                st = inf.get("section_type", "")
                if st not in KEEP:
                    continue
                rows.append({
                    "pmid": pmid, "pmcid": "",
                    "section_type": st,
                    "pass_type": inf.get("type", ""),
                    "text": (p.get("text") or "").strip(),
                    "offset": p.get("offset", 0),
                })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pmids", nargs="*")
    ap.add_argument("--pmids-csv")
    ap.add_argument("--pmid-col", default="pmid")
    ap.add_argument("--out", default="data/litmine/sections.parquet")
    ap.add_argument("--cache-dir", default="data/litmine/pmc_cache")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    pmids = list(args.pmids or [])
    if args.pmids_csv:
        for r in csv.DictReader(open(args.pmids_csv)):
            v = re.sub(r"\D", "", str(r.get(args.pmid_col, "")))
            if v:
                pmids.append(v)
    pmids = list(dict.fromkeys(pmids))
    if args.limit:
        pmids = pmids[:args.limit]
    print(f"pmids: {len(pmids)}", file=sys.stderr)

    import pandas as pd
    done = set()
    if os.path.exists(args.out):
        done = set(pd.read_parquet(args.out).pmcid.unique())
        print(f"resume: {len(done)} pmcids ya en {args.out}", file=sys.stderr)

    pmid2pmcid = resolve_pmcids(pmids)
    print(f"pmcids resueltos: {len(pmid2pmcid)}", file=sys.stderr)

    new_rows = []
    for pmid, pmcid in pmid2pmcid.items():
        if pmcid in done:
            continue
        doc = fetch_bioc(pmcid, args.cache_dir)
        time.sleep(0.4 if not KEY else 0.15)
        if doc is None:
            continue
        rows = passages(doc)
        for r in rows:
            r["pmid"] = pmid
            r["pmcid"] = pmcid
        new_rows += rows

    if new_rows:
        df = pd.DataFrame(new_rows)
        if os.path.exists(args.out):
            df = pd.concat([pd.read_parquet(args.out), df],
                           ignore_index=True)
        df.to_parquet(args.out, index=False)
        print(f"wrote {args.out}: +{len(new_rows)} passages "
              f"({df.pmcid.nunique()} pmcids total)", file=sys.stderr)


if __name__ == "__main__":
    main()
