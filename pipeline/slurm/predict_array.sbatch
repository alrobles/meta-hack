#!/bin/bash
#SBATCH --job-name=pred_%a
#SBATCH --partition=sixhour
#SBATCH --time=06:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --output=logs/pred_%A_%a.log
#SBATCH --error=logs/pred_%A_%a.err
# array task: SLURM_ARRAY_TASK_ID = shard index; env N_SHARDS
B="${WILDSEEK_BEEG:?set WILDSEEK_BEEG to the staging dir}"
[ -d "$B" ] || exit 53
export R_LIBS="${WILDSEEK_RLIBS:-$(dirname "$B")/rlib_442}"
SIF="${WILDSEEK_SIF:-$HOME/geospatial-rserver/xsdm_latest.sif}"
N_SHARDS=${N_SHARDS:-1}
# bind the parallel-FS root (grandparent of the staging dir) so the
# container sees $B and the sibling R_LIBS tree
apptainer exec --cleanenv --env R_LIBS="$R_LIBS" \
  --env WILDSEEK_BEEG="$B" \
  --env WILDSEEK_DOMAIN="${WILDSEEK_DOMAIN:-conus}" \
  --bind "$(dirname "$(dirname "$B")")" "$SIF" \
  Rscript "$B/predict_suit.R" "$SLURM_ARRAY_TASK_ID" "$N_SHARDS"
