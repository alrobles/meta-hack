"""API smoke: status paths against fixture dirs (no network, no key)."""
import pandas as pd
from fastapi.testclient import TestClient

import api.main as m
from api.main import app


def _catalog(d, n=3):
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({
        "specieskey": list(range(1, n + 1)),
        "species": [f"sp_{i}" for i in range(1, n + 1)],
        "aic": [float(10 + i) for i in range(n)],
        "best_temp": ["bio01"] * n,
        "best_prec": ["bio12"] * n,
    }).to_csv(d / "species_winner.csv", index=False)
    (d / "fit_catalog.csv").write_text("specieskey,pair,aic\n1,x,1.0\n")


def test_health_ok_and_empty(tmp_path, monkeypatch):
    import api.main as mm
    monkeypatch.setattr(mm, "DATA", tmp_path)
    assert TestClient(app).get("/health").json()["status"] == "empty"
    _catalog(tmp_path)
    monkeypatch.setattr(mm, "DATA", tmp_path)
    assert TestClient(app).get("/health").json()["status"] == "ok"


def test_species_joins_p_host(tmp_path, monkeypatch):
    import api.main as mm
    _catalog(tmp_path)
    pd.DataFrame({"specieskey": [1], "p_host": [0.9], "p05": [0.8],
                  "p95": [0.95], "label": [1]}).to_parquet(
        tmp_path / "susceptibility.parquet")
    monkeypatch.setattr(mm, "DATA", tmp_path)
    rows = TestClient(app).get("/species?limit=10").json()
    assert rows[0]["p_host"] == 0.9


def test_species_no_catalog_503(tmp_path, monkeypatch):
    import api.main as mm
    monkeypatch.setattr(mm, "DATA", tmp_path)
    assert TestClient(app).get("/species").status_code == 503


def test_species_fit_404(tmp_path, monkeypatch):
    import api.main as mm
    _catalog(tmp_path)
    monkeypatch.setattr(mm, "DATA", tmp_path)
    assert TestClient(app).get("/species/999/fit").status_code == 404


def test_risk_no_layer_503(tmp_path, monkeypatch):
    import api.main as mm
    monkeypatch.setattr(mm, "DATA", tmp_path)
    assert TestClient(app).get("/risk/watershed").status_code == 503


def test_explain_bad_kind_400(tmp_path, monkeypatch):
    import api.main as mm
    monkeypatch.setattr(mm, "DATA", tmp_path)
    assert TestClient(app).get("/explain/nope/x").status_code == 400


def test_briefing_nokey_degraded(tmp_path, monkeypatch):
    import api.main as mm
    _catalog(tmp_path)
    monkeypatch.setattr(mm, "DATA", tmp_path)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    r = TestClient(app).get("/briefing")
    assert r.status_code == 200
    assert r.json().get("brief") is None


def test_risk_top_ok(tmp_path, monkeypatch):
    import api.main as mm
    _catalog(tmp_path)
    pd.DataFrame({"huc8": ["01060003"], "name": ["W"], "states": ["NH"],
                  "mean_risk": [0.1], "max_risk": [0.5], "n_det": [3],
                  "n_cells": [10], "risk": [0.9]}).to_parquet(
        tmp_path / "risk_watershed.parquet")
    monkeypatch.setattr(mm, "DATA", tmp_path)
    rows = TestClient(app).get("/risk/watershed?top=5").json()
    assert rows[0]["huc8"] == "01060003"
