# Refit queued species under a switched likelihood family.
#
# Reads refit_queue.csv (specieskey, species, switch_to), refits the
# WINNER pair (from species_winner.csv) under the requested likelihood,
# writes refit_delta.csv: specieskey, switch_to, aic_new, flag_new.
# The old AIC is joined downstream in decide_tail delta merge.
#
# Usage: Rscript refit_switched.R  (single job — the queue is small)

suppressMessages({
  library(duckdb); library(DBI); library(xnicher)
})
OUT <- Sys.getenv("WILDSEEK_BEEG", ".")
DOM <- Sys.getenv("WILDSEEK_DOMAIN", "conus")
SUF <- ifelse(DOM == "conus", "", paste0("_", DOM))
n_starts <- 60L

con <- dbConnect(duckdb()); dbExecute(con, "PRAGMA threads=4")
q <- read.csv(sprintf("%s/refit_queue.csv", OUT), stringsAsFactors = FALSE)
win <- dbGetQuery(con, sprintf(
  "select w.*, p.class from '%s/species_winner.csv' w
   join '%s/pool_flagship%s.csv' p using (specieskey)", OUT, OUT, SUF))
q <- merge(q, win, by = "specieskey")
cat("refit queue:", nrow(q), "\n")

bc <- function(x, lam, eps) if (abs(lam) < 1e-8) log(x + eps) else ((x + eps)^lam - 1) / lam
bc_lambda <- function(x) {
  eps <- min(x[x > 0], na.rm = TRUE) / 2; if (!is.finite(eps) || eps <= 0) eps <- 1e-3
  x <- x + eps; lx <- log(x)
  f <- function(l) { z <- if (abs(l) < 1e-8) lx else (x^l - 1) / l
    v <- var(z); if (!is.finite(v) || v <= 0) return(Inf)
    -0.5 * length(z) * log(v) + (l - 1) * sum(lx) }
  list(lambda = optimize(f, c(-2, 2), maximum = TRUE)$maximum, eps = eps)
}

rows <- list()
for (i in seq_len(nrow(q))) {
  sp <- q$specieskey[i]; tv <- q$best_temp[i]; pv <- q$best_prec[i]
  like <- q$switch_to[i]; cls <- tolower(q$class[i])
  n_st <- if (like == "more_starts") 150L else n_starts
  if (!like %in% c("ip_weighted", "skew_normal", "skew_t", "ncst"))
    like <- "presence_only"
  occ <- dbGetQuery(con, sprintf(
    "select %s,%s from '%s/env_occ_%s.parquet' where specieskey=%d",
    tv, pv, OUT, cls, sp))
  ecos <- dbGetQuery(con, sprintf(
    "select distinct eco_id from '%s/m_ecoregions.parquet' where specieskey=%d",
    OUT, sp))$eco_id
  if (length(ecos) == 0) next
  m <- dbGetQuery(con, sprintf(
    "select %s,%s from '%s/env_normals_96_25.parquet' where eco_id in (%s)",
    tv, pv, OUT, paste(ecos, collapse = ",")))
  if (nrow(m) > 30000) { set.seed(1L); m <- m[sample(nrow(m), 30000), ] }
  l <- bc_lambda(m[[pv]])
  mo <- bc(m[[pv]], l$lambda, l$eps); oo <- bc(occ[[pv]], l$lambda, l$eps)
  z <- function(v, a) (a - mean(v)) / sd(v)
  em <- cbind(z(m[[tv]], m[[tv]]), z(mo, mo)); colnames(em) <- c(tv, pv)
  eo <- cbind(z(m[[tv]], occ[[tv]]), z(mo, oo)); colnames(eo) <- c(tv, pv)

  TEMPS <- paste0("bio", sprintf("%02d", 1:11))
  PRECS <- paste0("bio", sprintf("%02d", 12:19))
  pi <- (match(tv, TEMPS) - 1L) * length(PRECS) + match(pv, PRECS)
  fit <- tryCatch(
    optimize_niche(eo, em, likelihood = like, num_starts = n_st,
                   seed = (sp %% 1e6) * 89 + pi),
    error = function(e) NULL)
  if (is.null(fit)) { cat("refit fail", sp, like, "\n"); next }
  a <- tryCatch(assess(fit)$flag, error = function(e) "assess_fail")
  rows[[length(rows) + 1]] <- data.frame(
    specieskey = sp, species = q$species[i], switch_to = like,
    aic_new = AIC(fit), bic_new = BIC(fit), flag_new = a,
    stringsAsFactors = FALSE)
  cat("refit", sp, q$species[i], like, "AIC", round(AIC(fit), 1), a, "\n",
      flush = TRUE)
}
if (length(rows)) {
  write.csv(do.call(rbind, rows),
            sprintf("%s/refit_delta.csv", OUT), row.names = FALSE)
}
