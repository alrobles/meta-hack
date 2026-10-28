"""Literature-mining driver: OpenAlex -> Muse Spark extraction -> host list.

    cd meta-hack
    export MODEL_API_KEY="$(cat ~/env/meta-key)"
    python -m agent.run_mine --limit 25            # default target: H5N1
    python -m agent.run_mine --parasite "Cochliomyia hominivorax" \
        --parasite-common "New World screwworm, screwworm" --limit 25
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from .extract import extract_batch
from .host_list import HostList
from .lit_search import search
from .meta_client import MODEL_EXTRACTION, MetaClient
from .resolve import resolve_host_list

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Flagship pathogen (HPAI pivot, 2026-10-26 — see docs/data-sources.md)
PARASITE = "Influenza A virus subtype H5N1"
PARASITE_COMMON = ("highly pathogenic avian influenza, HPAI, H5N1, "
                   "bird flu, avian influenza")


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--parasite", default=PARASITE,
                    help="target pathogen (scientific name)")
    ap.add_argument("--parasite-common", default=PARASITE_COMMON,
                    help="comma-separated common names")
    ap.add_argument("--query", default=None,
                    help="OpenAlex query (default derived from parasite)")
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--min-year", type=int, default=None)
    ap.add_argument("--model", default=MODEL_EXTRACTION)
    ap.add_argument("--out", type=Path, default=None,
                    help="host list JSONL (default data/host_list_<parasite>.jsonl)")
    ap.add_argument("--rpm", type=int, default=120)
    ap.add_argument("--no-resolve", action="store_true",
                    help="skip GBIF taxonomic resolution")
    args = ap.parse_args(argv)

    query = args.query or f'"{args.parasite}" host OR mammal OR wildlife'
    out = args.out or DATA_DIR / f"host_list_{_slug(args.parasite)}.jsonl"

    print(f"Searching OpenAlex: {query!r} (limit {args.limit})")
    works = search(query, limit=args.limit, min_year=args.min_year)
    n_abs = sum(1 for w in works if w.abstract)
    print(f"  {len(works)} works, {n_abs} with abstracts")

    client = MetaClient()
    ev = extract_batch(
        client, works, args.parasite, args.parasite_common,
        model=args.model, rpm=args.rpm,
    )
    print(f"Extracted {len(ev)} evidence records")

    hosts = HostList.load(out)
    before = len(hosts.entries)
    new, added = hosts.merge_many(ev)
    if not args.no_resolve and hosts.entries:
        print("Resolving host names via GBIF...")
        print(f"  {resolve_host_list(hosts)}")
    hosts.save(out)
    print(f"Host list: {before} -> {len(hosts.entries)} entries "
          f"({new} new hosts, {added} new evidence) -> {out}")
    print("\nSpend:")
    print(client.spend.report())
    return 0


if __name__ == "__main__":
    sys.exit(main())
