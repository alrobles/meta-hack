# Rasterize RESOLVE Ecoregions2017 (847 polys) onto the 1/30-deg global grid.
#
# Emits eco_raster_130.csv.gz: one row per land cell as integer bins
#   lat_bin = floor((lat+90)*30), lon_bin = floor((lon+180)*30)   [0..2699, 0..5399]
# matching the convention lat_30 = floor(lat*30)/30 used by the GBIF pools.
# Run locally (terra + full-res shapefile), upload result to beegfs;
# the cluster join is then plain DuckDB, no geospatial deps.
#
# Source: Ecoregions2017.shp (RESOLVE / Dinerstein et al. 2017, CC-BY 4.0)
#         same layer packaged (simplified) in oneearthr::oe_ecoregions.

library(terra)

args <- commandArgs(trailingOnly = TRUE)
shp <- if (length(args) >= 1) args[1] else "/tmp/eco2017/Ecoregions2017.shp"
out <- if (length(args) >= 2) args[2] else "/tmp/eco_raster_130.csv.gz"

v <- vect(shp)
stopifnot(is.lonlat(v))
cat("features:", nrow(v), " fields:", paste(names(v), collapse = ","), "\n")

RES <- 1 / 30
r <- rast(xmin = -180, xmax = 180, ymin = -90, ymax = 90,
          resolution = RES, crs = "EPSG:4326")
x <- rasterize(v, r, field = "ECO_ID")

cc <- crds(x, df = TRUE, na.rm = FALSE)
vv <- values(x, dataframe = TRUE)
keep <- !is.na(vv[[1]])
df <- data.frame(
  lat_bin = as.integer(floor((cc$y[keep] + 90) * 30)),
  lon_bin = as.integer(floor((cc$x[keep] + 180) * 30)),
  eco_id  = vv[[1]][keep]
)

meta <- as.data.frame(v)[, c("ECO_ID", "ECO_NAME", "REALM", "BIOME_NAME", "NNH_NAME")]
names(meta) <- c("eco_id", "eco_name", "realm", "biome_name", "nnh_name")
df <- merge(df, meta, by = "eco_id")

cat("cells:", nrow(df), " ecoregions:", length(unique(df$eco_id)), "\n")
write.csv(df, gzfile(out), row.names = FALSE, quote = TRUE)
cat("wrote", out, "\n")
