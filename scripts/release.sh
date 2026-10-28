#!/usr/bin/env bash
# release.sh "slug" — gate de revision Muse + push curado al repo publico.
#
# Flujo:  devel (origin, privado) -> revision muse-spark-1.3 -> public (meta-hack)
#
# El repo publico es un SNAPSHOT curado: cada release publica un commit
# raiz con el arbol filtrado (paths de scripts/release.exclude eliminados).
# El historial de devel NUNCA viaja al publico — un force-push limpio
# garantiza que archivos internos no queden en blobs alcanzables.
#
# El gate es bloqueante: sin un veredicto SHIP explicito (o si hermes
# falla) no se empuja nada a public/main.
set -euo pipefail
cd "$(dirname "$0")/.."

SLUG="${1:-release}"
STAMP=$(date +%F)
OUT="docs/muse-reviews/${STAMP}-${SLUG}.md"
EXCLUDE="scripts/release.exclude"

# --- arbol filtrado de HEAD en un indice temporal -----------------------
IDX=$(mktemp /tmp/wildseek-idx.XXXX)
trap 'rm -f "$IDX" /tmp/wildseek-release.diff' EXIT
export GIT_INDEX_FILE="$IDX"
git read-tree HEAD
while IFS= read -r pat; do
    [ -z "$pat" ] || [ "${pat#\#}" != "$pat" ] || \
        git rm -r -q --cached --ignore-unmatch "$pat" 2>/dev/null || true
done < "$EXCLUDE"
TREE=$(git write-tree)
unset GIT_INDEX_FILE

# --- diff vs el tip publico (tambien filtrado) ---------------------------
git fetch -q -p public || true
BASE=$(git rev-parse -q --verify public/main 2>/dev/null \
    || git hash-object -t tree /dev/null)
git diff "$BASE" "$TREE" > /tmp/wildseek-release.diff
if [ ! -s /tmp/wildseek-release.diff ]; then
    echo "nada que soltar (arbol filtrado == public/main)"; exit 0
fi
LINES=$(wc -l < /tmp/wildseek-release.diff)

# --- review muse --------------------------------------------------------
mkdir -p docs/muse-reviews
VERDICT=FAIL
if command -v hermes >/dev/null && [ "$LINES" -lt 40000 ]; then
    PROMPT=$(mktemp /tmp/muse-review.XXXX.md)
    cat > "$PROMPT" <<EOF
Revisa este diff de release del repo WildSeek (hackathon). Encuentra
defectos concretos fichero:linea: bugs, secretos, rutas absolutas
personales, datos internos que no deban ser publicos, dead code que
rompa. Si esta limpio, dilo. Formato: lista de hallazgos, luego
veredicto SHIP/NO-SHIP.

$(cat /tmp/wildseek-release.diff)
EOF
    if hermes -p meta-hack chat -Q -m muse-spark-1.3 --query-file "$PROMPT" \
        | tee "$OUT"; then
        # anchor on the verdict line only — the body may quote older
        # NO-SHIP verdicts and plain SHIP is a substring of NO-SHIP
        VLINE=$(grep -iE 'veredicto|verdict' "$OUT" | tail -1 || true)
        if echo "$VLINE" | grep -qiE 'NO[- ]?SHIP'; then VERDICT=NO-SHIP
        elif echo "$VLINE" | grep -qi 'SHIP'; then VERDICT=SHIP; fi
    else
        echo "muse cayo; review incompleta en $OUT" | tee -a "$OUT"
    fi
    rm -f "$PROMPT"
else
    echo "hermes no disponible o diff grande ($LINES lineas)" | tee "$OUT"
fi
echo "review -> $OUT  [veredicto: $VERDICT]"

# --- devel siempre recibe la review; el publico solo si SHIP -------------
git add docs/muse-reviews
git commit -qm "docs: muse review ${SLUG}" || true
git push -q origin main

if [ "$VERDICT" != "SHIP" ]; then
    echo "BLOQUEADO: veredicto $VERDICT — public/main intacto." >&2
    exit 1
fi

MSG="release ${STAMP} (${SLUG}) — $(git rev-parse --short HEAD)"
COMMIT=$(git commit-tree "$TREE" -m "$MSG")
# fresh/recreated remote: no main to lease against -> plain create;
# otherwise force-with-lease guards against unexpected remote moves
if git rev-parse -q --verify public/main >/dev/null 2>&1; then
    git push --force-with-lease public "$COMMIT:refs/heads/main"
else
    git push public "$COMMIT:refs/heads/main"
fi
echo "soltado a public/main (${COMMIT:0:8}, arbol curado sin historial)"
