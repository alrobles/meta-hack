"""Taxonomic resolution of extracted host names via GBIF.

Free APIs, no key. Two stages:
  1. /v1/species/match  — scientific names (exact/fuzzy)
  2. /v1/species/search — vernacular fallback, restricted to Mammalia
     (key 359); first ACCEPTED species hit wins, matched_on recorded
     for audit.

Unresolved names (e.g. "wild and domestic animals") keep gbif=None so
the model layer can drop them.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import requests

from .host_list import HostList

MATCH_URL = "https://api.gbif.org/v1/species/match"
SEARCH_URL = "https://api.gbif.org/v1/species/search"
MAMMALIA_KEY = 359


def _get(url: str, params: dict, retries: int = 2) -> dict | None:
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=15)
            if r.status_code == 429 and attempt < retries - 1:
                time.sleep(1)
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            if attempt == retries - 1:
                return None
            time.sleep(1)
    return None


def match_scientific(name: str) -> dict | None:
    m = _get(MATCH_URL, {"name": name, "kingdom": "Animalia"})
    if not m or m.get("matchType") in (None, "NONE"):
        return None
    if m.get("rank") not in ("SPECIES", "SUBSPECIES", "VARIETY"):
        return None
    return {
        "usageKey": m.get("usageKey"),
        "scientificName": m.get("scientificName") or m.get("canonicalName"),
        "canonicalName": m.get("canonicalName"),
        "rank": m.get("rank"),
        "status": m.get("status"),
        "confidence": m.get("confidence"),
        "resolved_via": "match",
    }


def _norm(s: str) -> str:
    return re.sub(r"[^a-z ]", "", s.lower()).strip()


def _vernacular_overlap(query: str, result: dict) -> str | None:
    """Does any vernacular name relate to the query?
    Returns 'exact', 'fuzzy' (word-boundary containment), or None."""
    q = _norm(query)
    if not q:
        return None
    names = [v.get("vernacularName", "") for v in result.get("vernacularNames", [])]
    norms = [_norm(n) for n in names]
    if q in norms:
        return "exact"
    for n in norms:
        if n and (q in n or n in q):
            return "fuzzy"
    return None


def search_vernacular(name: str, limit: int = 10) -> dict | None:
    d = _get(SEARCH_URL, {
        "q": name, "rank": "SPECIES",
        "highertaxon_key": MAMMALIA_KEY, "limit": limit,
    })
    fuzzy_hit = None
    for r in (d or {}).get("results", []):
        if r.get("rank") != "SPECIES" or not r.get("canonicalName"):
            continue
        overlap = _vernacular_overlap(name, r)
        if overlap == "exact":
            via = "search"
        elif overlap == "fuzzy":
            via = "search_fuzzy"  # kept but flagged — review before use
            if fuzzy_hit is not None:
                continue          # keep scanning for an exact hit
            fuzzy_hit = {
                "usageKey": r.get("key"),
                "scientificName": r.get("scientificName"),
                "canonicalName": r.get("canonicalName"),
                "rank": r.get("rank"),
                "status": r.get("taxonomicStatus", "ACCEPTED"),
                "confidence": None,
                "resolved_via": via,
            }
            continue
        else:
            continue
        return {
            "usageKey": r.get("key"),
            "scientificName": r.get("scientificName"),
            "canonicalName": r.get("canonicalName"),
            "rank": r.get("rank"),
            "status": r.get("taxonomicStatus", "ACCEPTED"),
            "confidence": None,
            "resolved_via": via,
        }
    return fuzzy_hit


def resolve_name(name: str) -> dict | None:
    return match_scientific(name) or search_vernacular(name)


def resolve_host_list(hosts: HostList, delay: float = 0.15) -> dict:
    """Annotate every entry in-place. Returns a small summary dict."""
    tried: dict[str, dict | None] = {}
    resolved = unresolved = 0
    for e in hosts.entries.values():
        for cand in [e["host_species"], *e.get("common_names", [])]:
            if cand not in tried:
                tried[cand] = resolve_name(cand)
                time.sleep(delay)
            m = tried[cand]
            if m:
                e["gbif"] = {**m, "matched_on": cand}
                resolved += 1
                break
        else:
            e["gbif"] = None
            unresolved += 1
    consolidated = hosts.consolidate_resolved()
    return {"resolved": resolved, "unresolved": unresolved,
            "consolidated": consolidated, "lookups": len(tried)}


def resolve_file(path: str | Path) -> dict:
    hosts = HostList.load(path)
    summary = resolve_host_list(hosts)
    hosts.save(path)
    return summary
