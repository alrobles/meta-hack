"""T1 decision pass over the xnicher fit catalog.

Builds per-species FitSummary from fit_catalog.csv (cluster export) and
calls agent.decide.decide_species -> decisions_<cls>.jsonl.

Local run (Meta API only). Usage:
  MODEL_API_KEY=... python pipeline/decide_campaign.py --cls mammalia
"""

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent.decide import FitSummary, decide_species
from agent.meta_client import MetaClient

DATA = Path(__file__).resolve().parent.parent / "data"


def summaries(cat: pd.DataFrame) -> list[FitSummary]:
    out = []
    for (key, sp), g in cat.groupby(["specieskey", "species"]):
        flags = g["flag"].value_counts().to_dict()
        ok = g[g["flag"].isin(["accepted_global", "accepted_noise"])]
        aics = ok["aic"].nsmallest(5) if len(ok) else pd.Series(dtype=float)
        out.append(FitSummary(
            specieskey=int(key), species=sp,
            n_cells=int(g["nobs"].max()),
            n_pairs_tried=len(g),
            n_accepted=len(ok),
            best_aic=float(aics.min()) if len(aics) else None,
            aic_spread_top5=float(aics.max() - aics.min())
            if len(aics) > 1 else None,
            flags=flags))
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--cls", default="mammalia")
    p.add_argument("--cat", default=str(DATA / "fit_catalog.csv"))
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--rpm", type=float, default=30.0)
    a = p.parse_args()

    cat = pd.read_csv(a.cat)
    cls_file = DATA / f"env_occ_{a.cls}.parquet"
    keys = None
    if cls_file.exists():
        keys = set(pd.read_parquet(
            cls_file, columns=["specieskey"])["specieskey"].unique())
        cat = cat[cat["specieskey"].isin(keys)] if keys else cat
    ss = summaries(cat)
    if a.limit:
        ss = ss[: a.limit]

    out = DATA / f"decisions_{a.cls}.jsonl"
    done = set()
    if out.exists():
        for i, line in enumerate(out.read_text().splitlines()):
            try:
                done.add(json.loads(line)["specieskey"])
            except (ValueError, KeyError) as ex:
                print(f"warn: skip bad jsonl line {i}: {ex}",
                      file=sys.stderr)
    ss = [s for s in ss if s.specieskey not in done]
    print(f"{len(ss)} species pending ({len(done)} already decided)")

    client = MetaClient()
    out = DATA / f"decisions_{a.cls}.jsonl"
    delay = 60.0 / a.rpm
    n = 0
    with out.open("a") as fh:
        for s in ss:
            try:
                d = decide_species(client, s)
            except Exception as e:  # noqa: BLE001 — log and continue
                d = {"specieskey": s.specieskey, "species": s.species,
                     "decision": "flag_review", "switch_to": "none",
                     "reason": f"api_error: {e}", "confidence": "low"}
            fh.write(json.dumps(d) + "\n")
            n += 1
            if n % 20 == 0:
                print(f"{n}/{len(ss)} spend=${client.spend.total_usd:.4f}",
                      flush=True)
            time.sleep(delay)
    print("done", n, "->", out, "spend=$", round(client.spend.total_usd, 4))


if __name__ == "__main__":
    main()
