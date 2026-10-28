"""Briefing facts must surface the ambiguous-tail verdicts.

decisions_all.jsonl is T1 ( ~all accept). The 49 flag_review live in the
tail (decisions_tail.jsonl / decisions_delta.csv). A briefing whose facts
lack the tail honestly reports "nothing flagged" — stale narrative.
"""
from pathlib import Path

from agent.briefing import facts as build_facts


def _write(p: Path, name: str, text: str) -> None:
    (p / name).write_text(text)


def test_facts_include_tail_flags(tmp_path):
    _write(tmp_path, "species_winner.csv",
           "specieskey,species,aic,best_temp,best_prec\n1,sp_a,10.0,bio01,bio12\n2,sp_b,12.0,bio01,bio12\n")
    _write(tmp_path, "decisions_all.jsonl",
           '{"species": "sp_a", "decision": "accept"}\n'
           '{"species": "sp_b", "decision": "accept"}\n')
    _write(tmp_path, "decisions_delta.csv",
           "specieskey,species,decision,why\n"
           "2,sp_b,flag_review,\"small-n unstable ellipsoid\"\n")
    f = build_facts(tmp_path)
    assert f["t1_decisions"] == {"accept": 2}
    assert any("sp_b" in s for s in f["flagged_species"])


def test_facts_without_tail_has_empty_flags(tmp_path):
    _write(tmp_path, "species_winner.csv",
           "specieskey,species,aic,best_temp,best_prec\n1,sp_a,10.0,bio01,bio12\n")
    f = build_facts(tmp_path)
    assert f["flagged_species"] == []
