"""Nightly digest — muse-wonder (System-2) briefing over the catalog.

Reads the exported artifacts (species_winner, risk_watershed, decisions)
and asks wonder for a structured situation briefing: headline, hotspots,
watchlist, caveats. Runs locally or inside the container on demand.
"""

import json
import os
import re
import sys
from pathlib import Path

from .meta_client import MODEL_SHOWCASE, MetaClient

BRIEF_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "wildseek_brief",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "headline": {"type": "string"},
                "hotspots": {"type": "array", "items": {"type": "string"},
                             "maxItems": 5},
                "watchlist": {"type": "array", "items": {"type": "string"},
                              "maxItems": 5},
                "methods_note": {"type": "string"},
            },
            "required": ["headline", "hotspots", "watchlist", "methods_note"],
            "additionalProperties": False,
        },
    },
}

PROMPT = """You are the WildSeek situation analyst. Given these JSON facts from
tonight's avian-influenza wildlife susceptibility run (xnicher ellipsoid
models over ERA5 bioclim normals, M = 95% ecoregions, APHIS wild-bird
detections rasterized to 1/120-degree recency kernel and aggregated to
HUC8 watersheds), write a briefing. Hotspots: name the top watersheds with
state codes. Watchlist: species or pipeline caveats worth flagging.
Keep each field under 40 words. methods_note must be ONE plain-text
sentence describing the modeling method (no markdown, no bullets, never
a bare symbol like "****"). Facts:
{facts}"""


def facts(data_dir: Path | str) -> dict:
    """Facts for the briefing prompt (no network; safe to unit-test)."""
    import duckdb

    data = Path(os.environ.get("DATA_DIR", data_dir))
    con = duckdb.connect()
    facts: dict = {}

    w = data / "species_winner.csv"
    if w.exists():
        facts["catalog"] = con.execute(
            f"""select count(*) n, min(aic) best_aic
                from '{w}'""").fetchone()
        facts["top_pairs"] = con.execute(
            f"""select best_temp||' x '||best_prec pair, count(*) n
                from '{w}' group by 1 order by 2 desc limit 5""").fetchall()

    r = data / "risk_watershed.parquet"
    if r.exists():
        facts["top_watersheds"] = con.execute(
            f"""select name, states, round(risk,3) risk, n_det
                from '{r}' order by risk desc limit 8""").fetchall()

    s = data / "susceptibility.parquet"
    if s.exists():
        facts["n_known_hosts"] = con.execute(
            f"select count(*) from '{s}' where label=1").fetchone()[0]
        facts["top_susceptible_unlabeled"] = con.execute(
            f"""select species, round(p_host,3) p from '{s}'
                where label=0 order by p_host desc limit 8""").fetchall()

    dec = data / "decisions_all.jsonl"
    if dec.exists():
        decs = [json.loads(l) for l in dec.read_text().splitlines()]
        from collections import Counter
        facts["t1_decisions"] = dict(Counter(d["decision"] for d in decs))
        flagged = [d["species"] for d in decs
                   if d["decision"] != "accept"][:10]
        facts["flagged_species"] = flagged

    # Ambiguous-tail verdicts (decide_tail): the agentic delta. T1 is
    # ~all accept, so without this the briefing honestly reports nothing.
    tail = data / "decisions_tail.jsonl"
    if tail.exists():
        try:
            tds = [json.loads(l) for l in tail.read_text().splitlines()
                   if l.strip()]
            from collections import Counter
            facts["tail_decisions"] = dict(
                Counter(d.get("decision") for d in tds))
            tflag = [f"{d.get('species')}: {d.get('why', '')[:120]}"
                     for d in tds if d.get("decision") == "flag_review"]
            facts["flagged_species"] = (
                facts.get("flagged_species", []) + tflag)[:10]
        except (OSError, ValueError) as ex:
            print(f"warn: tail jsonl unreadable: {ex}", file=sys.stderr)
    else:
        delta = data / "decisions_delta.csv"
        if delta.exists():
            try:
                import csv
                with delta.open() as fh:
                    rows = list(csv.DictReader(fh))
                from collections import Counter
                facts["tail_decisions"] = dict(
                    Counter(r.get("decision") for r in rows))
                tflag = [f"{r.get('species')}: {r.get('why', '')[:120]}"
                         for r in rows
                         if r.get("decision") == "flag_review"]
                facts["flagged_species"] = (
                    facts.get("flagged_species", []) + tflag)[:10]
            except (OSError, ValueError, csv.Error) as ex:
                print(f"warn: delta csv unreadable: {ex}", file=sys.stderr)
    if "flagged_species" not in facts:
        facts["flagged_species"] = []
    con.close()
    return facts


def _client_or_none() -> MetaClient | None:
    try:
        return MetaClient()
    except KeyError:
        return None


def briefing(client: MetaClient | None = None,
             data_dir: Path | str = "data/serve",
             model: str = MODEL_SHOWCASE) -> dict:
    client = client or _client_or_none()
    data = Path(os.environ.get("DATA_DIR", data_dir))
    f = facts(data)
    if client is None:
        return {"facts": f, "brief": None, "note": "MODEL_API_KEY unset"}

    r = client.chat(
        model=model,
        messages=[{"role": "user",
                   "content": PROMPT.format(facts=json.dumps(f)[:6000])}],
        response_format=BRIEF_SCHEMA,
    )
    out = json.loads(r["content"])
    if not re.search(r"[a-z]{4}", out.get("methods_note", "")):
        out["methods_note"] = (
            "Ellipsoid niche models (xnicher) fit on 88 bioclim pairs per "
            "species over 1996-2025 ERA5 normals; risk = recency-weighted "
            "detection kernel aggregated to HUC8 watersheds.")
    out["facts"] = f
    out["model"] = model
    return out
