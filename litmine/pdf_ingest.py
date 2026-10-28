"""Ingest manually-downloaded full-text PDFs into sections.parquet.

    python litmine/pdf_ingest.py ~/Downloads/*.pdf \
        --map data/litmine/gold33_missing_fulltext.csv \
        --out data/litmine/sections.parquet

Text: pdftotext -layout. Map: DOI found in PDF text -> lookup map csv
(pmid, doi). Sectioning: header-line regex -> BioC-ish section_type.
Resume: pmids already in --out are skipped.
"""
import argparse
import csv
import os
import re
import subprocess
import sys

DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9a-z]+")
SEC_RES = [
    ("METHODS", re.compile(r"^\s*(materials?\s+and\s+)?methods?\s*$|"
                           r"^\s*experimental\s+procedures?\s*$", re.I)),
    ("RESULTS", re.compile(r"^\s*results?\s*$", re.I)),
    ("DISCUSS", re.compile(r"^\s*discussion\s*$", re.I)),
    ("INTRO",   re.compile(r"^\s*introduction\s*$", re.I)),
    ("REF",     re.compile(r"^\s*(references?|literature\s+cited|"
                           r"acknowledg?e?ments?)\s*$", re.I)),
]
KEEP = {"TITLE", "ABSTRACT", "METHODS", "RESULTS", "TABLE", "FIG", "SUPP"}


def extract_text(pdf):
    try:
        r = subprocess.run(["pdftotext", "-layout", pdf, "-"],
                           capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        sys.exit("pdftotext not found — install poppler-utils")
    return r.stdout if r.returncode == 0 else ""


def find_doi(text):
    m = DOI_RE.search(text[:6000])
    return m.group(0).rstrip(".,;)") if m else ""


def window_chunks(text, size=4000, overlap=200):
    """Split oversized section text into ~size-char windows."""
    text = text.strip()
    if len(text) <= size:
        return [(0, text)]
    out, i = [], 0
    while i < len(text):
        out.append((i, text[i:i + size]))
        i += size - overlap
    return out


def split_sections(text):
    """Yield (section_type, chunk_text) — header-driven split."""
    lines = text.splitlines()
    cur, buf = "TITLE", []
    for ln in lines:
        hit = next((s for s, rx in SEC_RES if rx.match(ln)), None)
        if hit:
            if buf:
                yield cur, "\n".join(buf)
            cur, buf = hit, []
        else:
            buf.append(ln)
    if buf:
        yield cur, "\n".join(buf)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdfs", nargs="+")
    ap.add_argument("--map", required=True)
    ap.add_argument("--pmid-map",
                    help="csv filename,pmid para pdfs sin DOI extraible")
    ap.add_argument("--out", default="data/litmine/sections.parquet")
    args = ap.parse_args()

    import pandas as pd
    doi2row = {}
    for r in csv.DictReader(open(args.map)):
        if r.get("doi"):
            doi2row[r["doi"].strip().lower().rstrip(".,;)")] = r
    file2pmid = {}
    if args.pmid_map:
        for r in csv.DictReader(open(args.pmid_map)):
            file2pmid[r["filename"]] = r["pmid"]

    done = set()
    if os.path.exists(args.out):
        done = set(pd.read_parquet(args.out).pmid.unique())

    rows, unmapped = [], []
    for pdf in args.pdfs:
        text = extract_text(pdf)
        if not text.strip():
            print(f"{os.path.basename(pdf)}: texto vacio", file=sys.stderr)
            continue
        doi = find_doi(text).lower()
        rec = doi2row.get(doi)
        pmid = (rec or {}).get("pmid") or file2pmid.get(
            os.path.basename(pdf))
        if not pmid:
            unmapped.append((os.path.basename(pdf), doi))
            continue
        if pmid in done:
            continue
        for st, chunk in split_sections(text):
            if st not in KEEP or len(chunk.strip()) < 40:
                continue
            for off, win in window_chunks(chunk):
                rows.append({"pmid": pmid,
                             "pmcid": "PDF:" + os.path.basename(pdf),
                             "section_type": st, "pass_type": "pdf",
                             "text": win.strip(), "offset": off})
    if rows:
        df = pd.DataFrame(rows)
        if os.path.exists(args.out):
            df = pd.concat([pd.read_parquet(args.out), df],
                           ignore_index=True)
        df.to_parquet(args.out, index=False)
        print(f"+{len(rows)} passages, {df.pmid.nunique()} pmids total",
              file=sys.stderr)
    if unmapped:
        print("sin mapa (revisar doi):", file=sys.stderr)
        for f, d in unmapped:
            print(f"  {f} -> {d or 'no doi'}", file=sys.stderr)


if __name__ == "__main__":
    main()
