#!/bin/bash
#SBATCH --job-name=risk
#SBATCH --partition=sixhour
#SBATCH --time=01:00:00
#SBATCH --cpus-per-task=4
#SBATCH --mem=48G
#SBATCH --output=logs/risk_%j.log
B="${WILDSEEK_BEEG:?set WILDSEEK_BEEG to the staging dir}"
[ -d "$B" ] || exit 53
cd "$B"
python3 build_risk.py
