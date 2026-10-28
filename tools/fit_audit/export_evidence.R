# Fit-audit evidence exporter — runs on the HPC side (beegfs staging).
#
# Re-fits each listed species' winner + top-K AIC pairs with
# optimize_niche() (same transforms/seed as pipeline/xnicher_run.R),
# unpacks theta -> (mu, sigma, rho) via cvine_cholesky, and writes one
# folder per species under <outdir>:
#   pairs.csv   top-K refits: temp,prec,aic,loglik,mu1,mu2,s1,s2,rho,
#               flag,n_conv,rel_gap,ll_spread  (multi-start loglik range)
#   occ_z.csv   lat_30,lon_30,t_z,p_z,d2 (Mahalanobis^2),inside95
#   m_z.csv     t_z,p_z        (M-cloud subsample, <=4000 rows)
#   starts.csv  per-start theta for the winner pair -> mu1,mu2,s1,s2,rho,
#               loglik,convergence   (multi-start dispersion evidence)
#
# Reproducibility note: the campaign subsamples M >30000 rows WITHOUT a
# seed; this exporter sets seed(specieskey) so the cloud is stable, but
# transform moments can drift ~1e-3 vs the production fit — acceptable
# for audit, flagged in meta.csv if M was subsampled.
#
# Usage: Rscript export_evidence.R <species_csv> <outdir> [top_k]
#   species_csv: column `specieskey` (+ optional `species`); may also
#   pass "flag_review" to audit decisions_delta changed rows, resolved
#   outside this script.
#
# Env: WILDSEEK_BEEG = staging dir with pool_flagship.csv,
#   env_occ_<cls>.parquet, m_ecoregions.parquet, env_normals_96_25.parquet

suppressMessages({library(duckdb); library(DBI); library(xnicher)})

args <- commandArgs(trailingOnly = TRUE)
LIST  <- args[1]
OUT_D <- args[2]
TOP_K <- if (length(args) >= 3) as.integer(args[3]) else 5L

OUT <- Sys.getenv("WILDSEEK_BEEG", ".")
DOM <- Sys.getenv("WILDSEEK_DOMAIN", "conus")
SUF <- ifelse(DOM == "conus", "", paste0("_", DOM))
cvine <- getFromNamespace("cvine_cholesky", "xnicher")

sp_list <- read.csv(LIST, stringsAsFactors = FALSE)
stopifnot("specieskey" %in% names(sp_list))
dir.create(OUT_D, recursive = TRUE, showWarnings = FALSE)

con <- dbConnect(duckdb()); dbExecute(con, "PRAGMA threads=4")

TEMPS <- paste0("bio", sprintf("%02d", 1:11))
PRECS <- paste0("bio", sprintf("%02d", 12:19))
bios  <- paste0("bio", sprintf("%02d", 1:19))

# --- transforms, identical to xnicher_run.R -----------------------------
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

unpack <- function(theta, p = 2L, eta = 1) {
  mu  <- theta[seq_len(p)]
  s   <- exp(theta[p + seq_len(p)])
  nv  <- p * (p - 1) / 2
  L   <- cvine(theta[2 * p + seq_len(nv)], d = p, eta = eta)
  R   <- L %*% t(L)
  list(mu = mu, s = s, rho = R[1, 2])
}

