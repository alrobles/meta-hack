# Patristic distances among our species.
#   Mammalia: cophenetic on a dated tree (MDD tips) pruned to matched
#             names -> REAL phylogenetic distance (Myr). Tree path via
#             env WILDSEEK_MAMMAL_TREE, default data/v6_point.nwk.
#   Aves:     no tree yet -> phy_source="proxy" rows are left to the
#             GBIF taxonomic fallback in dist_frame.py.
# Output: phy_dist.csv (item1,item2,phylo_distance,source)

library(ape)

TREE <- Sys.getenv("WILDSEEK_MAMMAL_TREE", "data/v6_point.nwk")
if (!file.exists(TREE)) {
  stop("mammal tree not found at '", TREE, "': it is pre-existing lab ",
       "infra, not shipped with this repo. Set WILDSEEK_MAMMAL_TREE ",
       "(see NEW.md).")
}
W <- read.csv("data/species_winner.csv", stringsAsFactors = FALSE)
W$u <- gsub(" ", "_", W$species)

tr <- read.tree(TREE)
# our-name -> tip label: direct match, else mammal synonym map
syn <- if (file.exists("data/mammal_syn_map.csv"))
       read.csv("data/mammal_syn_map.csv") else data.frame()
syn_map <- if (nrow(syn)) setNames(syn$tip, syn$species) else
           setNames(character(), character())
our_tip <- W$u
missing <- !(our_tip %in% tr$tip.label)
our_tip[missing] <- syn_map[W$species[missing]]
ok <- !is.na(our_tip) & our_tip %in% tr$tip.label
tip_our <- data.frame(tip = our_tip[ok], our = W$species[ok])

keep <- intersect(our_tip[ok], tr$tip.label)
cat("matched on v6 tree:", length(keep), "\n")

tr <- keep.tip(tr, keep)
d <- cophenetic.phylo(tr)
idx <- which(upper.tri(d), arr.ind = TRUE)
out <- data.frame(
  item1 = rownames(d)[idx[, 1]],
  item2 = colnames(d)[idx[, 2]],
  phylo_distance = d[idx],
  source = "v6_dated")
# tip labels -> our GBIF names (merge expands split-species sharing a tip)
m <- merge(out, tip_our, by.x = "item1", by.y = "tip")
m <- merge(m, tip_our, by.x = "item2", by.y = "tip")
out <- data.frame(item1 = m$our.x, item2 = m$our.y,
                  phylo_distance = m$phylo_distance, source = m$source)
out <- out[!duplicated(out[c("item1", "item2")]), ]
write.csv(out, "data/phy_dist.csv", row.names = FALSE)
cat("pairs:", nrow(out), "median d:", median(out$phylo_distance), "\n")
