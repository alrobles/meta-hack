#!/bin/bash
# domain_build.sh — submit the full per-domain build chain on kuhpc.
#
#   bash domain_build.sh na_ca          # Canada->Panama build
#   WILDSEEK_BEEG=/path bash domain_build.sh conus
#
# Stages (each afterok-chained; WILDSEEK_DOMAIN exported to all):
#   pool      gbif_pools.py            -> occ_<dom>_*, pool_flagship<SUF>
#   normals   conus_normals.py         -> env_<dom>_normals_1120.tif (19-band)
#   eco       eco_conus.py             -> eco_<dom>_1120.tif
#   xni       xnicher_array (both cls) -> fits_xnicher/fits_*
#   collect   collect_fits.py          -> fit_catalog + species_winner (union)
#   phy       name_reconcile + phy_dist*.R + dist_frame.py
#   model     suscept retrain          -> susceptibility.parquet
#   predict   predict_array            -> suit rasters over the domain
#   audit     export_evidence.R array  -> audit_export/ (all winner species)
#
# env_occ is NOT a stage: it reads occ_global_* (already global — the
# new pool species' occurrence envs are already materialized).
set -euo pipefail
DOM=${1:?usage: domain_build.sh <domain-name e.g. na_ca>}
B="${WILDSEEK_BEEG:?set WILDSEEK_BEEG to the staging dir}"
cd "$B"   # slurm scripts run inside staging (copied there by sync)
export WILDSEEK_DOMAIN="$DOM"
EXP="--export=ALL,WILDSEEK_DOMAIN=$DOM,WILDSEEK_BEEG=$B"
N_SHARDS=${N_SHARDS:-24}
# KB 0:53: node families without the beegfs mount — exclude via env,
# e.g. WILDSEEK_EXCLUDE='r05r10n[01-04]'
EXC=""; [ -n "${WILDSEEK_EXCLUDE:-}" ] && EXC="--exclude=$WILDSEEK_EXCLUDE"

submit() { # name deps cmd...
  local name=$1 deps=$2; shift 2
  local dep=""; [ -n "$deps" ] && dep="--dependency=afterok:$deps"
  sbatch --parsable $EXP $EXC --job-name="dom_${DOM}_${name}" $dep \
    --output="logs/${name}_${DOM}_%j.log" --error="logs/${name}_${DOM}_%j.err" "$@"
}

# Parallel-FS sanity (KB 0:53): wraps check the mount before running.
GUARD="[ -d \"$B\" ] || { echo \"no beegfs on \$SLURMD_NODENAME\" >&2; exit 53; }"

mkdir -p logs

J_POOL=$(submit pool "" --partition=sixhour --time=04:00:00 --mem=48G --cpus-per-task=8 --wrap \
  "$GUARD ; \"${WILDSEEK_PY:-python3}\" gbif_pools.py")

J_NORM=$(submit normals "" --partition=sixhour --time=06:00:00 --mem=16G --cpus-per-task=4 --wrap \
  "$GUARD ; \"${WILDSEEK_PY:-python3}\" conus_normals.py")

J_ECO=$(submit eco "$J_NORM" --partition=sixhour --time=01:00:00 --mem=40G --cpus-per-task=4 --wrap \
  "$GUARD ; \"${WILDSEEK_PY:-python3}\" eco_conus.py")

J_XNI_A=$(sbatch --parsable $EXC --job-name="dom_${DOM}_xni_aves" \
  --partition=sixhour --time=06:00:00 --mem=16G --cpus-per-task=4 \
  --array=0-$((N_SHARDS-1)) --dependency=afterok:$J_POOL \
  --export=ALL,WILDSEEK_DOMAIN=$DOM,WILDSEEK_BEEG=$B,CLS=aves,N_SHARDS=$N_SHARDS,N_STARTS=60 \
  --output="logs/xni_aves_${DOM}_%A_%a.log" --error="logs/xni_aves_${DOM}_%A_%a.err" \
  xnicher_array.sbatch)
J_XNI_M=$(sbatch --parsable $EXC --job-name="dom_${DOM}_xni_mam" \
  --partition=sixhour --time=06:00:00 --mem=16G --cpus-per-task=4 \
  --array=0-$((N_SHARDS-1)) --dependency=afterok:$J_POOL \
  --export=ALL,WILDSEEK_DOMAIN=$DOM,WILDSEEK_BEEG=$B,CLS=mammalia,N_SHARDS=$N_SHARDS,N_STARTS=60 \
  --output="logs/xni_mam_${DOM}_%A_%a.log" --error="logs/xni_mam_${DOM}_%A_%a.err" \
  xnicher_array.sbatch)

J_COLLECT=$(submit collect "$J_XNI_A:$J_XNI_M" --partition=sixhour --time=01:00:00 --mem=32G --cpus-per-task=8 --wrap \
  "$GUARD ; \"${WILDSEEK_PY:-python3}\" collect_fits.py")

J_PHY=$(submit phy "$J_COLLECT" --partition=sixhour --time=04:00:00 --mem=48G --cpus-per-task=8 --wrap \
  "$GUARD ; \"${WILDSEEK_PY:-python3}\" name_reconcile.py \
   && Rscript phy_dist.R && Rscript phy_dist_birds.R \
   && \"${WILDSEEK_PY:-python3}\" dist_frame.py")

J_MODEL=$(submit model "$J_PHY" --partition=sixhour --time=01:00:00 --mem=32G --cpus-per-task=8 --wrap \
  "$GUARD ; \"${WILDSEEK_PY:-python3}\" suscept_model.py")

J_PRED=$(sbatch --parsable $EXC --job-name="dom_${DOM}_predict" \
  --partition=sixhour --time=06:00:00 --mem=24G --cpus-per-task=4 \
  --array=0-$((N_SHARDS-1)) --dependency=afterok:$J_MODEL:$J_ECO \
  --export=ALL,WILDSEEK_DOMAIN=$DOM,WILDSEEK_BEEG=$B,N_SHARDS=$N_SHARDS \
  --output="logs/predict_${DOM}_%A_%a.log" --error="logs/predict_${DOM}_%A_%a.err" \
  predict_array.sbatch)

J_AUDIT=$(submit audit "$J_COLLECT" --partition=sixhour --time=06:00:00 --mem=16G --cpus-per-task=4 --wrap \
  "export R_LIBS=\"\${WILDSEEK_RLIBS:-\$(dirname \"$B\")/rlib_442}\" ; \
   apptainer exec --cleanenv --env R_LIBS=\"\$R_LIBS\" \
     --env WILDSEEK_BEEG=\"$B\" --env WILDSEEK_DOMAIN=\"$DOM\" \
     --bind \"\$(dirname \"\$(dirname \"$B\")\")\" \
     \"\${WILDSEEK_SIF:-\$HOME/geospatial-rserver/xsdm_latest.sif}\" \
     Rscript \"$B/export_evidence.R\" species_winner.csv audit_export_${DOM} 5")

echo "domain $DOM chain submitted:"
echo "  pool=$J_POOL normals=$J_NORM eco=$J_ECO xni=($J_XNI_A,$J_XNI_M)"
echo "  collect=$J_COLLECT phy=$J_PHY model=$J_MODEL predict=$J_PRED audit=$J_AUDIT"
