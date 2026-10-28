#!/bin/bash
#SBATCH --job-name=ws-refit
#SBATCH --partition=sixhour
#SBATCH --time=03:00:00
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --exclude=r05r10n01,r05r10n02,r05r10n03
#SBATCH --output=logs/refit_%j.log
B="${WILDSEEK_BEEG:?set WILDSEEK_BEEG to the staging dir}"
[ -d "$B" ] || exit 53
cd "$B"
set -x
# labels/dist_frame/model run locally (GBIF API + sklearn venv);
# this chain picks up the uploaded susceptibility.parquet
python3 build_reservoir.py && \
python3 build_risk.py && \
python3 risk_watershed.py && \
python3 county_exposure.py && \
python3 render_3857.py risk_1120.tif risk.png --cmap turbo --vmax 1 && \
python3 render_3857.py reservoir/reservoir_sum.tif reservoir/reservoir_sum.png --cmap turbo && \
python3 render_3857.py reservoir/reservoir_max.tif reservoir/reservoir_max.png --cmap turbo && \
python3 render_3857.py reservoir/reservoir_prob.tif reservoir/reservoir_prob.png --cmap turbo --vmax 1 && \
python3 export_serve.py --src "$B" --dst "$B/serve_data"
