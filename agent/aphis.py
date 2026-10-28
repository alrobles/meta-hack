"""APHIS Geospatial Hub — HPAI detections via public ArcGIS REST.

No key needed. Service catalog:
    https://services7.arcgis.com/2C1NQ7u6M6SXoa8p/arcgis/rest/services

Layers used (verified 2026-10-26):
  - Wild-bird surveillance: ~174k county-level samples since 2021-07,
    Final_IAV / Final_H5 / Final_H7 / Final_Pathogenicity fields.
    Positives: Final_Pathogenicity = 'High Path AI' (~9.5k records).
  - Livestock (dairy cattle) events: state polygons, Conf_MMYY + Total.
  - Migratory flyways: AdminFlyways + Global_Flyways polylines — spatial
    priors for where the virus travels next.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

BASE = "https://services7.arcgis.com/2C1NQ7u6M6SXoa8p/arcgis/rest/services"
WILD_BIRD = f"{BASE}/VS_Avian_Influenza_Wild_Bird_Surveillance_Dashboard_data_view_feature_layer_/FeatureServer/0/query"
LIVESTOCK = f"{BASE}/VS_HPAI_Livestock_Events_Feature_Layer/FeatureServer/11/query"
# APHIS commercial/backyard flock confirmations, county-level (OSU mirror
# of the USDA APHIS public dataset)
WILD_BIRD_SPECIES = (
    "https://services1.arcgis.com/jWQlP64OuwDh6GGX/arcgis/rest/services/"
    "hpai_wildbirds_usa/FeatureServer/0/query")
POULTRY = ("https://services1.arcgis.com/jWQlP64OuwDh6GGX/arcgis/rest/"
           "services/hpai_commercial_backyard_usa/FeatureServer/0/query")
MAMMAL_SPECIES = (
    "https://services1.arcgis.com/jWQlP64OuwDh6GGX/arcgis/rest/services/"
    "hpai_mammals_usa/FeatureServer/0/query")
FLYWAY_ADMIN = f"{BASE}/VS_Wild_Bird_Migratory_Flyways_US_Admin_and_Global/FeatureServer/0/query"
FLYWAY_GLOBAL = f"{BASE}/VS_Wild_Bird_Migratory_Flyways_US_Admin_and_Global/FeatureServer/1/query"

PAGE = 1000  # conservative: services cap maxRecordCount at 1000


@dataclass
class Detection:
    """One wild-bird surveillance sample."""
    state: str
    county: str
    watershed: str
    date_collected: datetime | None
    iav: str
    h5: str
    h7: str
    pathogenicity: str
    lon: float | None
    lat: float | None

    @property
    def is_hpai(self) -> bool:
        return self.pathogenicity == "High Path AI"


@dataclass
class LivestockEvent:
    """State-level HPAI-in-livestock event aggregate."""
    state: str
    state_fips: str
    confirmed: datetime | None   # month-resolution (Conf_MMYY)
    incident_site: str
    total: float


def _ms(ts: int | None) -> datetime | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc)


def _query(url: str, where: str, limit: int | None = None,
           order_by: str | None = None, retries: int = 3) -> list[dict]:
    """Paginated query -> raw features [{attributes, geometry}, ...]."""
    feats: list[dict] = []
    offset = 0
    while True:
        n = min(PAGE, limit - offset) if limit else PAGE
        params = {
            "where": where,
            "outFields": "*",
            "resultOffset": offset,
            "resultRecordCount": n,
            "outSR": 4326,
            "f": "json",
        }
        if order_by:
            params["orderByFields"] = order_by
        for attempt in range(retries):
            r = requests.get(url, params=params, timeout=60)
            if r.status_code in (429, 502, 503) and attempt < retries - 1:
                time.sleep(2 ** attempt + 0.5)
                continue
            r.raise_for_status()
            break
        data = r.json()
        if "error" in data:
            raise RuntimeError(f"ArcGIS error: {data['error']}")
        batch = data.get("features", [])
        feats.extend(batch)
        offset += len(batch)
        if len(batch) < n or (limit and offset >= limit):
            return feats


def wild_bird_detections(
    state: str | None = None,
    county: str | None = None,
    since: str | None = None,     # 'YYYY-MM-DD'
    hpai_only: bool = True,
    limit: int | None = None,
) -> list[Detection]:
    """Wild-bird surveillance samples. hpai_only keeps confirmed
    high-path detections; set False for all sampled birds."""
    clauses = []
    if hpai_only:
        clauses.append("Final_Pathogenicity = 'High Path AI'")
    if state:
        clauses.append(f"State = '{state}'")
    if county:
        clauses.append(f"County = '{county}'")
    if since:
        clauses.append(f"Date_Collected >= TIMESTAMP '{since} 00:00:00'")
    where = " AND ".join(clauses) or "1=1"
    out = []
    for f in _query(WILD_BIRD, where, limit, "Date_Collected DESC"):
        a, g = f["attributes"], f.get("geometry") or {}
        out.append(Detection(
            state=a.get("State") or "",
            county=a.get("County") or "",
            watershed=a.get("Watershed") or "",
            date_collected=_ms(a.get("Date_Collected")),
            iav=a.get("Final_IAV") or "",
            h5=a.get("Final_H5") or "",
            h7=a.get("Final_H7") or "",
            pathogenicity=a.get("Final_Pathogenicity") or "",
            lon=g.get("x"), lat=g.get("y"),
        ))
    return out


def livestock_events(limit: int | None = None) -> list[LivestockEvent]:
    """HPAI-in-livestock confirmed events (state-month aggregates)."""
    out = []
    for f in _query(LIVESTOCK, "1=1", limit):
        a = f["attributes"]
        out.append(LivestockEvent(
            state=a.get("STATE_NAME") or a.get("STATE_ABBR") or "",
            state_fips=a.get("STATE_FIPS") or "",
            confirmed=_ms(a.get("Conf_MMYY")),
            incident_site=a.get("Incident_Site") or "",
            total=a.get("Total") or 0.0,
        ))
    return out


@dataclass
class FlockEvent:
    """HPAI confirmation in a commercial/backyard flock."""
    state: str
    county: str
    county_fips: int | None
    confirmed: datetime | None
    production: str
    birds_affected: float
    lon: float | None
    lat: float | None


def flock_events(limit: int | None = None) -> list[FlockEvent]:
    """Commercial/backyard flock confirmations (county-level)."""
    out = []
    for f in _query(POULTRY, "1=1", limit, "Confirmed DESC"):
        a, g = f["attributes"], f.get("geometry") or {}
        out.append(FlockEvent(
            state=a.get("State") or "",
            county=a.get("County_Name") or "",
            county_fips=a.get("County_FIPS"),
            confirmed=_ms(a.get("Confirmed")),
            production=a.get("Production") or "",
            birds_affected=float(a.get("Birds_Affected") or 0),
            lon=g.get("x"), lat=g.get("y"),
        ))
    return out


@dataclass
class SpeciesDetection:
    """HPAI detection with species name (NWHC mirror layer)."""
    state: str
    county: str
    county_fips: int | None
    date_detected: datetime | None
    bird_species: str        # common name as reported
    hpai_strain: str
    woah: str
    lon: float | None
    lat: float | None


def wild_bird_species(limit: int | None = None) -> list[SpeciesDetection]:
    """Species-level HPAI wild-bird confirmations (drives host labels)."""
    out = []
    for f in _query(WILD_BIRD_SPECIES, "1=1", limit, "ObjectId ASC"):
        a, g = f["attributes"], f.get("geometry") or {}
        out.append(SpeciesDetection(
            state=a.get("State") or "",
            county=a.get("County") or "",
            county_fips=a.get("County_FIPS"),
            date_detected=_ms(a.get("Date_Detected") or a.get("Collection_Date")),
            bird_species=a.get("Bird_Species") or "",
            hpai_strain=a.get("HPAI_Strain") or "",
            woah=a.get("WOAH_Classification") or "",
            lon=g.get("x"), lat=g.get("y"),
        ))
    return out


def mammal_species(limit: int | None = None) -> list[dict]:
    """HPAI mammal detections (OSU mirror layer) — Species/LAT_X/LON_Y."""
    out = []
    for f in _query(MAMMAL_SPECIES, "1=1", limit, "ObjectId ASC"):
        a, g = f["attributes"], f.get("geometry") or {}
        out.append({
            "State": a.get("State") or "",
            "County": a.get("County") or "",
            "County_FIPS": a.get("County_FIPS"),
            "Date_Detected": _ms(
                a.get("Date_Detected") or a.get("Date_Collected")),
            "HPAI_Strain": a.get("HPAI_Strain") or "",
            "Species": a.get("Species") or "",
            "lon": g.get("x") if g else a.get("LON_Y"),
            "lat": g.get("y") if g else a.get("LAT_X"),
        })
    return out


def flyways(global_: bool = False) -> list[dict]:
    """Migratory flyway polylines; attributes + esri paths geometry."""
    return _query(FLYWAY_GLOBAL if global_ else FLYWAY_ADMIN, "1=1")
