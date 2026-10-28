"""On-demand narrative for the /explain endpoint (muse-spark, typed).

Reads the deterministic risk/fit tables, packs the numbers, asks
muse-spark for a structured surveillance blurb. The model sees only
catalog facts — no invented epidemiology.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .meta_client import MODEL_AGENT, MetaClient

EXPLAIN_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "surveillance_brief",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "headline": {"type": "string"},
                "risk_level": {
                    "type": "string",
                    "enum": ["low", "moderate", "elevated", "high"],
                },
                "drivers": {"type": "array",
                            "items": {"type": "string"}},
                "watch_species": {"type": "array",
                                  "items": {"type": "string"}},
                "sampling_rec": {"type": "string"},
            },
            "required": ["headline", "risk_level", "drivers",
                         "watch_species", "sampling_rec"],
            "additionalProperties": False,
        },
    },
}

SYS = """\
You write terse HPAI surveillance briefs for wildlife-agriculture
interfaces. Given catalog facts (recent detections, susceptible
species, fit diagnostics), emit the structured brief. Do not invent
detections or species not present in the input. sampling_rec is one
concrete WILDLIFE sampling action for professionals; never
personal-health advice."""


def explain(kind: str, ident: str, data_dir: Path | str,
            model: str = MODEL_AGENT) -> dict:
    import duckdb

    data = Path(os.environ.get("DATA_DIR", data_dir))
    con = duckdb.connect()
    facts: dict = {"kind": kind, "id": ident}

    # ident comes straight from the URL path — escape quotes before
    # interpolating (specieskey cast to int, the rest are string cols)
    esc = str(ident).replace("'", "''")
    if kind == "species":
        f = data / "species_winner.csv"
        if f.exists():
            cond = (f"specieskey={int(ident)}" if str(ident).isdigit()
                    else f"lower(species)=lower('{esc}')")
            r = con.execute(
                f"select * from '{f}' where {cond} limit 1"
            ).fetchdf()
            facts["fit"] = r.to_dict("records")
    else:
        f = data / "risk_watershed.parquet"
        if f.exists():
            r = con.execute(
                f"select * from '{f}' where huc8='{esc}' "
                f"or name ilike '%{esc}%' "
                f"order by risk desc limit 5"
            ).fetchdf()
            facts["risk"] = r.to_dict("records")

    key = os.environ.get("MODEL_API_KEY")
    if not key:
        return {"kind": kind, "id": ident, "facts": facts,
                "brief": None, "note": "MODEL_API_KEY unset"}

    client = MetaClient(api_key=key)
    r = client.chat(
        model=model,
        messages=[
            {"role": "system", "content": SYS},
            {"role": "user",
             "content": json.dumps(facts, default=str)[:6000]},
        ],
        response_format=EXPLAIN_SCHEMA,
        temperature=0.2,
    )
    return {"kind": kind, "id": ident,
            "facts": facts, "brief": json.loads(r["content"])}
