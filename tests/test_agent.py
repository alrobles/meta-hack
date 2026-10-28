"""Unit tests for the agent's pure-python parts — no network."""

from agent.extract import Evidence
from agent.host_list import HostList, norm_name
from agent.lit_search import _reconstruct_abstract
from agent.meta_client import Usage, Spend
from agent.resolve import _vernacular_overlap
from agent.aphis import Detection, _ms
from agent.run_mine import _slug


def _ev(host="Odocoileus virginianus", parasite="Cochliomyia hominivorax",
        quote="larvae in deer", work="W1", conf="high", **kw):
    return Evidence(
        host_species=host, host_common_name=kw.get("cn", "white-tailed deer"),
        parasite=parasite, evidence=kw.get("ev", "larvae collected"),
        verbatim_quote=quote, country=kw.get("country", "USA"),
        year=kw.get("year", "2016"), confidence=conf,
        work_id=work, doi=kw.get("doi"), model=kw.get("model", "m"),
    )


def test_norm_name():
    assert norm_name("  Odocoileus   Virginianus (Key deer) ") == \
        "odocoileus virginianus"


def test_merge_dedup_new_host():
    h = HostList()
    assert h.merge_evidence(_ev()) is True          # new host
    assert h.merge_evidence(_ev()) is False         # same work+quote -> dup
    assert h.merge_evidence(_ev(quote="a second record", work="W2")) is False
    e = next(iter(h.entries.values()))
    assert e["n_evidence"] == 2                     # two distinct evidence kept


def test_merge_confidence_takes_max():
    h = HostList()
    h.merge_evidence(_ev(conf="low", work="W1", quote="q1"))
    h.merge_evidence(_ev(conf="high", work="W2", quote="q2"))
    assert next(iter(h.entries.values()))["confidence"] == "high"


def test_merge_countries_years_union():
    h = HostList()
    h.merge_evidence(_ev(work="W1", quote="q1", country="USA", year="2016"))
    h.merge_evidence(_ev(work="W2", quote="q2", country="Mexico", year="2020"))
    e = next(iter(h.entries.values()))
    assert set(e["countries"]) == {"USA", "Mexico"}
    assert set(e["years"]) == {"2016", "2020"}


def test_roundtrip(tmp_path):
    h = HostList()
    h.merge_evidence(_ev())
    p = tmp_path / "hl.jsonl"
    h.save(p)
    h2 = HostList.load(p)
    assert h2.entries.keys() == h.entries.keys()
    assert h2.merge_evidence(_ev()) is False        # dup survives reload


def test_species_filter():
    h = HostList()
    h.merge_evidence(_ev(host="A b", conf="low", work="W1", quote="q1"))
    h.merge_evidence(_ev(host="C d", conf="high", work="W2", quote="q2"))
    assert h.species("high") == ["C d"]
    assert len(h.species("low")) == 2


def test_abstract_reconstruction():
    inv = {"the": [0, 3], "screwworm": [1], "infests": [2], "deer": [4]}
    assert _reconstruct_abstract(inv) == "the screwworm infests the deer"
    assert _reconstruct_abstract(None) == ""


def test_usage_cost():
    u = Usage(prompt_tokens=1_000_000, completion_tokens=1_000_000,
              cached_tokens=400_000)
    # contributor: 0.6M*0.10 + 0.4M*0.002 + 1M*0.20 = 0.0608
    c = u.cost_usd("muse-spark-1.2-contributor")
    assert abs(c - (0.6 * 0.10 + 0.4 * 0.002 + 1.0 * 0.20)) < 1e-9


def test_spend_report():
    s = Spend()
    s.add("m", 0.01)
    s.add("m", 0.02)
    assert "$0.0300" in s.report() and "2 calls" in s.report()


def test_consolidate_resolved():
    h = HostList()
    for raw, work, quote in [("human", "W1", "q1"), ("humans", "W2", "q2"),
                             ("man", "W3", "q3")]:
        assert h.merge_evidence(_ev(host=raw, work=work, quote=quote, cn=raw))
    # simulate GBIF resolution to the same usageKey
    for e in h.entries.values():
        e["gbif"] = {"usageKey": 2436436, "canonicalName": "Homo sapiens"}
    assert h.consolidate_resolved() == 2
    assert len(h.entries) == 1
    e = next(iter(h.entries.values()))
    assert e["host_species"] == "Homo sapiens"
    assert e["n_evidence"] == 3
    assert {"human", "humans", "man"} <= set(e["common_names"])


def test_ms_epoch():
    assert _ms(1626134400000).year == 2021
    assert _ms(None) is None


def test_detection_is_hpai():
    d = Detection(state="NE", county="Otoe", watershed="",
                  date_collected=None, iav="Detected", h5="Detected",
                  h7="", pathogenicity="High Path AI", lon=None, lat=None)
    assert d.is_hpai
    d.pathogenicity = ""
    assert not d.is_hpai


def test_slug():
    assert _slug("Influenza A virus subtype H5N1") == \
        "influenza_a_virus_subtype_h5n1"


def test_vernacular_overlap():
    res = {"vernacularNames": [{"vernacularName": "Dall sheep"},
                               {"vernacularName": "Thinhorn Sheep"}]}
    assert _vernacular_overlap("dall sheep", res) == "exact"
    assert _vernacular_overlap("sheep", res) == "fuzzy"
    assert _vernacular_overlap("wild and domestic animals", res) is None
