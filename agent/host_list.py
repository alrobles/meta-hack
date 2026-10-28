"""Living host list — deduplicated, provenance-tracked, append-only.

Storage: one JSON object per line (JSONL). Each line is a host entry:

    {"host_species": "Odocoileus virginianus",
     "parasite": "Cochliomyia hominivorax",
     "common_names": ["white-tailed deer"],
     "countries": ["USA"], "years": ["2016"],
     "confidence": "high",           # max across evidence
     "n_evidence": 3,
     "first_seen": 1761436800.0,
     "evidence": [ {...}, ... ]}

Merge key: (normalized parasite, normalized host_species). Normalization
lowercases and collapses whitespace; subspecies are kept (susceptibility
can be subspecies-specific) but parenthetical common names are stripped.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict
from pathlib import Path

from .extract import Evidence

_CONF_RANK = {"low": 0, "medium": 1, "high": 2}

_PAREN = re.compile(r"\s*\([^)]*\)\s*")
_WS = re.compile(r"\s+")


def norm_name(name: str) -> str:
    n = _PAREN.sub(" ", name.strip().lower())
    return _WS.sub(" ", n).strip()


def _key(parasite: str, host: str) -> str:
    return f"{norm_name(parasite)}|{norm_name(host)}"


class HostList:
    def __init__(self, entries: dict[str, dict] | None = None):
        self.entries = entries or {}

    @classmethod
    def load(cls, path: str | Path) -> HostList:
        p = Path(path)
        entries: dict[str, dict] = {}
        if p.exists():
            for line in p.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                e = json.loads(line)
                entries[_key(e["parasite"], e["host_species"])] = e
        return cls(entries)

    def merge_evidence(self, ev: Evidence) -> bool:
        """Add evidence; returns True if this created a NEW host entry."""
        d = asdict(ev)
        k = _key(ev.parasite, ev.host_species)
        if k not in self.entries:
            self.entries[k] = {
                "host_species": ev.host_species.strip(),
                "parasite": ev.parasite.strip(),
                "common_names": [],
                "countries": [],
                "years": [],
                "confidence": ev.confidence,
                "n_evidence": 0,
                "first_seen": time.time(),
                "evidence": [],
            }
            created = True
        else:
            created = False
        e = self.entries[k]
        # skip exact-duplicate evidence (same work + same quote)
        for old in e["evidence"]:
            if old["work_id"] == ev.work_id and \
               norm_name(old["verbatim_quote"]) == norm_name(ev.verbatim_quote):
                return created
        e["evidence"].append(d)
        e["n_evidence"] = len(e["evidence"])
        if ev.host_common_name and ev.host_common_name not in e["common_names"]:
            e["common_names"].append(ev.host_common_name)
        if ev.country and ev.country not in e["countries"]:
            e["countries"].append(ev.country)
        if ev.year and ev.year not in e["years"]:
            e["years"].append(ev.year)
        if _CONF_RANK.get(ev.confidence, 0) > _CONF_RANK.get(e["confidence"], 0):
            e["confidence"] = ev.confidence
        return created

    def merge_many(self, evs: list[Evidence]) -> tuple[int, int]:
        """Returns (new_hosts, total_evidence_added_delta)."""
        before_n = sum(e["n_evidence"] for e in self.entries.values())
        new = sum(self.merge_evidence(ev) for ev in evs)
        after_n = sum(e["n_evidence"] for e in self.entries.values())
        return new, after_n - before_n

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w") as f:
            for k in sorted(self.entries):
                f.write(json.dumps(self.entries[k], ensure_ascii=False) + "\n")

    def consolidate_resolved(self) -> int:
        """Merge entries that resolved to the same GBIF usageKey
        (e.g. 'human'/'humans'/'man' -> Homo sapiens). Runs after
        resolve_host_list. Returns number of entries removed."""
        groups: dict[int, list[str]] = {}
        for k, e in self.entries.items():
            g = e.get("gbif")
            if g and g.get("usageKey"):
                groups.setdefault(g["usageKey"], []).append(k)
        removed = 0
        for _key_, ks in groups.items():
            if len(ks) < 2:
                continue
            canon = min(ks, key=lambda k: -self.entries[k]["n_evidence"])
            base = self.entries[canon]
            for k in ks:
                if k == canon:
                    continue
                e = self.entries.pop(k)
                removed += 1
                base["common_names"] = sorted(set(
                    base["common_names"] + e["common_names"] + [e["host_species"]]
                ) - {base["host_species"]})
                base["countries"] = sorted(set(base["countries"]) | set(e["countries"]))
                base["years"] = sorted(set(base["years"]) | set(e["years"]))
                seen = {(x["work_id"], norm_name(x["verbatim_quote"]))
                        for x in base["evidence"]}
                for x in e["evidence"]:
                    if (x["work_id"], norm_name(x["verbatim_quote"])) not in seen:
                        base["evidence"].append(x)
                base["n_evidence"] = len(base["evidence"])
                if _CONF_RANK.get(e["confidence"], 0) > \
                   _CONF_RANK.get(base["confidence"], 0):
                    base["confidence"] = e["confidence"]
                base["first_seen"] = min(base["first_seen"], e["first_seen"])
            g = base["gbif"]
            canon_name = g.get("canonicalName")
            if canon_name and canon_name != base["host_species"]:
                if base["host_species"] not in base["common_names"]:
                    base["common_names"].append(base["host_species"])
                base["host_species"] = canon_name
        if removed:
            self.entries = {
                _key(e["parasite"], e["host_species"]): e
                for e in self.entries.values()
            }
        return removed

    def species(self, min_confidence: str = "low") -> list[str]:
        thr = _CONF_RANK[min_confidence]
        return sorted(
            e["host_species"] for e in self.entries.values()
            if _CONF_RANK.get(e["confidence"], 0) >= thr
        )
