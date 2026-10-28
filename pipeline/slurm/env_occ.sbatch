#!/bin/bash
#SBATCH --job-name=env_occ
#SBATCH --partition=sixhour
#SBATCH --time=01:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=36G
#SBATCH --output=logs/env_occ_%j.log
#SBATCH --error=logs/env_occ_%j.err

# env_occ extraction — runs on a compute node, never login.
# Parallel-FS sanity: some nodes lack the mount (KB: exit 0:53) — check first.
B="${WILDSEEK_BEEG:?set WILDSEEK_BEEG to the staging dir}"
if [ ! -d "$B" ]; then
  echo "staging dir not reachable on $SLURMD_NODENAME" >&2
  exit 53
fi

PY="${WILDSEEK_PY:-$(dirname "$B")/miniconda3/envs/genoml-phylo/bin/python3}"
cd "$B"
"$PY" env_occ.py mammalia aves
