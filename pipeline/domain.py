"""Named analysis domains — the geographic window a build targets.

Everything downstream (pools, normals window, eco raster, predict
extent) keys off this; the niche machinery itself is domain-agnostic
(each species brings its own occurrences + M ecoregions wherever they
live — occurrence pools, env_cube, m_ecoregions are already global).

Selected via env WILDSEEK_DOMAIN (default "conus" — backward compatible
with every existing product).

  conus : lower-48 US + margins — the production risk surface
  na_ca : Canada -> Panama — the expanded susceptibility domain

bbox = (lon_min, lat_min, lon_max, lat_max). min_cells is the pool
threshold (unique 1/30-deg occupied cells inside bbox).
"""
import os

DOMAINS = {
    "conus": dict(bbox=(-125.0, 24.0, -66.0, 49.5), min_cells=100),
    "na_ca": dict(bbox=(-170.0, 7.0, -52.0, 84.0), min_cells=100),
}


def get(name=None):
    d = DOMAINS[name or os.environ.get("WILDSEEK_DOMAIN", "conus")]
    return dict(d)


def name():
    return os.environ.get("WILDSEEK_DOMAIN", "conus")


def suffix():
    """Product-name suffix: '' for conus (legacy names), '_na_ca' etc."""
    n = name()
    return "" if n == "conus" else f"_{n}"
