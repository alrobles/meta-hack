"""Materialize APHIS HPAI layers to parquet for the risk surface.

Pulls the full wild-bird surveillance table (174k records, incl.
Watershed field = sub-county resolution) + livestock events +
flyways into data/ as parquet. API fetch — runs locally, not on HPC.
"""

import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent import aphis

OUT = Path(__file__).resolve().parent.parent / "data"


def main() -> None:
    import pandas as pd

    OUT.mkdir(exist_ok=True)

    birds = [dataclasses.asdict(d)
                 for d in aphis.wild_bird_detections(hpai_only=False)]
    df = pd.DataFrame(birds)
    df.to_parquet(OUT / "aphis_wild_birds.parquet", index=False)
    print("wild_birds:", len(df),
          "| hpai:", int(df["pathogenicity"].eq("High Path AI").sum()))

    stock = [dataclasses.asdict(e) for e in aphis.livestock_events()]
    df2 = pd.DataFrame(stock)
    df2.to_parquet(OUT / "aphis_livestock.parquet", index=False)
    print("livestock:", len(df2))

    flocks = [dataclasses.asdict(e) for e in aphis.flock_events()]
    df3 = pd.DataFrame(flocks)
    df3.to_parquet(OUT / "aphis_poultry.parquet", index=False)
    print("poultry flocks:", len(df3),
          "| counties:", df3["county_fips"].nunique(),
          "| birds_affected:", int(df3["birds_affected"].sum()))

    fly = aphis.flyways()
    (OUT / "aphis_flyways.json").write_text(json.dumps(fly))
    print("flyways:", len(fly))

    det_sp = [dataclasses.asdict(d) for d in aphis.wild_bird_species()]
    df4 = pd.DataFrame(det_sp)
    df4.to_parquet(OUT / "aphis_wild_birds_species.parquet", index=False)
    print("wild_birds species-level:", len(df4),
          "| species:", df4["bird_species"].nunique())

    df5 = pd.DataFrame(aphis.mammal_species())
    df5.to_parquet(OUT / "aphis_mammals_species.parquet", index=False)
    print("mammal species-level:", len(df5),
          "| species:", df5["Species"].nunique())


if __name__ == "__main__":
    main()
