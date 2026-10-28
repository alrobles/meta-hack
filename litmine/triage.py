"""litmine Capa 1 — cheap YES/NO triage over sections.parquet chunks.

Each passage gets a tiny typed prompt: does it state a concrete
parasite/pathogen<->host association OR a negative result
(no_detection)? Taxon-agnostic (zero-shot); only YES chunks should
reach the expensive typed-extraction layer (Capa 2).

    python litmine/triage.py --sections data/litmine/sections.parquet \
        --out data/litmine/triage.parquet --backend fireworks --jobs 4

Resume: (pmid, section_type, offset) already in --out are skipped.
"""
import argparse
import json
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent"))
from meta_client import MetaClient  # noqa: E402

SYS = """Decide whether this text chunk states a CONCRETE
parasite/pathogen<->host association: a specific pathogen infecting,
isolated from, or detected in a specific animal host species/group —
INCLUDING negative findings (host sampled, pathogen NOT detected =
no_detection).
Output ONLY JSON: {"relevant": true|false,
 "hosts": [names as written], "parasites": [names as written]}
Rules: primary findings only; generic reviews/editorial claims,
mechanism-only or vaccine-efficacy text WITHOUT a stated
parasite-in-host observation -> false."""


def triage_chunk(client, model, text):
    r = client.chat(
        model=model, temperature=0, max_tokens=2000,
        messages=[{"role": "system", "content": SYS},
                  {"role": "user", "content": text}])
    try:
        d = json.loads(r["content"] or "{}")
    except json.JSONDecodeError:
        m = re.search(r"\{[^{}]*\}", r["content"] or "")
        try:
            d = json.loads(m.group(0)) if m else {"relevant": None}
        except (json.JSONDecodeError, AttributeError):
            d = {"relevant": None}
    return d, r["usage"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sections", default="data/litmine/sections.parquet")
    ap.add_argument("--out", default="data/litmine/triage.parquet")
    ap.add_argument("--backend", default="fireworks",
                    choices=["fireworks", "local", "meta"])
    ap.add_argument("--model")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    import pandas as pd
    sec = pd.read_parquet(args.sections)
    sec["key"] = (sec.pmid.astype(str) + "|" + sec.section_type + "|" +
                  sec.offset.astype(str))
    done = set()
    if os.path.exists(args.out):
        old = pd.read_parquet(args.out)
        done = set(old[old.relevant.notna()].key)
    todo = sec[~sec.key.isin(done)]
    if args.limit:
        todo = todo.head(args.limit)
    print(f"chunks: {len(sec)} | done {len(done)} | todo {len(todo)}",
          file=sys.stderr)

    client = {"fireworks": MetaClient.fireworks,
              "local": MetaClient.local,
              "meta": MetaClient}[args.backend]()
    model = args.model or ("accounts/fireworks/models/muse-spark-open"
                           if args.backend == "fireworks"
                           else "muse-spark-1.3")

    rows, lock = [], threading.Lock()

    def work(r):
        try:
            d, u = triage_chunk(client, model, r.text)
            rd = r._asdict() if hasattr(r, "_asdict") else r
            return {k: rd[k] for k in
                    ("key", "pmid", "pmcid", "section_type", "offset")} | {
                    "relevant": d.get("relevant"),
                    "hosts": "; ".join(d.get("hosts") or []),
                    "parasites": "; ".join(d.get("parasites") or []),
                    "in_tok": u.prompt_tokens, "out_tok": u.completion_tokens}
        except Exception as e:
            return {"key": r.key, "pmid": r.pmid, "pmcid": r.pmcid,
                    "section_type": r.section_type, "offset": r.offset,
                    "relevant": None, "hosts": "", "parasites": "",
                    "in_tok": 0, "out_tok": 0, "err": str(e)[:60]}

    todo_rows = list(todo.itertuples())
    with ThreadPoolExecutor(args.jobs) as pool:
        futs = {pool.submit(work, r): r for r in todo_rows}
        for fu in as_completed(futs):
            rows.append(fu.result())
            if len(rows) % 25 == 0:
                with lock:
                    print(f"  {len(rows)}/{len(todo_rows)}", file=sys.stderr)

    if rows:
        df = pd.DataFrame(rows)
        if done:
            df = pd.concat([pd.read_parquet(args.out), df],
                           ignore_index=True)
        df = df.drop_duplicates("key", keep="last")
        df.to_parquet(args.out, index=False)
    elif os.path.exists(args.out):
        df = pd.read_parquet(args.out)
        print("nada pendiente — resumen sobre el parquet existente",
              file=sys.stderr)
    else:
        print("nada pendiente y no hay parquet previo", file=sys.stderr)
        return

    yes = int(df.relevant.sum())
    tok_in, tok_out = int(df.in_tok.sum()), int(df.out_tok.sum())
    est = int(df[df.relevant == True].in_tok.sum())  # noqa: E712
    print(f"\nwrote {args.out}: {len(df)} chunks | YES {yes} "
          f"({100*yes/max(len(df),1):.0f}%) | tok in {tok_in} "
          f"out {tok_out}", file=sys.stderr)
    print(f"tokens que llegarian a Capa 2: {est} "
          f"({100*est/max(tok_in,1):.0f}% del total) — ahorro "
          f"~{100-100*est/max(tok_in,1):.0f}%", file=sys.stderr)
    print(client.spend.report(), file=sys.stderr)


if __name__ == "__main__":
    main()
