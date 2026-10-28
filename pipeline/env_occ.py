"""Extract env_occ: 30-yr normals at each species' 1/30-deg occurrence cells.

Maps occ cells to env pixels via cell center:
  occ cell (lat_30, lon_30) -> center (+1/60) ->
  env col = floor((lon_c + 179.9)/0.1), row = floor((90 - lat_c)/0.1)
(env grid origin x=-179.9 y=90, 0.1-deg, era5land convention).

env_m is NOT materialized per species -- it is
`select * from env_normals_96_25 where eco_id in (species' M)`,
a live query on the cube. Output: env_occ_{cls}.parquet
(specieskey, lat_30, lon_30, col, row, bio01..19).

Runs under SLURM (no login-node compute).
"""

import os
import sys
import time

import duckdb

OUT = os.environ.get("WILDSEEK_BEEG", ".")
NORM = f"'{OUT}/env_normals_96_25.parquet'"


def run(cls: str) -> None:
    con = duckdb.connect()
    con.execute("PRAGMA threads=8; PRAGMA memory_limit='32GB'")
    occ = f"'{OUT}/occ_global_{cls}_130.parquet'"
    con.execute(
        f"""copy (
          with o as (
            select specieskey, species, lat_30, lon_30,
                   floor((lon_30 + 1.0/60 + 179.9) / 0.1)::int ecol,
                   floor((90 - lat_30 - 1.0/60) / 0.1)::int erow
            from {occ})
          select o.specieskey, o.species, o.lat_30, o.lon_30,
                 n.col, n.row, n.eco_id,
                 n.bio01, n.bio02, n.bio03, n.bio04, n.bio05, n.bio06,
                 n.bio07, n.bio08, n.bio09, n.bio10, n.bio11, n.bio12,
                 n.bio13, n.bio14, n.bio15, n.bio16, n.bio17, n.bio18,
                 n.bio19
          from o join {NORM} n on o.ecol = n.col and o.erow = n.row
        ) to '{OUT}/env_occ_{cls}.parquet'
        (format parquet, compression zstd)"""
    )
    print(cls, con.execute(
        f"select count(*), count(distinct specieskey), "
        f"count(*) filter (eco_id is null) no_eco "
        f"from '{OUT}/env_occ_{cls}.parquet'"
    ).fetchone())
    con.close()


if __name__ == "__main__":
    t0 = time.time()
    for c in (sys.argv[1:] or ["mammalia", "aves"]):
        run(c)
        print("elapsed", round(time.time() - t0), "s", flush=True)
