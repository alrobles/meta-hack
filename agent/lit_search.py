"""OpenAlex literature search for host-parasite evidence.

No key needed. Set OPENALEX_MAILTO to enter the polite pool.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

import requests

BASE = "https://api.openalex.org/works"


@dataclass
class Work:
    work_id: str
    doi: str | None
    title: str
    abstract: str
    year: int | None
    url: str
    cited_by: int = 0

    @property
    def text(self) -> str:
        return f"{self.title}\n\n{self.abstract}".strip()


def _reconstruct_abstract(inv_index: dict | None) -> str:
    """OpenAlex ships abstracts as {word: [positions]} — rebuild the string."""
    if not inv_index:
        return ""
    positions: dict[int, str] = {}
    for word, idxs in inv_index.items():
        for i in idxs:
            positions[i] = word
    return " ".join(positions[i] for i in sorted(positions))


def search(
    query: str,
    limit: int = 50,
    min_year: int | None = None,
    retries: int = 3,
) -> list[Work]:
    """Relevance-ranked works matching `query`. Returns up to `limit`."""
    mailto = os.environ.get("OPENALEX_MAILTO")
    params = {
        "search": query,
        "per-page": min(limit, 200),
        "select": "id,doi,title,abstract_inverted_index,publication_year,primary_location,cited_by_count",
    }
    if min_year:
        params["filter"] = f"publication_year:>{min_year - 1}"
    if mailto:
        params["mailto"] = mailto

    works: list[Work] = []
    cursor = "*"
    while len(works) < limit:
        params["cursor"] = cursor
        for attempt in range(retries):
            r = requests.get(BASE, params=params, timeout=30)
            if r.status_code == 429 and attempt < retries - 1:
                time.sleep(2 ** attempt + 0.5)
                continue
            r.raise_for_status()
            break
        data = r.json()
        for w in data.get("results", []):
            loc = (w.get("primary_location") or {})
            landing = (loc.get("landing_page_url") or "") if loc else ""
            works.append(Work(
                work_id=w["id"],
                doi=w.get("doi"),
                title=w.get("title") or "",
                abstract=_reconstruct_abstract(w.get("abstract_inverted_index")),
                year=w.get("publication_year"),
                url=landing or w["id"],
                cited_by=w.get("cited_by_count") or 0,
            ))
        cursor = (data.get("meta") or {}).get("next_cursor")
        if not cursor or not data.get("results"):
            break
    return works[:limit]
