"""meta.json must carry n_species from the catalog when no suit PNGs exist.

export_serve counts suit_*.png for n_species; a src without renders
(e.g. fresh export before render_3857) yields null and trips the qa
codigo gate (n_species>=1000). Fallback: catalog row count.
"""
import json
from pathlib import Path

import pandas as pd

from pipeline.export_serve import main


def _winner(path: Path, n: int = 3) -> None:
    pd.DataFrame({
        "specieskey": list(range(1, n + 1)),
        "species": [f"sp{i}" for i in range(1, n + 1)],
        "aic": [float(i) for i in range(1, n + 1)],
    }).to_csv(path, index=False)


def test_n_species_falls_back_to_catalog(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    _winner(src / "species_winner.csv", 3)
    main(str(src), str(dst))
    meta = json.loads((dst / "meta.json").read_text())
    assert meta["n_species"] == 3


def test_n_species_prefers_suit_count(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    _winner(src / "species_winner.csv", 5)
    (src / "suit_1.png").write_bytes(b"\x89PNG" + b"0" * 100)
    (src / "suit_2.png").write_bytes(b"\x89PNG" + b"0" * 100)
    main(str(src), str(dst))
    meta = json.loads((dst / "meta.json").read_text())
    assert meta["n_species"] == 2
