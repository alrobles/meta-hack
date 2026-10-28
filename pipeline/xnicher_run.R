# xnicher campaign runner — one SLURM array task = a slice of species.
#
# Per species: env_occ (1/30-deg normals at dedup'd cells) and env_m
# (normals over its One Earth M ecoregions). For each of the 88
# (temp_i, prec_j) pairs: Box-Cox(prec, lambda fit on env_m) -> z-score
# by env_m moments -> optimize_niche(likelihood="presence_only") ->
# assess() -> AIC/BIC row. compare_xnicher() refuses cross-pair
# comparison (env_occ fingerprint) so the pair table is ranked manually
# — valid variable selection since nobs/likelihood are fixed.
#
# Usage: Rscript xnicher_run.R <cls> <shard> <n_shards> [num_starts]
# Output: fits_xnicher/fits_<cls>_<shard>.parquet (duckdb COPY) or .csv

suppressMessages({
  library(duckdb); library(DBI); library(xnicher); library(terra)
})

args <- commandArgs(trailingOnly = TRUE)
cls       <- args[1]
shard     <- as.integer(args[2])
n_shards  <- as.integer(args[3])
n_starts  <- if (length(args) >= 4) as.integer(args[4]) else 60L

OUT   <- Sys.getenv("WILDSEEK_BEEG", ".")
DOM   <- Sys.getenv("WILDSEEK_DOMAIN", "conus")
SUF   <- ifelse(DOM == "conus", "", paste0("_", DOM))
TEMPS <- paste0("bio", sprintf("%02d", 1:11))
PRECS <- paste0("bio", sprintf("%02d", 12:19))
PAIRS <- expand.grid(temp = TEMPS, prec = PRECS, stringsAsFactors = FALSE)

con <- dbConnect(duckdb())
dbExecute(con, "PRAGMA threads=4")

POOL <- Sys.getenv("WILDSEEK_POOL", "")
if (!nzchar(POOL)) POOL <- sprintf("pool_flagship%s.csv", SUF)
pool <- dbGetQuery(con, sprintf(
  "select * from '%s/%s' where class='%s'",
  OUT, POOL, if (cls == "mammalia") "Mammalia" else "Aves"))
idx <- which(pool$specieskey %% n_shards == shard)
pool <- pool[idx, , drop = FALSE]
cat("cls", cls, "shard", shard, "species:", nrow(pool), "\n")

# Box-Cox lambda on env_m precip; epsilon-shift zeros (bio14/17).
bc_lambda <- function(x, eps = NULL) {
  if (is.null(eps)) eps <- min(x[x > 0], na.rm = TRUE) / 2
  if (!is.finite(eps) || eps <= 0) eps <- 1e-3
  x <- x + eps
  lx <- log(x); m <- mean(lx)
  f <- function(l) {
    z <- if (abs(l) < 1e-8) lx else (x^l - 1) / l
    v <- var(z)
    if (!is.finite(v) || v <= 0) return(Inf)
    -0.5 * length(z) * log(v) + (l - 1) * sum(lx)
  }
  opt <- optimize(f, c(-2, 2), maximum = TRUE)
  list(lambda = opt$maximum, eps = eps)
}
bc <- function(x, lam, eps) if (abs(lam) < 1e-8) log(x + eps) else ((x + eps)^lam - 1) / lam

bios <- paste0("bio", sprintf("%02d", 1:19))
res <- list(); k <- 0L
t00 <- Sys.time()
for (si in seq_len(nrow(pool))) {
  sp <- pool$specieskey[si]; spname <- pool$species[si]
  occ <- tryCatch(dbGetQuery(con, sprintf(
    "select %s from '%s/env_occ_%s.parquet' where specieskey=%d",
    paste(bios, collapse = ","), OUT, cls, sp)), error = function(e) NULL)
  if (is.null(occ) || nrow(occ) < 10) next
  ecos <- dbGetQuery(con, sprintf(
    "select distinct eco_id from '%s/m_ecoregions.parquet' where specieskey=%d",
    OUT, sp))$eco_id
  if (length(ecos) == 0) next
  m <- dbGetQuery(con, sprintf(
    "select %s from '%s/env_normals_96_25.parquet' where eco_id in (%s)",
    paste(bios, collapse = ","), OUT, paste(ecos, collapse = ",")))
  if (nrow(m) < 50) next
  # subsample huge M for speed (transform params unaffected much)
  if (nrow(m) > 30000) m <- m[sample(nrow(m), 30000), ]

  for (pi in seq_len(nrow(PAIRS))) {
    tv <- PAIRS$temp[pi]; pv <- PAIRS$prec[pi]
    l <- bc_lambda(m[[pv]])
    mo <- bc(m[[pv]], l$lambda, l$eps); oo <- bc(occ[[pv]], l$lambda, l$eps)
    z <- function(v, a) (a - mean(v)) / sd(v)
    em <- cbind(z(m[[tv]], m[[tv]]), z(mo, mo))
    eo <- cbind(z(m[[tv]], occ[[tv]]), z(mo, oo))
    colnames(em) <- colnames(eo) <- c(tv, pv)
    if (any(!is.finite(eo))) next
    t0 <- Sys.time()
    fit <- tryCatch(
      optimize_niche(eo, em, likelihood = "presence_only",
                   num_starts = n_starts,
                   seed = (sp %% 1e6) * 89 + pi),
      error = function(e) NULL)
    if (is.null(fit)) next
    as_ <- assess(fit)
    ll  <- as.numeric(logLik(fit)); df <- attr(logLik(fit), "df")
    no  <- nobs(fit)
    k <- k + 1L
    res[[k]] <- data.frame(
      specieskey = sp, species = spname, temp = tv, prec = pv,
      lambda = l$lambda, aic = -2 * ll + 2 * df,
      bic = -2 * ll + log(no) * df, loglik = ll, nobs = no,
      flag = as_$flag, n_conv = as_$n_converged, rel_gap = as_$rel_gap,
      wall_s = as.numeric(difftime(Sys.time(), t0, units = "secs")))
  }
  if (si %% 5 == 0) cat("sp", si, "/", nrow(pool),
                      round(difftime(Sys.time(), t00, units = "mins"), 1),
                      "min\n", flush = TRUE)
}
ans <- if (k > 0) do.call(rbind, res) else data.frame()
fn <- sprintf("%s/fits_xnicher/fits_%s_%04d.csv", OUT, cls, shard)
dir.create(dirname(fn), showWarnings = FALSE, recursive = TRUE)
write.csv(ans, fn, row.names = FALSE)
cat("wrote", fn, nrow(ans), "rows\n")
