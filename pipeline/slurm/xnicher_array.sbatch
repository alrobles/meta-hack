#!/bin/bash
#SBATCH --job-name=xni_%a
#SBATCH --partition=sixhour
#SBATCH --time=06:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --output=logs/xni_%A_%a.log
#SBATCH --error=logs/xni_%A_%a.err
# array task: SLURM_ARRAY_TASK_ID = shard index
# env: CLS (mammalia|aves), N_SHARDS, N_STARTS
B="${WILDSEEK_BEEG:?set WILDSEEK_BEEG to the staging dir}"
[ -d "$B" ] || exit 53
export R_LIBS="${WILDSEEK_RLIBS:-$(dirname "$B")/rlib_442}"
SIF="${WILDSEEK_SIF:-$HOME/geospatial-rserver/xsdm_latest.sif}"
CLS=${CLS:-mammalia}
N_SHARDS=${N_SHARDS:-1}
N_STARTS=${N_STARTS:-60}
apptainer exec --cleanenv --env R_LIBS="$R_LIBS" \
  --env WILDSEEK_BEEG="$B" \
  --env WILDSEEK_DOMAIN="${WILDSEEK_DOMAIN:-conus}" \
  --env WILDSEEK_POOL="${WILDSEEK_POOL:-}" \
  --bind "$(dirname "$(dirname "$B")")" "$SIF" \
  Rscript "$B/xnicher_run.R" \
  "$CLS" "$SLURM_ARRAY_TASK_ID" "$N_SHARDS" "$N_STARTS"
