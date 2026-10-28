"""ERA5-land bioclim data cube keyed to One Earth ecoregions.

Builds an operational cell-level env table: every land pixel of the
0.1-deg era5land_bioclim grid, per year, with its ecoregion id — so
"env for ecoregions X,Y,Z" (a species' M) or "by bioregion/realm" is a
plain DuckDB query, not a geoprocess.

Layout under $WILDSEEK_BEEG (default: cwd):
  env_cube_raw/year=YYYY.parquet   col,row + bio01..19 (melted GeoTIFF)
  env_cell_map.parquet             col,row,lat,lon -> eco_id,eco_name,realm,biome
  env_cube/year=YYYY.parquet       raw + eco_id joined (final cube)
  env_normals_96_25.parquet        30-yr mean per cell (env_m source)

Phases (run any subset):
  --melt      GeoTIFF -> raw parquet shards (rasterio; shardable --shard i/N by year)
  --cellmap   distinct land cells -> ecoregion via center-point -> eco_raster_130 bins
  --enrich    raw shards + cell_map -> final cube (hive-partitioned)
  --normals   mean over --years per cell
"""

import argparse
import glob
import os
import time

import duckdb
import numpy as np
import rasterio

OUT = os.environ.get("WILDSEEK_BEEG", ".")
# era5 bioclim store: sibling dir of the staging root unless overridden
_ERA5 = os.environ.get(
    "WILDSEEK_ERA5",
    os.path.join(os.path.dirname(os.path.abspath(OUT)), "era5_bioclim"))
SRC = f"{_ERA5}/v1/era5land_bioclim_{{year}}.tif"
ECO = f"'{OUT}/eco_raster_130.csv.gz'"
BIOS = [f"bio{str(i).zfill(2)}" for i in range(1, 20)]


def year_list(a: argparse.Namespace) -> list[int]:
    if a.years:
        lo, hi = a.years.split("-")
        ys = list(range(int(lo), int(hi) + 1))
    else:
        ys = sorted(
            int(p.rsplit("_", 1)[1].split(".")[0])
            for p in glob.glob(SRC.format(year="*"))
        )
    if a.n_shards > 1:
        ys = [y for i, y in enumerate(ys) if i % a.n_shards == a.shard]
    return ys


def melt(year: int) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    with rasterio.open(SRC.format(year=year)) as r:
        t = r.transform  # (0.1, 0, -179.9, 0, -0.1, 90)
        arr = r.read()  # (19, 1801, 3600) float32, NaN over ocean
    land = np.isfinite(arr[0])
    rows, cols = np.where(land)
    table = pa.table(
        {
            "col": pa.array(cols.astype(np.int16)),
            "row": pa.array(rows.astype(np.int16)),
            "year": pa.array(np.full(land.sum(), year, dtype=np.int16)),
            **{
                b: pa.array(arr[i][land].astype(np.float32))
                for i, b in enumerate(BIOS)
            },
        }
    )
    pq.write_table(table, f"{OUT}/env_cube_raw/year_{year}.parquet",
                   compression="zstd")
    if not hasattr(melt, "_logged"):
        print("transform:", tuple(round(v, 6) for v in t[:6]))
        melt._logged = True


def cellmap() -> None:
    con = duckdb.connect()
    con.execute("PRAGMA threads=8; PRAGMA memory_limit='40GB'")
    con.execute(
        f"""copy (
          with cells as (
            select distinct col, row
            from read_parquet('{OUT}/env_cube_raw/*.parquet')),
          pts as (
            select col, row,
                   -179.9 + (col + 0.5) * 0.1 lon_c,
                   90.0 - (row + 0.5) * 0.1 lat_c from cells)
          select p.col, p.row, p.lat_c, p.lon_c,
                 e.eco_id, e.eco_name, e.realm, e.biome_name
          from pts p
          left join (select lat_bin, lon_bin, eco_id, eco_name, realm,
                            biome_name from {ECO}) e
            on floor((p.lat_c + 90) * 30) = e.lat_bin
           and floor((p.lon_c + 180) * 30) = e.lon_bin
        ) to '{OUT}/env_cell_map.parquet' (format parquet, compression zstd)"""
    )
    print("cell_map:", con.execute(
        f"select count(*), count(eco_id) n_match from '{OUT}/env_cell_map.parquet'"
    ).fetchone())
    con.close()