catalog <- dbGetQuery(con, sprintf(
  "select specieskey,temp,prec,aic,loglik,flag,n_conv,rel_gap
   from '%s/fit_catalog.csv'", OUT))

for (i in seq_len(nrow(sp_list))) {
  sp <- sp_list$specieskey[i]
  cat("species", sp, "(", i, "/", nrow(sp_list), ")\n")
  cat_tab <- catalog[catalog$specieskey == sp, ]
  if (nrow(cat_tab) == 0) { cat("  no catalog rows\n"); next }
  cls <- dbGetQuery(con, sprintf(
    "select class from '%s/pool_flagship%s.csv' where specieskey=%d",
    OUT, SUF, sp))$class
  cls <- if (length(cls) && cls == "Mammalia") "mammalia" else "aves"

  occ <- dbGetQuery(con, sprintf(
    "select specieskey,species,lat_30,lon_30,%s
     from '%s/env_occ_%s.parquet' where specieskey=%d",
    paste(bios, collapse = ","), OUT, cls, sp))
  ecos <- dbGetQuery(con, sprintf(
    "select distinct eco_id from '%s/m_ecoregions.parquet'
     where specieskey=%d", OUT, sp))$eco_id
  if (length(ecos) == 0) { cat(sp, "no m ecoregions; skip\n"); next }
  m <- dbGetQuery(con, sprintf(
    "select %s from '%s/env_normals_96_25.parquet' where eco_id in (%s)",
    paste(bios, collapse = ","), OUT, paste(ecos, collapse = ",")))
  m_sub <- FALSE
  set.seed(sp)
  if (nrow(m) > 30000) { m <- m[sample(nrow(m), 30000), ]; m_sub <- TRUE }

  # top-K pairs by catalog AIC -> refit with optimize_niche
  top <- cat_tab[order(cat_tab$aic), ][seq_len(min(TOP_K, nrow(cat_tab))), ]
  pairs <- list(); starts <- list(); occ_z <- NULL; m_z <- NULL
  for (r in seq_len(nrow(top))) {
    tv <- top$temp[r]; pv <- top$prec[r]
    l  <- bc_lambda(m[[pv]])
    mo <- bc(m[[pv]], l$lambda, l$eps); oo <- bc(occ[[pv]], l$lambda, l$eps)
    z  <- function(v, a) (a - mean(v)) / sd(v)
    em <- cbind(z(m[[tv]], m[[tv]]), z(mo, mo))
    eo <- cbind(z(m[[tv]], occ[[tv]]), z(mo, oo))
    colnames(em) <- colnames(eo) <- c(tv, pv)
    if (any(!is.finite(eo))) next
    # campaign seed: (sp%%1e6)*89 + row index in expand.grid(TEMPS,PRECS)
    pi_idx <- match(tv, TEMPS) + (match(pv, PRECS) - 1) * length(TEMPS)
    fit <- tryCatch(
      optimize_niche(eo, em, likelihood = "presence_only",
                     num_starts = 60L, seed = (sp %% 1e6) * 89 + pi_idx),
      error = function(e) NULL)
    if (is.null(fit)) next
    th <- unpack(fit$best$theta)
    lls <- fit$solutions$loglik[fit$solutions$convergence %in% c(1L, 2L)]
    pairs[[length(pairs) + 1]] <- data.frame(
      temp = tv, prec = pv, aic = top$aic[r], loglik = as.numeric(logLik(fit)),
      mu1 = th$mu[1], mu2 = th$mu[2], s1 = th$s[1], s2 = th$s[2],
      rho = th$rho, flag = top$flag[r], n_conv = top$n_conv[r],
      rel_gap = top$rel_gap[r],
      ll_spread = if (length(lls) > 1) diff(range(lls)) else NA_real_)
    # multi-start evidence (winner pair only)
    if (r == 1 && !is.null(fit$solutions$full_par)) {
      sol <- fit$solutions
      for (s_i in seq_along(sol$full_par)) {
        t2 <- tryCatch(unpack(sol$full_par[[s_i]]), error = function(e) NULL)
        if (is.null(t2)) next
        starts[[length(starts) + 1]] <- data.frame(
          mu1 = t2$mu[1], mu2 = t2$mu[2], s1 = t2$s[1], s2 = t2$s[2],
          rho = t2$rho, loglik = sol$loglik[s_i],
          convergence = sol$convergence[s_i])
      }
      Sig <- matrix(c(th$s[1]^2, th$rho*prod(th$s), th$rho*prod(th$s), th$s[2]^2), 2)
      Si  <- tryCatch(solve(Sig), error = function(e) NULL)
      if (is.null(Si)) next
      d2  <- (eo[,1]-th$mu[1])^2*Si[1,1] + 2*(eo[,1]-th$mu[1])*(eo[,2]-th$mu[2])*Si[1,2] +
             (eo[,2]-th$mu[2])^2*Si[2,2]
      occ_z <- data.frame(lat_30 = occ$lat_30, lon_30 = occ$lon_30,
                          t_z = eo[,1], p_z = eo[,2], d2 = d2,
                          inside95 = d2 <= qchisq(0.95, 2))
      keep_m <- if (nrow(em) > 4000) sample(nrow(em), 4000) else seq_len(nrow(em))
      m_z <- data.frame(t_z = em[keep_m, 1], p_z = em[keep_m, 2])
    }
  }
  d <- file.path(OUT_D, paste0("sp_", sp)); dir.create(d, showWarnings = FALSE)
  spname <- if (nrow(occ)) occ$species[1] else as.character(sp)
  meta <- data.frame(
    specieskey = sp, species = spname, class = cls,
    n_occ = nrow(occ), n_m = nrow(m), m_subsampled = m_sub,
    n_pairs_refit = length(pairs))
  write.csv(meta, file.path(d, "meta.csv"), row.names = FALSE)
  if (length(pairs))  write.csv(do.call(rbind, pairs),  file.path(d, "pairs.csv"),  row.names = FALSE)
  if (length(starts)) write.csv(do.call(rbind, starts), file.path(d, "starts.csv"), row.names = FALSE)
  if (!is.null(occ_z)) write.csv(occ_z, file.path(d, "occ_z.csv"), row.names = FALSE)
  if (!is.null(m_z))   write.csv(m_z,   file.path(d, "m_z.csv"),   row.names = FALSE)
}
dbDisconnect(con)
cat("done ->", OUT_D, "\n")
