"""Unvalidated limit/top must not reach SQL (negative/huge -> 500).

Contract: limit/top accept 1..1000 (FastAPI 422 outside), so DuckDB
never sees a negative LIMIT or an unbounded scan dump.
"""
from fastapi.testclient import TestClient

from api.main import app

c = TestClient(app)


def test_species_negative_limit_no_500():
    r = c.get("/species?limit=-1")
    assert r.status_code in (200, 422)


def test_species_huge_limit_capped():
    r = c.get("/species?limit=1000000")
    assert r.status_code in (200, 422)
    if r.status_code == 200:
        assert len(r.json()) <= 1000


def test_susceptibility_huge_top_no_500():
    r = c.get("/susceptibility?top=1000000")
    assert r.status_code in (200, 422)
