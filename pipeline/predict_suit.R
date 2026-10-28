# Predict suitability rasters for winner species (CONUS 1/120-deg).
#
# Winner params come from species_winner.csv (best pair + lambda);
# the fit is re-fit deterministically (same seed as the campaign)
# then predict() over the transformed CONUS normals stack.
# Usage: Rscript predict_suit.R <shard> <n_shards>  (array task)

suppressMessages({
  library(duckdb); library(DBI); library(xnicher); library(terra)
})

args <- commandArgs(trailingOnly = TRUE)
shard <- as.integer(args[1]); n_shards <- as.integer(args[2])
n_starts <- 60L
OUT <- Sys.getenv("WILDSEEK_BEEG", ".")
DOM <- Sys.getenv("WILDSEEK_DOMAIN", "conus")
SUF <- ifelse(DOM == "conus", "", paste0("_", DOM))
bios <- paste0("bio", sprintf("%02d", 1:19))

con <- dbConnect(duckdb()); dbExecute(con, "PRAGMA threads=4")
win <- dbGetQuery(con, sprintf(
  "select w.*, p.class from '%s/species_winner.csv' w
   join '%s/pool_flagship%s.csv' p using (specieskey)", OUT, OUT, SUF))
win <- win[win$specieskey %% n_shards == shard, ]
cat("shard", shard, "species:", nrow(win), "\n")

norm <- rast(sprintf("%s/env_%s_normals_1120.tif", OUT, DOM))
eco_r <- rast(sprintf("%s/eco_%s_1120.tif", OUT, DOM))

bc <- function(x, lam, eps) if (abs(lam) < 1e-8) log(x + eps) else ((x + eps)^lam - 1) / lam
bc_lambda <- function(x) {
  eps <- min(x[x > 0], na.rm = TRUE) / 2; if (!is.finite(eps) || eps <= 0) eps <- 1e-3
  x <- x + eps; lx <- log(x)
  f <- function(l) { z <- if (abs(l) < 1e-8) lx else (x^l - 1) / l
    v <- var(z); if (!is.finite(v) || v <= 0) return(Inf)
    -0.5 * length(z) * log(v) + (l - 1) * sum(lx) }
  list(lambda = optimize(f, c(-2, 2), maximum = TRUE)$maximum, eps = eps)
}

dir.create(sprintf("%s/suit", OUT), showWarnings = FALSE, recursive = TRUE)
# class lookup: specieskey present in env_occ_aves => "aves" else "mammalia"
# (species_winner.csv lost its class column in export merge)
aves_keys <- dbGetQuery(con, sprintf(
  "select distinct specieskey from '%s/env_occ_aves.parquet'", OUT))$specieskey
for (i in seq_len(nrow(win))) {
  sp <- win$specieskey[i]; tv <- win$best_temp[i]; pv <- win$best_prec[i]
  out_tif <- sprintf("%s/suit/suit_%d.tif", OUT, sp)
  if (file.exists(out_tif)) next
  cls <- if (sp %in% aves_keys) "aves" else "mammalia"
  occ <- dbGetQuery(con, sprintf(
    "select %s,%s from '%s/env_occ_%s.parquet' where specieskey=%d",
    tv, pv, OUT, cls, sp))
  ecos <- dbGetQuery(con, sprintf(
    "select distinct eco_id from '%s/m_ecoregions.parquet' where specieskey=%d",
    OUT, sp))$eco_id
  if (length(ecos) == 0) { cat("no M", sp, "\n"); next }
  m <- dbGetQuery(con, sprintf(
    "select %s,%s from '%s/env_normals_96_25.parquet' where eco_id in (%s)",
    tv, pv, OUT, paste(ecos, collapse = ",")))
  if (nrow(m) > 30000) { set.seed(1L); m <- m[sample(nrow(m), 30000), ] }
  l <- bc_lambda(m[[pv]])
  mo <- bc(m[[pv]], l$lambda, l$eps); oo <- bc(occ[[pv]], l$lambda, l$eps)
  z <- function(v, a) (a - mean(v)) / sd(v)
  em <- cbind(z(m[[tv]], m[[tv]]), z(mo, mo)); colnames(em) <- c(tv, pv)
  eo <- cbind(z(m[[tv]], occ[[tv]]), z(mo, oo)); colnames(eo) <- c(tv, pv)

  stack <- norm[[c(tv, pv)]]
  mp <- mean(m[[pv]]); spv <- sd(m[[pv]])
  mt <- mean(m[[tv]]); st <- sd(m[[tv]])
  stack[[pv]] <- (bc(stack[[pv]], l$lambda, l$eps) - mean(mo)) / sd(mo)
  stack[[tv]] <- (stack[[tv]] - mt) / st
  names(stack) <- c(tv, pv)

  TEMPS <- paste0("bio", sprintf("%02d", 1:11))
  PRECS <- paste0("bio", sprintf("%02d", 12:19))
  pi <- (match(tv, TEMPS) - 1L) * length(PRECS) + match(pv, PRECS)
  fit <- tryCatch(
    optimize_niche(eo, em, likelihood = "presence_only",
                   num_starts = n_starts,
                   seed = (sp %% 1e6) * 89 + pi),
      error = function(e) NULL)
  if (is.null(fit)) { cat("refit fail", sp, "\n"); next }
  s <- tryCatch(predict(fit, stack), error = function(e) NULL)
  if (is.null(s)) { cat("predict fail", sp, "\n"); next }
  # mask to the species' accessible area M (its occupied ecoregions);
  # niche model only valid inside M
  s[!(eco_r %in% ecos)] <- NA_real_
  if (all(is.na(values(s)))) { cat("empty M", sp, "\n"); next }
  writeRaster(s, out_tif, overwrite = TRUE, datatype = "FLT4S")
  cat("suit", sp, win$species[i], "\n", flush = TRUE)
}
