"""Precompute agent artifacts into the serve dir (runs locally, Meta API).

- briefing.json             muse-wonder situation digest
- explain_watershed_<h>.json  top-8 risk watersheds (muse-spark)
- explain_species_<k>.json    top-8 catalog species

These bake into the container so the demo needs no live key.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent.briefing import briefing
from agent.explain import explain
from agent.meta_client import MetaClient

DATA = Path(__file__).resolve().parent.parent / "app" / "data"


def main() -> None:
    import pandas as pd

    client = MetaClient()
    b = briefing(client, data_dir=DATA)
    (DATA / "briefing.json").write_text(json.dumps(b, indent=1))
    print("briefing ok:", b["headline"][:80])

    ws = pd.read_parquet(DATA / "risk_watershed.parquet")
    for _, r in ws.nlargest(8, "risk").iterrows():
        try:
            e = explain("watershed", r["huc8"], data_dir=DATA)
            (DATA / f"explain_watershed_{r['huc8']}.json").write_text(
                json.dumps(e))
            print("ws", r["name"], "ok")
        except Exception as ex:  # noqa: BLE001
            print("ws", r["huc8"], "fail", ex)

    win = pd.read_csv(DATA / "species_winner.csv").head(8)
    for _, r in win.iterrows():
        try:
            e = explain("species", str(r["specieskey"]), data_dir=DATA)
            (DATA / f"explain_species_{r['specieskey']}.json").write_text(
                json.dumps(e))
            print("sp", r["species"], "ok")
        except Exception as ex:  # noqa: BLE001
            print("sp", r["specieskey"], "fail", ex)

    print("spend: $", round(client.spend.total_usd, 4))


if __name__ == "__main__":
    main()
