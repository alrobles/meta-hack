#!/bin/bash
#SBATCH --job-name=finish2
#SBATCH --partition=sixhour
#SBATCH --time=03:00:00
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --exclude=r05r10n01,r05r10n02,r05r10n03
#SBATCH --output=logs/finish_%j.log
#SBATCH --error=logs/finish_%j.err
B="${WILDSEEK_BEEG:?set WILDSEEK_BEEG to the staging dir}"
[ -d "$B" ] || exit 53
cd "$B"
mkdir -p suit_png serve_data
set -x

echo "=== RISK $(date -Is)"
python3 build_risk.py
echo "=== WATERSHED $(date -Is)"
python3 risk_watershed.py
echo "=== COUNTY EXPOSURE $(date -Is)"
python3 county_exposure.py
echo "=== RENDER RISK $(date -Is)"
python3 render_3857.py risk_1120.tif risk.png --cmap turbo --vmax 1
echo "=== RENDER 3857 $(date -Is)"
# skip-existing by default; FORCE_RERENDER=1 regenerates the stack
# (e.g. after the M-mask change, stale pre-mask PNGs need replacing)
ls suit/suit_*.tif | xargs -P 8 -I{} sh -c \
  'b=$(basename {} .tif); { [ -f suit_png/$b.png ] && [ "${FORCE_RERENDER:-0}" != 1 ]; } || python3 render_3857.py {} suit_png/$b.png'
echo "=== EXPORT $(date -Is)"
python3 export_serve.py --src "$B" --dst "$B/serve_data"
echo "=== DONE $(date -Is)"
