"""Build per-species M (accessible area) from One Earth ecoregions.

M = smallest union of ecoregions containing >=95% of a species' deduplicated
1/30-deg presence cells (occ_global_*_130.parquet). Ecoregions are already
rasterized to the same grid (eco_raster_130.csv.gz), so the spatial join is
an equality merge on integer bins -- no geospatial deps on the cluster.

Stage A (--counts): species x ecoregion cell counts.
  Optionally shard with --shard i/N (for a SLURM array); shards split on
  specieskey hash. Each shard writes species_eco_counts_{cls}_{i}.parquet.

Stage B (--reduce): rank ecoregions per species by cell share, cumulative
  >=95% -> m_ecoregions.parquet (specieskey, eco_id, share, cum_share) plus
  m_summary.parquet per species: n_eco_m, coverage, n_unmatched_cells
  (cells over ocean/no ecoregion -> marine flag), log ready for the T1 agent.

DuckDB-only; runs on the login node in minutes. To run as an array:
  sbatch --array=0-7 pipeline/slurm/m_counts.sbatch   # N=8 shards
"""

import argparse
import os
import time

import duckdb

OUT = os.environ.get("WILDSEEK_BEEG", ".")
CLASSES = ["mammalia", "aves"]
BIN = "round((lat_30+90)*30)::int lat_bin, round((lon_30+180)*30)::int lon_bin"
ECO = f"'{OUT}/eco_raster_130.csv.gz'"


def counts(cls: str, shard_i: int, n_shards: int) -> None:
    con = duckdb.connect()
    con.execute("PRAGMA threads=8; PRAGMA memory_limit='40GB'")
    occ = f"'{OUT}/occ_global_{cls}_130.parquet'"
    where = (
        f"and hash(specieskey) % {n_shards} = {shard_i}" if n_shards > 1 else ""
    )
    con.execute(
        f"""copy (
          select o.specieskey, o.species, e.eco_id, count(*) n_cells
          from (select specieskey, species, {BIN} from {occ} where 1=1 {where}) o
          join (select lat_bin, lon_bin, eco_id from {ECO}) e
            on o.lat_bin = e.lat_bin and o.lon_bin = e.lon_bin
          group by 1,2,3
        ) to '{OUT}/species_eco_counts_{cls}_{shard_i}.parquet'
        (format parquet, compression zstd)"""
    )
    # Unmatched cells (ocean / rasterized-out) per species for the marine flag.
    con.execute(
        f"""copy (
          select o.specieskey, count(*) n_unmatched
          from (select specieskey, {BIN} from {occ} where 1=1 {where}) o
          anti join (select lat_bin, lon_bin from {ECO}) e
            on o.lat_bin = e.lat_bin and o.lon_bin = e.lon_bin
          group by 1
        ) to '{OUT}/species_eco_unmatched_{cls}_{shard_i}.parquet'
        (format parquet, compression zstd)"""
    )
    con.close()


def reduce() -> None:
    con = duckdb.connect()
    con.execute("PRAGMA threads=8; PRAGMA memory_limit='40GB'")
    pats = ",".join(f"'{OUT}/species_eco_counts_{c}_*.parquet'" for c in CLASSES)
    upat = ",".join(f"'{OUT}/species_eco_unmatched_{c}_*.parquet'" for c in CLASSES)
    meta = f"(select distinct eco_id, eco_name, realm, biome_name from {ECO})"
    # Rank ecoregions per species, keep those needed to reach 95% cumulative.
    con.execute(
        f"""copy (
          with tot as (
            select specieskey, species, eco_id, sum(n_cells) n
            from read_parquet([{pats}]) group by 1,2,3),
          rk as (
            select *, sum(n) over w / sum(n) over (partition by specieskey)
                   shr,
                   sum(n) over w / sum(n) over (partition by specieskey)
                   - n / sum(n) over (partition by specieskey) cum_prev
            from tot
            window w as (partition by specieskey order by n desc, eco_id
                         rows unbounded preceding))
          select r.specieskey, r.species, m.eco_name, m.realm, m.biome_name,
                 r.eco_id, r.n, r.shr
          from rk r join {meta} m using (eco_id)
          where r.cum_prev < 0.95
          order by r.specieskey, r.n desc
        ) to '{OUT}/m_ecoregions.parquet'
        (format parquet, compression zstd)"""
    )
    con.execute(
        f"""copy (
          select m.specieskey, any_value(m.species) species,
                 count(*) n_eco_m, sum(m.n) n_cells_m,
                 coalesce(any_value(u.n_unmatched), 0) n_unmatched
          from '{OUT}/m_ecoregions.parquet' m
          left join read_parquet([{upat}]) u using (specieskey)
          group by 1
        ) to '{OUT}/m_summary.parquet'
        (format parquet, compression zstd)"""
    )
    print(
        "m_summary:",
        con.execute(
            f"""select count(*), avg(n_eco_m)::int,
                count(*) filter (n_unmatched > n_cells_m) marine_ish
                from '{OUT}/m_summary.parquet'"""
        ).fetchone(),
    )
    con.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--counts", action="store_true")
    p.add_argument("--reduce", action="store_true")
    p.add_argument("--cls", choices=CLASSES)
    p.add_argument("--shard", type=int, default=0)
    p.add_argument("--n-shards", type=int, default=1)
    a = p.parse_args()
    t0 = time.time()
    if a.counts:
        for c in ([a.cls] if a.cls else CLASSES):
            counts(c, a.shard, a.n_shards)
            print("counts", c, f"shard{a.shard}/{a.n_shards}",
                  round(time.time() - t0), "s")
    if a.reduce:
        reduce()
        print("reduce", round(time.time() - t0), "s")
