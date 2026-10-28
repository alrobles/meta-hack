"""litmine — zero-shot parasite-host relation extraction.

    PMIDs -> PubTator3 annotations (Species->TaxID, free) ->
    S1 typed relation (MetaClient: fireworks muse-spark-open /
    glimmer / spark) -> edges.csv  -> optional eval vs gold.

The LLM never does NER: it classifies relations between entities
PubTator3 already linked to TaxIDs. Unlinked mentions are mapped by
the model to a linked TaxID when they clearly refer to it.

Usage:
  python litmine/extract.py --pmids 20694141 16319873 \
      --out edges.csv --eval data/litmine/virion_ebola_lassa_gold.csv
  python litmine/extract.py --pmids-csv data/litmine/gold_pmids.csv
"""
import argparse
import csv
import json
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent"))
from meta_client import MetaClient  # noqa: E402

PUBTATOR = ("https://www.ncbi.nlm.nih.gov/research/pubtator3-api/"
            "publications/export/pubtator?pmids=")
# NCBI_API_KEY (env) raises the rate limit 3->10 req/s — required for
# sweeps, harmless otherwise.
_API_KEY = os.environ.get("NCBI_API_KEY", "")

RELATIONS = ("parasite_of", "host_of", "vector_of", "reservoir_of",
             "intermediate_host_of", "dead_end", "no_detection")

SYS = """You extract parasite/pathogen<->host relations from abstracts.
You are given PubTator3 species annotations (text -> NCBI TaxID).
For EVERY pathogen-host relation stated in the text, output one JSON
object in a list:
{"parasite_taxid": str|int, "host_taxid": str|int,
 "relation": one of parasite_of|host_of|vector_of|reservoir_of|
   intermediate_host_of|dead_end|no_detection,
 "evidence_span": exact quote <=30 words, "confidence": 0-1,
 "geo": str|null, "date": str|null, "method": str|null}
Rules: use the TaxID of the LINKED annotation the mention maps to
(e.g. "E. helvum" -> its species TaxID; "EBOV" -> its virus TaxID).
parasite_taxid must differ from host_taxid. If no relation, output [].
Output ONLY the JSON list."""


def fetch_pubtator(pmids: list[str]) -> list[dict]:
    """Returns per-PMID {pmid, title, abstract, ents:{taxid:[texts]}}."""
    docs = {}
    for i in range(0, len(pmids), 100):
        chunk = ",".join(pmids[i:i + 100])
        url = PUBTATOR + chunk
        if _API_KEY:
            url += "&api_key=" + _API_KEY
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "litmine/0.1 (research)")
        raw = urllib.request.urlopen(req, timeout=60).read().decode()
        for line in raw.splitlines():
            parts = line.split("\t") if "\t" in line else line.split("|")
            if not parts:
                continue
            if "|t|" in line:
                pm = line.split("|")[0]
                docs.setdefault(pm, {"pmid": pm, "ents": {}})
                docs[pm]["title"] = line.split("|t|", 1)[1]
            elif "|a|" in line:
                pm = line.split("|")[0]
                docs.setdefault(pm, {"pmid": pm, "ents": {}})
                docs[pm]["abstract"] = line.split("|a|", 1)[1]
            elif len(parts) >= 5 and parts[4] == "Species":
                pm = parts[0]
                tid = parts[5] if len(parts) > 5 and parts[5] else ""
                docs.setdefault(pm, {"pmid": pm, "ents": {}})
                if tid:
                    docs[pm]["ents"].setdefault(tid, set()).add(parts[3])
    return list(docs.values())


def extract_edges(client: MetaClient, doc: dict, model: str,
                  retries: int = 3) -> list[dict]:
    ent_lines = "\n".join(
        f"  TaxID {tid}: {', '.join(sorted(ts))}"
        for tid, ts in sorted(doc["ents"].items()))
    user = (f"TITLE: {doc.get('title','')}\nABSTRACT: "
            f"{doc.get('abstract','')}\nENTITIES:\n{ent_lines}")
    r = None
    for att in range(retries):
        try:
            r = client.chat(
                model=model, temperature=0, max_tokens=2048,
                messages=[{"role": "system", "content": SYS},
                          {"role": "user", "content": user}])
            break
        except Exception:
            if att == retries - 1:
                raise
            import time
            time.sleep(5 * (att + 1))
    m = re.search(r"\[.*\]", r["content"], re.S)
    edges = []
    try:
        items = json.loads(m.group(0)) if m else []
    except json.JSONDecodeError:
        items = []
    known = set(doc["ents"])
    for e in items:
        try:
            p, h, rel = str(e["parasite_taxid"]), str(e["host_taxid"]), \
                e["relation"]
        except (KeyError, TypeError):
            continue
        if p == h or rel not in RELATIONS:
            continue
        e["pmid"] = doc["pmid"]
        e["parasite_taxid"], e["host_taxid"] = p, h
        e["host_texts"] = " | ".join(sorted(doc["ents"].get(h, [])))
        e["para_texts"] = " | ".join(sorted(doc["ents"].get(p, [])))
        e["known"] = p in known and h in known
        edges.append(e)
    return edges


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


