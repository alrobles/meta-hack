"""Compute planner — the orient/decide layer of the agentic OODA loop.

    observe (fit_catalog + decisions + audit scores)
      -> orient (which species/stages need compute, at what depth)
      -> decide (emit a run spec the actor executes via sbatch)

The actor today is domain_build.sh / nightly.py (submit+poll); this
emits the spec JSON they consume, plus a refit_queue.csv.

Policy v1 — tiered starts instead of a flat 88x60 blind campaign:
  screen : every pair x SCREEN_STARTS (cheap AIC landscape)
  refine : top-REFINE_K pairs x FULL_STARTS (winner + dispersion)
  audit  : species flagged by verdicts/poor metrics -> refit queue
Estimated cpu-hours are printed so a budget (--max-cpuh) can gate it.

Usage:
  python planner.py --domain na_ca --pool pool_all_na_ca.csv \
      --max-cpuh 120 --emit run_spec_na_ca.json
"""
import argparse
import json
import os
import sys
import time

import duckdb
import pandas as pd

OUT = os.environ.get("WILDSEEK_BEEG", ".")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import domain

N_PAIRS = 88            # 11 temps x 8 precs
SEC_PER_FIT = 1.3       # observed ~1.3 s per optimize_niche call (CPU)


def load_state(out: str) -> dict:
    st = {}
    if os.path.exists(f"{out}/fit_catalog.csv"):
        with duckdb.connect() as con:
            st["catalog"] = con.execute(
                f"""select specieskey, count(*) n_pairs,
                    min(aic) aic_min,
                    count(*) filter (where converged) n_conv
                    from '{out}/fit_catalog.csv' group by 1""").df()
    for f, key in (("species_winner.csv", "winner"),
                   ("decisions_delta.csv", "decisions")):
        p = f"{out}/{f}"
        if os.path.exists(p):
            st[key] = pd.read_csv(p)
    return st


def plan(pool_csv: str, state: dict, args) -> dict:
    pool = pd.read_csv(f"{OUT}/{pool_csv}")
    n_sp = len(pool)
    done = state.get("catalog")
    fitted = set(done["specieskey"]) if done is not None else set()
    new = pool[~pool["specieskey"].isin(fitted)]

    # tier3: species the audit/verdicts already flagged -> more starts
    flagged = set()
    dec = state.get("decisions")
    if dec is not None and "decision" in dec:
        flagged = set(dec.loc[dec["decision"].isin(
            ["flag_review", "refit", "switch"]), "specieskey"])
    win = state.get("winner")
    if win is not None and "rel_gap" in win:
        # multimodal evidence -> they deserve the deep tier on refit
        flagged |= set(win.loc[win["rel_gap"].fillna(0) > 0.25,
                               "specieskey"])
    flagged &= set(pool["specieskey"])

    tiers = [
        dict(name="screen", species=len(new),
             pairs=N_PAIRS, starts=args.screen_starts),
        dict(name="refine", species=len(new),
             pairs=args.refine_k, starts=args.full_starts),
        dict(name="audit", species=len(flagged),
             pairs=args.refine_k, starts=args.full_starts),
    ]
    est = 0.0
    for t in tiers:
        t["fits"] = t["species"] * t["pairs"] * t["starts"]
        t["cpu_h"] = round(t["fits"] * SEC_PER_FIT / 3600, 1)
        est += t["cpu_h"]

    shards = max(8, min(args.max_shards, int(est // args.hours_target) + 1))
    return {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "domain": domain.name(), "pool": pool_csv,
        "species_total": n_sp, "species_new": len(new),
        "species_flagged": len(flagged),
        "tiers": tiers, "est_cpu_h": round(est, 1),
        "n_shards": shards,
        "env": {"WILDSEEK_DOMAIN": domain.name(),
                "WILDSEEK_POOL": pool_csv,
                "N_SHARDS": shards,
                "SCREEN_STARTS": args.screen_starts,
                "FULL_STARTS": args.full_starts},
        "refit_queue": sorted(flagged),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default=f"pool_flagship{domain.suffix()}.csv")
    ap.add_argument("--max-cpuh", dest="max_cpuh", type=float,
                    default=120)
    ap.add_argument("--max-shards", type=int, default=64)
    ap.add_argument("--hours-target", type=float, default=2.0)
    ap.add_argument("--screen-starts", type=int, default=12)
    ap.add_argument("--full-starts", type=int, default=60)
    ap.add_argument("--refine-k", type=int, default=5)
    ap.add_argument("--emit")
    ap.add_argument("--domain")
    args = ap.parse_args()
    if args.domain:
        os.environ["WILDSEEK_DOMAIN"] = args.domain

    spec = plan(args.pool, load_state(OUT), args)
    if spec["est_cpu_h"] > args.max_cpuh:
        spec["budget"] = (f"OVER {args.max_cpuh} cpu-h "
                          "— trim screen_starts or split pools")
    else:
        spec["budget"] = "ok"
    txt = json.dumps(spec, indent=1)
    print(txt)
    if args.emit:
        open(args.emit, "w").write(txt + "\n")
        print("wrote", args.emit, file=sys.stderr)


if __name__ == "__main__":
    main()
