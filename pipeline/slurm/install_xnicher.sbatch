#!/bin/bash
#SBATCH --job-name=inst_xnicher
#SBATCH --partition=sixhour
#SBATCH --time=02:00:00
#SBATCH --mem=8G
#SBATCH --cpus-per-task=2
#SBATCH --output=logs/install_xnicher_%j.log
B="${WILDSEEK_BEEG:?set WILDSEEK_BEEG to the staging dir}"
[ -d "$B" ] || exit 53
export R_LIBS="${WILDSEEK_RLIBS:-$(dirname "$B")/rlib_442}"
mkdir -p "$R_LIBS"
SIF="${WILDSEEK_SIF:-$HOME/geospatial-rserver/xsdm_latest.sif}"
SRC="$R_LIBS/srcpkgs"
apptainer exec --cleanenv --env R_LIBS="$R_LIBS" "$SIF" R --slave -e "
lib <- Sys.getenv('R_LIBS'); src <- '$SRC'
pkgs <- c('Rcpp','RcppEigen','RcppParallel','checkmate','xnicher')
for (p in pkgs) {
  if (!requireNamespace(p, quietly=TRUE)) {
    f <- list.files(src, pattern=paste0('^',p,'_'), full.names=TRUE)[1]
    cat('installing', p, 'from', basename(f), '\n')
    install.packages(f, lib=lib, repos=NULL, type='source', Ncpus=2)
  } else cat(p, 'already present\n')
}
stopifnot(requireNamespace('xnicher', quietly=TRUE))
cat('OK xnicher', as.character(packageVersion('xnicher')), '\n')
"
