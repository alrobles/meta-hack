# Bird patristic distances from BirdTree Stage2 posterior (Ericson).
# PNAS script 06 pattern: sample trees -> keep.tip ours -> consensus
# (least.squares) -> cophenetic. GBIF<->BirdTree names reconciled via
# BLIOCPhyloMasterTax + GBIF synonyms (data/bird_syn_map.csv).
# Appends source="birdtree_ericson" rows to data/phy_dist.csv.

library(ape)
library(phytools)

BIRD_DIR <- Sys.getenv("WILDSEEK_BIRDTREE_DIR", "data/birdtree")
TRE <- file.path(BIRD_DIR, "e2/Stage2_MayrAll_Ericson_set10_decisive.tre")
W <- read.csv("data/species_winner.csv", stringsAsFactors = FALSE)
W$u <- gsub(" ", "_", W$species)

bt <- read.csv(file.path(BIRD_DIR, "BLIOCPhyloMasterTax.csv"))
sci2tip <- setNames(bt$TipLabel, bt$Scientific)

# our-name -> tip label: direct Scientific match, else GBIF synonym map
syn <- if (file.exists("data/bird_syn_map.csv"))
       read.csv("data/bird_syn_map.csv") else data.frame()
syn_map <- if (nrow(syn)) setNames(syn$tip, syn$X) else
           setNames(character(), character())

our_tip <- sci2tip[W$species]
missing <- is.na(our_tip)
our_tip[missing] <- syn_map[W$species[missing]]
our_tip <- our_tip[!is.na(our_tip)]
cat("birds mappable to tips:", length(our_tip), "\n")

cat("reading posterior bundle...\n")
trees <- read.tree(TRE)
cat("trees:", length(trees), " tips/tree:", Ntip(trees[[1]]), "\n")

keep <- intersect(our_tip, trees[[1]]$tip.label)
cat("on tree:", length(keep), "\n")

set.seed(7)
sub <- trees[sample(seq_along(trees), min(100, length(trees)))]
sub <- lapply(sub, keep.tip, tip = keep)
class(sub) <- "multiPhylo"

cat("consensus (least.squares over", length(sub), "trees)...\n")
cons <- consensus.edges(sub, method = "least.squares")

d <- cophenetic.phylo(cons)
idx <- which(upper.tri(d), arr.ind = TRUE)
out <- data.frame(
  item1 = rownames(d)[idx[, 1]],
  item2 = colnames(d)[idx[, 2]],
  phylo_distance = d[idx],
  source = "birdtree_ericson")
# tip labels -> our GBIF names for uniform downstream joins.
# merge (not direct index): several of our names may share one tip
# (split species lumped on the tree) -> emit a row per our-name combo.
tip_our <- data.frame(tip = unname(our_tip), our = names(our_tip))
m <- merge(out, tip_our, by.x = "item1", by.y = "tip")
m <- merge(m, tip_our, by.x = "item2", by.y = "tip")
out <- data.frame(item1 = m$our.x, item2 = m$our.y,
                  phylo_distance = m$phylo_distance, source = m$source)
out <- out[!duplicated(out[c("item1", "item2")]), ]

old <- if (file.exists("data/phy_dist.csv"))
       read.csv("data/phy_dist.csv") else data.frame()
old <- old[old$source != "birdtree_ericson", ]
write.csv(rbind(old, out), "data/phy_dist.csv", row.names = FALSE)
cat("bird pairs:", nrow(out), "median d:", median(out$phylo_distance), "\n")
