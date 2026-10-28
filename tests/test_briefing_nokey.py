"""Without baked file and without key, briefing() must degrade like
explain() ({brief: None, note}) instead of raising KeyError."""
import json

from agent.briefing import briefing


def test_briefing_without_key_no_crash(tmp_path, monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    (tmp_path / "species_winner.csv").write_text(
        "specieskey,species,aic,best_temp,best_prec\n"
        "1,sp_a,10.0,bio01,bio12\n")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    out = briefing(data_dir=tmp_path)
    assert out.get("brief") is None
    assert "facts" in out
    assert json.dumps(out.get("facts"))  # facts are real, not empty
