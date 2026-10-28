"""GBIF occurrence pools for the HPAI susceptibility pilot.

Runs on the HPC cluster (login node OK, ~2 min). Reads the
class-segmented gbif_thinned_120.duckdb (1/120-deg deduplicated
occurrence store on the parallel FS) and materializes, under
$WILDSEEK_BEEG (default: cwd):

  species_cell_counts.csv          per-species unique-cell counts both tiers
  occ_conus_{class}_1120.parquet   CONUS points, one per 1/120-deg cell
  occ_global_{class}_130.parquet   global cells, one per 1/30-deg cell
                                   (coarsened from the 1/120 grid, 4x4 blocks)
  pool_flagship.csv                species with >=100 unique CONUS cells
  pool_conus_endemic.csv           subset whose global footprint is CONUS-only

Filters: class in {Mammalia, Aves}, year 1996-2025, occurrencestatus PRESENT.
CONUS bbox: lat 24-49.5, lon -125 to -66 (lower 48 + margins).
"""

import os
import sys
import time

import duckdb

DB = os.environ.get("WILDSEEK_GBIF_DB", "")
if not DB:
    raise SystemExit(
        "WILDSEEK_GBIF_DB is not set: the GBIF duckdb mirror is "
        "pre-existing lab infra, not shipped with this repo. "
        "Set WILDSEEK_GBIF_DB (see docs/data-sources.md).")
OUT = os.environ.get("WILDSEEK_BEEG", ".")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import domain

DOM = domain.name()
SUF = domain.suffix()
_B = domain.get()["bbox"]
MIN_CELLS = domain.get()["min_cells"]
# second, wider pool tier: every species with enough data to fit a
# stable ellipsoid, regardless of flagship threshold
MIN_CELLS_ALL = int(os.environ.get("WILDSEEK_MIN_CELLS_ALL", "30"))
CLASSES = ["Mammalia", "Aves"]
MIN_YEAR, MAX_YEAR = 1996, 2025
CONUS = (
    f"decimallatitude between {_B[1]} and {_B[3]} "
    f"and decimallongitude between {_B[0]} and {_B[2]}"
)


def base(cls: str) -> str:
    return (
        f"from occurrences where class='{cls}' "
        f"and year between {MIN_YEAR} and {MAX_YEAR} "
        "and occurrencestatus='PRESENT' and decimallatitude is not null"
    )


def main() -> None:
    assert DB, "set WILDSEEK_GBIF_DB to the gbif_thinned_120.duckdb path"
    con = duckdb.connect(DB, read_only=True)
    con.execute("PRAGMA threads=8; PRAGMA memory_limit='40GB'")
    t0 = time.time()

    # Per-species unique-cell counts at both tiers + CONUS-endemic flag.
    con.execute(
        f"""copy (
          with occ as (
            select species, class, decimallatitude, decimallongitude,
                   lat_120, lon_120 {base('Mammalia').replace("class='Mammalia'", "class in ('Mammalia','Aves')")}
          ),
          conus as (select * from occ where {CONUS})
          select c.species, o.class,
                 count(distinct c.lat_120 || '|' || c.lon_120) n_cells_conus,
                 count(distinct floor(o.lat_120*4)/4 || '|' ||
                               floor(o.lon_120*4)/4) n_cells_global,
                 count(distinct case when not (
                     o.decimallatitude between {_B[1]} and {_B[3]} and
                     o.decimallongitude between {_B[0]} and {_B[2]})
                   then floor(o.lat_120*4)/4 || '|' || floor(o.lon_120*4)/4
                 end) n_cells_outside
          from conus c join occ o using (species)
          group by 1, 2
        ) to '{OUT}/species_cell_counts{SUF}.csv' (header, delimiter ',')"""
    )
    print("counts", round(time.time() - t0), "s")

    for cls in CLASSES:
        con.execute(
            f"""copy (select specieskey, species, decimallatitude lat,
                      decimallongitude lon, lat_120, lon_120, year,
                      basisofrecord
              {base(cls)} and {CONUS})
            to '{OUT}/occ_{DOM}_{cls.lower()}_1120.parquet'
            (format parquet, compression zstd)"""
        )
        print("conus", cls, round(time.time() - t0), "s")

    for cls in CLASSES:
        con.execute(
            f"""copy (select specieskey, species,
                      floor(lat_120*4)/4 lat_30, floor(lon_120*4)/4 lon_30,
                      count(*) n120, min(year) first_yr, max(year) last_yr
              {base(cls)} group by 1,2,3,4)
            to '{OUT}/occ_global_{cls.lower()}_130.parquet'
            (format parquet, compression zstd)"""
        )
        print("global30", cls, round(time.time() - t0), "s")

    con.close()

    # Pool tables from the counts CSV.
    con2 = duckdb.connect()
    con2.execute("PRAGMA threads=8")
    counts = f"'{OUT}/species_cell_counts{SUF}.csv'"
    con2.execute(
        f"""copy (select * from {counts} where n_cells_conus >= {MIN_CELLS})
            to '{OUT}/pool_flagship{SUF}.csv' (header, delimiter ',')"""
    )
    con2.execute(
        f"""copy (select * from {counts} where n_cells_conus >= {MIN_CELLS_ALL})
            to '{OUT}/pool_all{SUF}.csv' (header, delimiter ',')"""
    )
    con2.execute(
        f"""copy (select * from {counts}
                where n_cells_conus >= {MIN_CELLS} and n_cells_outside = 0)
            to '{OUT}/pool_{DOM}_endemic.csv' (header, delimiter ',')"""
    )
    for t in [f"pool_flagship{SUF}", f"pool_{DOM}_endemic"]:
        print(t, con2.execute(
            f"select class, count(*) from '{OUT}/{t}.csv' group by 1"
        ).fetchall())
    con2.close()


if __name__ == "__main__":
    main()
