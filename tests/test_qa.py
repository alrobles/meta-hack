"""qa.check() quorum: full serve passes 3/4 without manifest (brain
abstains by design); broken meta fails codigo and blocks deploy."""
import inspect
import json
import os

import pandas as pd

from agent import qa
from agent.qa import check


def _full_serve(s, d) -> None:
    import pathlib
    s, d = pathlib.Path(s), pathlib.Path(d)
    s.mkdir(parents=True, exist_ok=True)
    d.mkdir(parents=True, exist_ok=True)
    (s / "meta.json").write_text(json.dumps({
        "built": "2026-10-27 00:00 UTC",
        "n_species": 1200,
        "n_detections": 9000,
        "suit_bbox": [[0, 0], [1, 1]],
        "risk_png": "risk.png",
        "risk_bbox": [[0, 0], [1, 1]],
        "reservoir_sum_png": "reservoir_sum.png",
    }))
    pd.DataFrame({"p_host": [0.5 + i / 100 for i in range(12)],
                  "label": [1] * 12}).to_parquet(s / "susceptibility.parquet")
    feats = [{"type": "Feature", "properties": {},
              "geometry": {"type": "Point", "coordinates": [0, 0]}}
             for _ in range(8000)]
    (s / "detections.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": feats}))
    for png in ("risk.png", "reservoir_sum.png", "reservoir_max.png",
                "reservoir_prob.png"):
        (s / png).write_bytes(os.urandom(60_000))
    (d / "detect_seen.json").write_text(json.dumps({"n": 9000}))


def test_check_full_serve_passes_without_manifest(tmp_path):
    s, d = tmp_path / "serve", tmp_path / "data"
    _full_serve(s, d)
    r = check(serve=s, data=d, manifest=None)
    assert r["verdict"]["codigo"] is True
    assert r["verdict"]["seguridad"] is True
    assert r["verdict"]["ux"] is True
    assert r["verdict"]["brain"] is False  # abstain, no manifest
    assert r["pass"] is True and r["quorum"] == "3/4"
    assert json.loads((s / "qa_report.json").read_text())["pass"] is True


def test_check_broken_meta_blocks(tmp_path):
    s, d = tmp_path / "serve", tmp_path / "data"
    _full_serve(s, d)
    (s / "meta.json").write_text(json.dumps({"built": "x"}))
    r = check(serve=s, data=d, manifest=None)
    assert r["verdict"]["codigo"] is False
    assert r["pass"] is False


def test_check_default_serve_is_app_data():
    assert inspect.signature(check).parameters["serve"].default == "app/data"