def evaluate(edges: list[dict], gold_csv: str) -> None:
    gold = [r for r in csv.DictReader(open(gold_csv))]
    by_pmid = {}
    for g in gold:
        by_pmid.setdefault(str(g.get("PUBMED ID", "")).strip(), []
                           ).append(_norm(g.get("Host", "")))
    hit = 0
    for e in edges:
        gh = by_pmid.get(e["pmid"], [])
        names = _norm(e.get("host_texts", "") + " " +
                      e.get("evidence_span", "") or "")
        hit += any(g and (g in names or
                          g.split()[0] in e.get("host_texts", "").lower())
                   for g in gh)
    print(f"gold={len(gold)} extracted={len(edges)} "
          f"host-recall-ish={hit}/{len(gold)} edges matched")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pmids", nargs="*", default=[])
    ap.add_argument("--pmids-csv")
    ap.add_argument("--pmid-col", default="PUBMED ID")
    ap.add_argument("--backend", default="fireworks",
                    choices=["fireworks", "local", "meta"])
    ap.add_argument("--model", default=None)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--out", default="edges.csv")
    ap.add_argument("--jobs", type=int, default=1,
                    help="parallel doc extractions (threads)")
    ap.add_argument("--resume", action="store_true",
                    help="skip pmids already in --out")
    ap.add_argument("--eval", dest="gold")
    args = ap.parse_args()

    pmids = list(args.pmids)
    if args.pmids_csv:
        pmids += [r[args.pmid_col] for r in
                  csv.DictReader(open(args.pmids_csv))
                  if r.get(args.pmid_col)]
    pmids = [p for p in dict.fromkeys(
             re.sub(r"\D", "", str(p)) for p in pmids) if p]
    done = set()
    if args.resume and os.path.exists(args.out):
        done = {r["pmid"] for r in csv.DictReader(open(args.out))}
        print(f"resume: {len(done)} pmids already done", file=sys.stderr)
    if args.limit:
        pmids = pmids[:args.limit]
    print(f"pmids: {len(pmids)}", file=sys.stderr)

    docs = fetch_pubtator(pmids)
    print(f"pubtator docs: {len(docs)}", file=sys.stderr)

    client = {"fireworks": MetaClient.fireworks,
              "local": MetaClient.local,
              "meta": MetaClient}[args.backend]()
    model = args.model or ("accounts/fireworks/models/muse-spark-open"
                           if args.backend == "fireworks"
                           else "muse-spark-1.3")

    fields = ["pmid", "parasite_taxid", "host_taxid", "relation",
              "confidence", "evidence_span", "geo", "date", "method",
              "host_texts", "para_texts", "known"]
    all_edges = []
    fails = []
    todo = [d for d in docs if d["pmid"] not in done]
    mode = "a" if args.resume and done else "w"
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed
    lock = threading.Lock()
    with open(args.out, mode, newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if mode == "w":
            w.writeheader()

        def work(d):
            try:
                return d["pmid"], extract_edges(client, d, model), None
            except Exception as e:
                return d["pmid"], [], str(e)[:80]

        if args.jobs > 1:
            pool = ThreadPoolExecutor(args.jobs)
            futs = [pool.submit(work, d) for d in todo]
            it = (fu.result() for fu in as_completed(futs))
        else:
            it = (work(d) for d in todo)
        for pm, es, err in it:
            if err is not None:
                fails.append((pm, err))
                print(f"  pmid {pm}: FAIL {err}", file=sys.stderr)
                continue
            all_edges += es
            with lock:
                w.writerows(es); f.flush()
            print(f"  pmid {pm}: {len(es)} edges", file=sys.stderr)
        if args.jobs > 1:
            pool.shutdown()
    if fails:
        print(f"FAILURES: {fails}", file=sys.stderr)
    print(f"wrote {args.out}: {len(all_edges)} edges", file=sys.stderr)
    print(client.spend.report(), file=sys.stderr)
    if args.gold:
        evaluate(all_edges, args.gold)


if __name__ == "__main__":
    main()
