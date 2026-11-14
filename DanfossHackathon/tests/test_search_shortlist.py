import pytest

from comparison_engine.logic import canonical_family, load_logic
from comparison_engine.search import search
from comparison_engine.shortlist import shortlist

COMPX = "CompetitorX|DEMO pump 71"


# ---------------------------------------------------------------- logic files
def test_general_and_family_logic_are_merged():
    logic = load_logic("pump")
    assert logic.files == ["_general.md", "pump.md"]
    assert logic.settings["weights"]["max_displacement"] == 5          # from pump.md
    assert logic.settings["shortlist_size"] == 8                       # from _general.md
    assert "General comparison logic" in logic.text and "open circuit pumps" in logic.text
    assert logic.size_attribute() == "max_displacement"


def test_missing_family_file_uses_general_only():
    logic = load_logic("gearbox")
    assert logic.files == ["_general.md"]
    assert logic.settings["weights"] == {}


def test_family_aliases():
    assert canonical_family(" Axial  Piston Pump") == "pump"
    assert canonical_family("valve") == "valve"


# ---------------------------------------------------------------- search
@pytest.mark.parametrize("query", ["competitorx pump71", "COMPETITORX pump 71", "competitorx pmp 71",
                                   "compettorx 71", "a71vo-demo", "A71VO DEMO", "CompetitorX|DEMO pump 71"])
def test_search_spelling_variants(query):
    hits = search(query)
    assert not hits.empty
    assert hits.iloc[0]["product_id"] == COMPX


def test_search_all_words_matter():
    hits = search("danfoss 75")
    assert list(hits["product_id"]) == ["Danfoss|DEMO pump 75"]


def test_search_numbers_must_match_exactly():
    assert "Danfoss|DEMO pump 100" not in set(search("danfoss 10")["product_id"])


def test_search_no_hit_means_offer_upload():
    assert search("xyz unknown thing").empty
    assert search("").empty


def test_search_filters():
    assert set(search("demo pump", brand="Danfoss")["brand"]) == {"Danfoss"}


# ---------------------------------------------------------------- shortlist
def test_shortlist_only_danfoss_same_family():
    s = shortlist(COMPX)
    assert len(s) == 3
    assert all(pid.startswith("Danfoss|") for pid in s["product_id"])
    assert s.iloc[0]["product_id"] == "Danfoss|DEMO pump 75"
    assert s.attrs["family"] == "pump"


def test_logic_hard_filters_are_only_hints():
    s = shortlist(COMPX).set_index("product_id")
    # pump 45 is far from 71 cc -> NOT removed, but flagged and ranked lower
    assert "Danfoss|DEMO pump 45" in s.index
    assert any(f.startswith("hint:") for f in s.loc["Danfoss|DEMO pump 45", "flags"])
    assert s.loc["Danfoss|DEMO pump 45", "score"] < s.loc["Danfoss|DEMO pump 75", "score"]


def test_must_have_removes_known_failures():
    s = shortlist(COMPX, must_haves={"rated_pressure": 270})
    ids = set(s["product_id"])
    assert ids == {"Danfoss|DEMO pump 100"}                       # 280 bar (unverified) passes
    removed = {e["product_id"] for e in s.attrs["excluded"]}
    assert removed == {"Danfoss|DEMO pump 45", "Danfoss|DEMO pump 75"}


def test_must_have_not_stated_is_kept_and_flagged():
    s = shortlist(COMPX, must_haves={"weight": {"max": 40}}).set_index("product_id")
    assert "must-have not stated: weight" in s.loc["Danfoss|DEMO pump 100", "flags"]
    assert "Danfoss|DEMO pump 75" in s.index


def test_text_must_have():
    s = shortlist(COMPX, must_haves={"control_options": "EP"})
    assert "Danfoss|DEMO pump 75" in set(s["product_id"])          # has PC, LS, EP
    s2 = shortlist(COMPX, must_haves={"control_options": "XX"})
    assert "Danfoss|DEMO pump 75" not in set(s2["product_id"])


def test_shortlist_unknown_product():
    with pytest.raises(KeyError):
        shortlist("Nobody|Nothing")