def enrich() -> None:
    con = duckdb.connect()
    con.execute("PRAGMA threads=8; PRAGMA memory_limit='40GB'")
    con.execute(
        f"""copy (
          select c.col, c.row, m.eco_id, c.year, c.bio01, c.bio02, c.bio03,
                 c.bio04, c.bio05, c.bio06, c.bio07, c.bio08, c.bio09,
                 c.bio10, c.bio11, c.bio12, c.bio13, c.bio14, c.bio15,
                 c.bio16, c.bio17, c.bio18, c.bio19
          from read_parquet('{OUT}/env_cube_raw/*.parquet',
                            union_by_name=true) c
          left join '{OUT}/env_cell_map.parquet' m
            on c.col = m.col and c.row = m.row
        ) to '{OUT}/env_cube'
        (format parquet, compression zstd, partition_by (year),
         overwrite true)"""
    )
    print("cube rows:", con.execute(
        f"select count(*) from read_parquet('{OUT}/env_cube/**/*.parquet', hive_partitioning=true)"
    ).fetchone()[0])
    con.close()


def normals(years: list[int]) -> None:
    """30-yr mean per cell via numpy accumulation — a DuckDB 19-aggregate
    groupby over 66M rows was OOM-killed; the raw shards are already one
    row per cell-year so summation is enough (~2 GB resident)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    lo, hi = min(years), max(years)
    con = duckdb.connect()
    con.execute("PRAGMA threads=8")
    s = np.zeros((1801, 3600, 19), np.float64)
    c = np.zeros((1801, 3600), np.int32)
    for y in years:
        t = con.execute(
            f"select col, row, {', '.join(BIOS)} "
            f"from '{OUT}/env_cube_raw/year_{y}.parquet'"
        ).to_arrow_table()
        col, row = t["col"].to_numpy(), t["row"].to_numpy()
        x = np.stack([t[b].to_numpy() for b in BIOS], axis=1)
        np.add.at(s, (row, col), x)
        np.add.at(c, (row, col), 1)
    with np.errstate(invalid="ignore"):
        m = s / c[..., None]
    valid = c > 0
    rows, cols = np.where(valid)
    cm = con.execute(
        f"select col, row, eco_id from '{OUT}/env_cell_map.parquet'"
    ).to_arrow_table()
    eco = np.full((1801, 3600), -1, np.int64)  # -1 = outside ecoregions
    ev = cm["eco_id"].to_numpy(zero_copy_only=False)
    eco[cm["row"].to_numpy(), cm["col"].to_numpy()] = np.where(
        np.isnan(ev), -1, ev
    )
    out = pa.table(
        {
            "col": cols.astype(np.int16),
            "row": rows.astype(np.int16),
            "eco_id": eco[rows, cols],
            **{b: m[rows, cols, i].astype(np.float32)
               for i, b in enumerate(BIOS)},
        }
    )
    pq.write_table(
        out, f"{OUT}/env_normals_{str(lo)[2:]}_{str(hi)[2:]}.parquet",
        compression="zstd",
    )
    con.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--melt", action="store_true")
    p.add_argument("--cellmap", action="store_true")
    p.add_argument("--enrich", action="store_true")
    p.add_argument("--normals", action="store_true")
    p.add_argument("--years", help="e.g. 1996-2025; default = all on disk")
    p.add_argument("--shard", type=int, default=0)
    p.add_argument("--n-shards", type=int, default=1)
    a = p.parse_args()
    ys = year_list(a)
    t0 = time.time()
    if a.melt:
        import os
        os.makedirs(f"{OUT}/env_cube_raw", exist_ok=True)
        for y in ys:
            melt(y)
            print("melted", y, round(time.time() - t0), "s", flush=True)
    if a.cellmap:
        cellmap()
    if a.enrich:
        enrich()
    if a.normals:
        normals(ys)
        print("normals", round(time.time() - t0), "s")
