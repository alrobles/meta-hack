"""Collect xnicher shard outputs into the fit catalog.

Merges fits_xnicher/fits_{cls}_*.csv -> fit_catalog.parquet, then per
species picks the winning (temp, prec) pair: min AIC among rows with
assess()$flag == "accepted_global" (fallback: accepted_noise, then any
fit) -> species_winner.parquet. Also emits per-species campaign stats
for the T1 decision layer.
"""

import os

import duckdb

OUT = os.environ.get("WILDSEEK_BEEG", ".")


def main() -> None:
    con = duckdb.connect()
    con.execute("PRAGMA threads=8")
    con.execute(
        f"""copy (select * from read_csv('{OUT}/fits_xnicher/fits_*.csv',
                  union_by_name=true, filename=true))
            to '{OUT}/fit_catalog.csv' (header, delimiter ',')"""
    )
    con.execute(
        f"""copy (
          with ranked as (
            select *, rank() over (partition by specieskey
                   order by aic asc) rk_aic,
              flag in ('accepted_global','accepted_noise') ok_flag
            from '{OUT}/fit_catalog.csv'),
          win as (
            select * from ranked where ok_flag
            qualify rank() over (partition by specieskey
                                 order by aic) = 1)
          select w.specieskey, w.species, w.temp best_temp,
                 w.prec best_prec, w.aic, w.bic, w.loglik, w."lambda",
                 w.n_conv, w.rel_gap, w.flag,
                 (select count(*) from ranked r
                   where r.specieskey = w.specieskey) n_pairs_tried,
                 (select count(*) from ranked r
                   where r.specieskey = w.specieskey and r.ok_flag) n_accepted
          from win w
        ) to '{OUT}/species_winner.csv' (header, delimiter ',')"""
    )
    print(con.execute(
        f"""select flag, count(*),
            count(distinct specieskey) spp
            from '{OUT}/fit_catalog.csv' group by 1 order by 2 desc"""
    ).fetchall())
    print("winners:", con.execute(
        f"select count(*) from '{OUT}/species_winner.csv'"
    ).fetchone())
    con.close()


if __name__ == "__main__":
    main()
