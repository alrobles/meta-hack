"""Structured host-pathogen evidence extraction via Meta Model API.

Bulk jobs run on muse-spark contributor models (public literature only —
Meta may train on contributor-mode content). Every record carries the
verbatim quote as its provenance anchor back into the source text.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from .lit_search import Work
from .meta_client import MODEL_EXTRACTION, MetaClient

# NOTE: under strict mode every property must be listed in `required`
# (the API 400s otherwise — verified 2026-10-26).
EXTRACTION_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "host_parasite_extraction",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "records": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "host_species": {"type": "string"},
                            "host_common_name": {"type": "string"},
                            "parasite": {"type": "string"},
                            "evidence": {"type": "string"},
                            "verbatim_quote": {"type": "string"},
                            "country": {"type": "string"},
                            "year": {"type": "string"},
                            "confidence": {
                                "type": "string",
                                "enum": ["high", "medium", "low"],
                            },
                        },
                        "required": [
                            "host_species", "host_common_name", "parasite",
                            "evidence", "verbatim_quote", "country",
                            "year", "confidence",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["records"],
            "additionalProperties": False,
        },
    },
}

INSTRUCTIONS = """\
You are extracting host-pathogen association records from scientific
literature for a wildlife-disease surveillance system.

From the paper title and abstract below, extract every claim that a
species is a host of (or was found infected/infested by) the pathogen
{parasite} (or its common names: {parasite_common}).

Rules:
- host_species: latin binomial if the text gives it, else the common name.
- evidence: short phrase describing the kind of record (e.g. "naturally
  infected", "found dead, PCR-confirmed", "experimental inoculation",
  "seropositive").
- verbatim_quote: copy the exact supporting sentence(s) from the text.
  Never paraphrase this field.
- country/year: only if stated; "" otherwise.
- confidence: high = observed in nature; medium = experimental or
  indirect; low = speculative / listed without direct record.
- If the text contains NO host records for {parasite}, return
  {{"records": []}}. Do not extract records for other pathogens.

TITLE: {title}

ABSTRACT: {abstract}
"""


@dataclass
class Evidence:
    host_species: str
    host_common_name: str
    parasite: str
    evidence: str
    verbatim_quote: str
    country: str
    year: str
    confidence: str
    work_id: str
    doi: str | None
    model: str
    extracted_at: float = field(default_factory=time.time)


def extract_work(
    client: MetaClient,
    work: Work,
    parasite: str,
    parasite_common: str,
    model: str = MODEL_EXTRACTION,
) -> list[Evidence]:
    """One work -> zero or more provenance-tracked evidence records."""
    if not work.abstract:
        return []
    prompt = INSTRUCTIONS.format(
        parasite=parasite,
        parasite_common=parasite_common,
        title=work.title,
        abstract=work.abstract[:6000],  # keep well under context + cost caps
    )
    try:
        r = client.chat(
            [{"role": "user", "content": prompt}],
            model=model,
            reasoning_effort="low",
            max_tokens=4096,
            response_format=EXTRACTION_SCHEMA,
        )
    except Exception as e:  # noqa: BLE001 — log-and-continue per-work
        body = getattr(getattr(e, "response", None), "text", "")
        print(f"  ! {work.work_id}: {type(e).__name__} {e} {body[:300]}")
        if getattr(e, "response", None) is not None and \
                e.response.status_code in (400, 413, 422):
            # Retry once with the abstract truncated hard.
            retry = INSTRUCTIONS.format(
                parasite=parasite, parasite_common=parasite_common,
                title=work.title, abstract=work.abstract[:3000])
            try:
                r = client.chat(
                    [{"role": "user", "content": retry}],
                    model=model, reasoning_effort="low", max_tokens=4096,
                    response_format=EXTRACTION_SCHEMA,
                )
            except Exception as e2:  # noqa: BLE001
                print(f"  ! retry {work.work_id}: {type(e2).__name__} {e2}")
                return []
        else:
            return []
    content = r["content"]
    if not content:
        print(f"  ! {work.work_id}: empty content (finish={r['finish_reason']})")
        return []
    try:
        records = json.loads(content).get("records", [])
    except json.JSONDecodeError:
        print(f"  ! {work.work_id}: bad JSON")
        return []
    return [
        Evidence(
            host_species=rec.get("host_species", "").strip(),
            host_common_name=rec.get("host_common_name", "").strip(),
            parasite=rec.get("parasite", "").strip(),
            evidence=rec.get("evidence", "").strip(),
            verbatim_quote=rec.get("verbatim_quote", "").strip(),
            country=rec.get("country", "").strip(),
            year=rec.get("year", "").strip(),
            confidence=rec.get("confidence", "low"),
            work_id=work.work_id,
            doi=work.doi,
            model=model,
        )
        for rec in records
        if rec.get("host_species", "").strip()
    ]


def extract_batch(
    client: MetaClient,
    works: list[Work],
    parasite: str,
    parasite_common: str,
    model: str = MODEL_EXTRACTION,
    rpm: int = 120,  # contributor cap is 150 RPM
) -> list[Evidence]:
    out: list[Evidence] = []
    interval = 60.0 / rpm
    for i, w in enumerate(works, 1):
        t0 = time.time()
        ev = extract_work(client, w, parasite, parasite_common, model)
        out.extend(ev)
        print(f"  [{i}/{len(works)}] {w.work_id.rsplit('/',1)[-1]}: {len(ev)} records")
        dt = time.time() - t0
        if dt < interval:
            time.sleep(interval - dt)
    return out
