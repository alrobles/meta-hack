"""Ambiguous-tail decision pass (audit §4) — the layer that actually decides.

Deterministic gate first: a species only reaches the agent when the
88-pair scan leaves real doubt:
  - top5 AIC spread < 15        (several pairs nearly tied)
  - nobs < 120                  (sparse occurrence cells)
  - marine_frac > 0.5           (mostly outside terrestrial ecoregions)
  - n_eco < 3  or  n_eco > 200  (degenerate / ubiquitous M)
  - flag != accepted_global     (any fit trouble)

Agent verdicts have teeth:
  - drop          -> species leaves species_winner_served.csv (the map)
  - flag_review   -> review=true column in served catalog
  - switch_family / refit_more_starts -> schedules refit (switch_to column
                     carries the target family, or "more_starts"), delta
                     records old_aic -> new_aic after refit
  - accept        -> stays, but only ~the tail was ever asked

Outputs:
  data/decisions_tail.jsonl   raw verdicts
  data/serve/decisions_delta.csv  species, decision, why, old->new aic,
                                  in_map before/after
  data/serve/species_winner.csv   served catalog (drops applied)
  data/refit_queue.csv        species to re-fit (consumed by R job)

Usage:
  MODEL_API_KEY=... python pipeline/decide_tail.py [--dry-run] [--rpm 60]
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

TAIL_AIC_SPREAD = 15.0
TAIL_NOBS = 120
TAIL_MARINE = 0.5
TAIL_ECO_LO, TAIL_ECO_HI = 3, 200


def load_tail(src: Path) -> pd.DataFrame:
    cat = pd.read_csv(src / "fit_catalog.csv")
    win = pd.read_csv(src / "species_winner.csv")

    ok = cat[cat["flag"].isin(["accepted_global", "accepted_noise"])]
    top5 = (ok.sort_values("aic")
              .groupby("specieskey")["aic"]
              .apply(lambda s: s.nsmallest(5))
              .groupby("specieskey")
              .agg(["min", "max", "count"])
              .rename(columns={"min": "aic_best", "max": "aic_5th"}))
    top5["aic_spread"] = top5["aic_5th"] - top5["aic_best"]

    nobs = cat.groupby("specieskey")["nobs"].max()
    flags = cat.groupby("specieskey")["flag"].agg(
        lambda f: f.value_counts().to_dict())

    df = win.set_index("specieskey").join(top5).join(
        nobs.rename("nobs")).join(flags.rename("flags"))
    df.reset_index(inplace=True)

    eco_file = src / "m_summary.parquet"
    if eco_file.exists():
        eco = pd.read_parquet(eco_file)
        df = df.merge(
            eco[["specieskey", "n_eco_m", "n_unmatched"]],
            on="specieskey", how="left")
        df["n_eco"] = df["n_eco_m"]
    else:
        df["n_eco"] = None
        df["n_unmatched"] = 0
    cc = src / "species_cell_counts.csv"
    if cc.exists():
        df = df.merge(
            pd.read_csv(cc)[["specieskey", "n_cells_global"]],
            on="specieskey", how="left")
        df["marine_frac"] = (df["n_unmatched"].fillna(0)
                             / df["n_cells_global"].clip(lower=1))
    else:
        df["marine_frac"] = 0

    amb = (
        (df["aic_spread"] < TAIL_AIC_SPREAD)
        | (df["nobs"] < TAIL_NOBS)
        | (df["marine_frac"].fillna(0) > TAIL_MARINE)
        | (df["n_eco"].fillna(10) < TAIL_ECO_LO)
        | (df["n_eco"].fillna(10) > TAIL_ECO_HI)
        | (df["flag"] != "accepted_global")
    )
    df["ambiguous"] = amb
    why = []
    for _, r in df.iterrows():
        w = []
        if pd.notna(r["aic_spread"]) and r["aic_spread"] < TAIL_AIC_SPREAD:
            w.append(f"aic_spread={r['aic_spread']:.1f}")
        if r["nobs"] < TAIL_NOBS:
            w.append(f"nobs={int(r['nobs'])}")
        if (r.get("marine_frac") or 0) > TAIL_MARINE:
            w.append(f"marine={r['marine_frac']:.2f}")
        ne = r.get("n_eco")
        if pd.notna(ne) and (ne < TAIL_ECO_LO or ne > TAIL_ECO_HI):
            w.append(f"n_eco={int(ne)}")
        if r["flag"] != "accepted_global":
            w.append(f"flag={r['flag']}")
        why.append(";".join(w))
    df["tail_reason"] = why
    return df


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--src", default=str(DATA))
    p.add_argument("--dst", default=str(DATA / "serve"))
    p.add_argument("--rpm", type=float, default=60.0)
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    src, dst = Path(a.src), Path(a.dst)
    dst.mkdir(parents=True, exist_ok=True)

    df = load_tail(src)
    tail = df[df["ambiguous"]].copy()
    print(f"{len(tail)}/{len(df)} species in ambiguous tail")
    if a.dry_run:
        print(tail[["species", "tail_reason"]].head(20).to_string())
        return

    client = MetaClient()
    out_jsonl = DATA / "decisions_tail.jsonl"
    delay = 60.0 / a.rpm
    verdicts = []
    with out_jsonl.open("a") as fh:
        for i, r in tail.iterrows():
            s = FitSummary(
                specieskey=int(r["specieskey"]), species=r["species"],
                n_cells=int(r["nobs"]),
                n_pairs_tried=int(r["n_pairs_tried"]),
                n_accepted=int(r["n_accepted"]),
                best_aic=float(r["aic"]),
                aic_spread_top5=float(r["aic_spread"])
                if pd.notna(r["aic_spread"]) else None,
                flags=r["flags"] if isinstance(r["flags"], dict) else {},
                context=(f"tail_reason={r['tail_reason']}; "
                         f"marine_frac={r.get('marine_frac', 0):.2f}; "
                         f"n_eco_m={r.get('n_eco')}; "
                         f"best_pair={r['best_temp']}x{r['best_prec']}"),
            )
            try:
                d = decide_species(client, s)
            except Exception as e:  # noqa: BLE001
                d = {"specieskey": s.specieskey, "species": s.species,
                     "decision": "flag_review", "switch_to": "none",
                     "reason": f"api_error: {e}", "confidence": "low"}
            d["tail_reason"] = r["tail_reason"]
            fh.write(json.dumps(d) + "\n")
            fh.flush()
            verdicts.append((r, d))
            if len(verdicts) % 20 == 0:
                print(f"{len(verdicts)}/{len(tail)} "
                      f"spend=${client.spend.total_usd:.4f}", flush=True)
            time.sleep(delay)

    rows = []
    drops = set()
    for r, d in verdicts:
        dec = d.get("decision", "accept")
        if dec == "drop":
            drops.add(int(r["specieskey"]))
        # more_starts is a DECISION (refit, same family) — surface it in
        # switch_to so the refit queue picks it up
        sw = "more_starts" if dec == "refit_more_starts" \
            else d.get("switch_to", "none")
        rows.append({
            "specieskey": int(r["specieskey"]),
            "species": r["species"],
            "decision": dec,
            "switch_to": sw,
            "why": d.get("reason", ""),
            "tail_reason": r["tail_reason"],
            "aic_old": r["aic"],
            "aic_new": None,
            "changed": dec != "accept",
            "in_map_before": True,
            "in_map_after": dec != "drop",
            "confidence": d.get("confidence"),
        })
    delta = pd.DataFrame(rows)
    delta.to_csv(dst / "decisions_delta.csv", index=False)

    served = pd.read_csv(src / "species_winner.csv")
    served["review"] = served["specieskey"].isin(
        delta.loc[delta["decision"] == "flag_review", "specieskey"])
    served = served[~served["specieskey"].isin(drops)]
    served.to_csv(dst / "species_winner.csv", index=False)

    refit = delta[delta["switch_to"].isin(
        ["skew_t", "skew_normal", "ip_weighted", "more_starts"])]
    refit[["specieskey", "species", "switch_to"]].to_csv(
        DATA / "refit_queue.csv", index=False)

    print(delta["decision"].value_counts())
    print(f"delta -> {dst/'decisions_delta.csv'} "
          f"({int(delta['changed'].sum())} changed, "
          f"{len(drops)} dropped, {len(refit)} refit queued)")
    print(f"served catalog -> {dst/'species_winner.csv'} ({len(served)} spp)")
    print("spend: $", round(client.spend.total_usd, 4))


if __name__ == "__main__":
    main()
