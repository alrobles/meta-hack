#!/bin/bash
#SBATCH --job-name=reservoir
#SBATCH --partition=sixhour
#SBATCH --time=2:00:00
#SBATCH --mem=16G
#SBATCH --output=logs/reservoir_%j.log
#SBATCH --exclude=r05r10n01,r05r10n02,r05r10n03

B="${WILDSEEK_BEEG:?set WILDSEEK_BEEG to the staging dir}"
[ -d "$B" ] || exit 53
cd "$B"
export MODULESHOME=${MODULESHOME:-/kuhpc/sw/lmod/9.3}
source $MODULESHOME/init/bash
python3 build_reservoir.py && \
python3 render_3857.py reservoir/reservoir_sum.tif  reservoir/reservoir_sum.png  --cmap turbo && \
python3 render_3857.py reservoir/reservoir_max.tif  reservoir/reservoir_max.png  --cmap turbo && \
python3 render_3857.py reservoir/reservoir_prob.tif reservoir/reservoir_prob.png --cmap turbo --vmax 1
