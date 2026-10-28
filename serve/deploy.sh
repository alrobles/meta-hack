#!/bin/bash
# Deploy WildSeek to the serve host (ecoseek stack pattern).
# Run from the repo root on the dev machine:
#   WILDSEEK_DEPLOY_HOST=myhost WILDSEEK_DEPLOY_DIR=/srv/wildseek \
#     bash serve/deploy.sh
set -euo pipefail
HOST="${WILDSEEK_DEPLOY_HOST:?set WILDSEEK_DEPLOY_HOST}"
DEST="${WILDSEEK_DEPLOY_DIR:?set WILDSEEK_DEPLOY_DIR}"

echo "== rsync code =="
rsync -a --delete --exclude=.git --exclude=data --exclude='*.tif' \
    --exclude='__pycache__' api agent app serve pipeline "$HOST:$DEST/"

echo "== rsync serve data (app/data) =="
rsync -a app/data/ "$HOST:$DEST/app/data/"

echo "== docker build + up =="
ssh "$HOST" "cd $DEST/serve && docker compose build --quiet && docker compose up -d"
ssh "$HOST" "sleep 3 && curl -s http://127.0.0.1:8654/health"

cat <<'EOF'

== public route (one-time, on terminal) ==
cloudflared tunnel route dns <existing-tunnel-id> wildseek.ecoseek.org
# or create dedicated:
#   cloudflared tunnel create wildseek
#   -> writes credentials json + tunnel id
# add to ~/.cloudflared/wildseek.yml:
#   tunnel: <id>
#   credentials-file: $HOME/.cloudflared/<id>.json
#   ingress:
#     - hostname: wildseek.ecoseek.org
#       service: http://127.0.0.1:8654
#     - service: http_status:404
# then run it like the other tunnels.
EOF
