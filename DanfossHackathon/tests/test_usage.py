"""Token diet + cost log: prices, the metered client, the lean fact list and the cheap-first retry."""
from types import SimpleNamespace

from comparison_engine import usage
from comparison_engine.facts import Fact
from comparison_engine.interpret import _ask_and_check, lean_facts
from comparison_engine.logic import Logic


def test_cost_uses_the_price_list():
    # 1M input + 1M output on Sonnet 5.5 = $2 + $10; Haiku 5.5 = $0.10 / $0.50 per 1M (prompt up to 100k)
    assert usage.cost("claude-sonnet-5-5", 1_000_000, 1_000_000) == 12.0
    assert round(usage.cost("claude-haiku-5-5", 50_000, 50_000), 6) == round((50_000 * 0.10 + 50_000 * 0.50) / 1e6, 6)
    # Haiku prompts over 100,000 tokens are priced higher
    assert round(usage.cost("claude-haiku-5-5", 200_000, 0), 6) == 0.1
    # unknown model -> Sonnet prices (never underestimate)
    assert usage.cost("some-new-model", 1_000_000, 0) == 2.0


def test_record_collect_and_summary():
    with usage.collect() as calls:
        usage.record("compare", "claude-sonnet-5-5", {"input_tokens": 3000, "output_tokens": 1000,
                                                      "cache_read_input_tokens": 2000})
    assert len(calls) == 1 and calls[0]["usd"] == round((3000 * 2 + 1000 * 10 + 2000 * 0.10) / 1e6, 6)
    usage.record("extract", "claude-haiku-5-5", SimpleNamespace(input_tokens=5000, output_tokens=2000))
    s = usage.summary()
    assert s["total"]["calls"] == 2 and set(s["byPurpose"]) == {"compare", "extract"}


def test_metered_client_records_every_call():
    response = SimpleNamespace(usage=SimpleNamespace(input_tokens=100, output_tokens=50))
    fake = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: response))
    with usage.collect() as calls:
        assert usage.MeteredClient(fake, "extract").messages.create(model="claude-haiku-5-5") is response
    assert calls[0]["purpose"] == "extract" and calls[0]["model"] == "claude-haiku-5-5"


def _fact(i, product, attribute, status="ok"):
    return Fact(f"F{i}", product, "target" if product == "a" else "candidate", attribute, "1", "bar", 1.0, "bar",
                "x.pdf", 1, "", status)


def test_lean_facts_drops_code_numbers_and_keeps_shared_attributes_first():
    facts = [_fact(1, "a", "code_number_pvb"), _fact(2, "a", "only_a"), _fact(3, "a", "max_pressure"),
             _fact(4, "b", "max_pressure"), _fact(5, "b", "ordering_code_spool")]
    logic = Logic("valve", {"better": {}, "attribute_aliases": {}}, "")
    sent = lean_facts(facts, logic, None, max_per_product=1)
    assert [f.id for f in sent] == ["F3", "F4"]            # shared attribute wins, codes never sent


def test_retry_can_use_a_stronger_model():
    used = []
    cheap = lambda s, p, schema=None: used.append("cheap") or '{"x": 1}'   # noqa: E731
    strong = lambda s, p, schema=None: used.append("strong") or '{"x": 2}'  # noqa: E731
    check = lambda a: [] if a.get("x") == 2 else ["wrong"]                   # noqa: E731
    answer, problems, attempts = _ask_and_check(cheap, "sys", "prompt", check, strong)
    assert used == ["cheap", "strong"] and answer == {"x": 2} and not problems and attempts == 2


def _flow(i, product, attribute, value, page):
    return Fact(f"F{i}", product, "target" if product == "pvg" else "candidate", attribute, str(value), "l/min",
                float(value), "l/min", f"{product}.pdf", page, "", "ok")


def test_paired_rows_for_the_same_target_fact_are_merged_into_one_row():
    from comparison_engine.results import AnalysisResult
    from comparison_engine.website import to_website
    facts = [_flow(1, "pvg", "max_inlet_flow", 140, 12), _flow(2, "kmp", "rated_flow_port_p", 70, 2),
             _flow(3, "kpes6", "rated_flow_port_p", 70, 3)]
    point = lambda cand, fid: {"topic": "Flow at P port", "candidate": cand, "target_facts": ["F1"],   # noqa: E731
                               "candidate_facts": [fid], "candidate_value_is": "lower", "verdict": "target_better",
                               "why": "", "confidence": "high"}
    result = AnalysisResult(status="ok", message="", target={"product_id": "pvg", "product_name": "PVG"},
                            shortlist=[{"product_id": "kmp"}, {"product_id": "kpes6"}], facts=facts,
                            interpretation={"points": [point("kmp", "F2"), point("kpes6", "F3")]})
    rows = [a for a in to_website(result)["attributes"] if a.get("paired")]
    assert len(rows) == 1                                           # was one row per competitor
    assert all(rows[0]["values"][p]["value"] for p in ("pvg", "kmp", "kpes6"))


def test_lean_facts_limits_values_only_one_product_has():
    facts = [_fact(i, "a", f"only_a_{i}") for i in range(30)] + [_fact(100, "a", "shared"), _fact(101, "b", "shared")]
    logic = Logic("valve", {"better": {}, "attribute_aliases": {}}, "")
    sent = lean_facts(facts, logic, None, max_per_product=40, max_unshared=5)
    assert sum(f.product_id == "a" for f in sent) == 6              # the shared one + 5 of its own
    # words from the customer's brief pick WHICH unshared facts are sent
    facts = [_fact(1, "a", "pve_reaction_time"), _fact(2, "a", "spool_stroke"), _fact(3, "a", "max_inlet_flow")]
    sent = lean_facts(facts, logic, None, max_per_product=40, max_unshared=1, context="must-have topics: flow")
    assert [f.id for f in sent] == ["F3"]
