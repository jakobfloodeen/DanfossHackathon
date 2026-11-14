"""AI step tests. The LLM is ALWAYS a fake function here - no API key, no cost."""
import copy
import json

import pytest

from comparison_engine import analyse, interpret, load_logic
from comparison_engine.facts import build_facts, code_differences
from comparison_engine.guardrails import add_missing_verify, check_interpretation, numbers_in
from comparison_engine.llm import LLMUnavailable, extract_json

COMPX = "CompetitorX|DEMO pump 71"
P75, P45, P100 = "Danfoss|DEMO pump 75", "Danfoss|DEMO pump 45", "Danfoss|DEMO pump 100"
CANDS = [P75, P45, P100]

# Fact ids in the demo (target first, then the shortlist order pump 75, 45, 100):
#   F1 displacement 71 | F2 rated 280 bar | F3 peak 350 | F4/F5 speed 2550/2200 (conflict) | F6 weight missing
#   F9 pump75 displacement 75 | F10 rated 260 | F12 peak 310 | F13 speed 2400 | F15 controls PC, LS, EP
GOOD = {
    "comparable": True,
    "not_comparable_reason": None,
    "best_match": {"product_id": P75, "why": "Closest displacement: 75 vs 71 cc/rev.", "fact_ids": ["F1", "F9"]},
    "alternatives": [{"product_id": P100, "why": "Same rated pressure, but much larger.", "fact_ids": ["F2", "F23"]}],
    "points": [
        {"topic": "Size", "candidate": P75, "target_facts": ["F1"], "candidate_facts": ["F9"],
         "candidate_value_is": "higher", "verdict": "trade_off", "why": "75 vs 71 cc/rev, about 6% more.",
         "confidence": "high"},
        {"topic": "Rated pressure", "candidate": P75, "target_facts": ["F2"], "candidate_facts": ["F10"],
         "candidate_value_is": "lower", "verdict": "target_better", "why": "260 bar vs 280 bar.",
         "confidence": "high"},
    ],
    "application_fit": {"text": "Load sensing is available on both (PC, LS).", "fact_ids": ["F7", "F15"]},
    "talking_points": [{"text": "Adds EP control option.", "fact_ids": ["F15"]}],
    "tradeoffs": [{"text": "Rated pressure is 7% lower.", "fact_ids": ["F2", "F10"]}],
    "verify": [{"fact_id": "F4", "reason": "Sources disagree on max speed."}],
    "overall_confidence": "medium",
}


def fake(*answers):
    """A fake LLM that returns the given answers one after the other and records the calls."""
    calls = []

    def ask(system, prompt, schema=None):
        calls.append({"system": system, "prompt": prompt, "schema": schema})
        a = answers[min(len(calls), len(answers)) - 1]
        if isinstance(a, Exception):
            raise a
        return a if isinstance(a, str) else json.dumps(a)
    ask.calls = calls
    return ask


@pytest.fixture
def setup():
    logic = load_logic("pump")
    facts = build_facts(COMPX, CANDS, logic=logic)
    return facts, code_differences(facts, logic), logic


def check(answer, setup, user_text=""):
    facts, diffs, logic = setup
    return check_interpretation(answer, facts, diffs, COMPX, CANDS, logic, user_text)


def bad(**changes):
    a = copy.deepcopy(GOOD)
    for path, value in changes.items():
        obj = a
        keys = path.split("__")
        for k in keys[:-1]:
            obj = obj[int(k)] if k.isdigit() else obj[k]
        obj[int(keys[-1]) if keys[-1].isdigit() else keys[-1]] = value
    return a


# ---------------------------------------------------------------- facts
def test_fact_ids_match_the_demo(setup):
    facts, diffs, _ = setup
    by = {f.id: f for f in facts}
    assert (by["F1"].attribute, by["F9"].product_id, by["F10"].norm_value) == ("max_displacement", P75, 260)
    assert by["F4"].status == by["F5"].status == "conflict"
    assert by["F6"].status == "missing" and by["F6"].display() == "not stated"
    assert by["F11"].status == "calculated" and by["F11"].based_on == ["F9", "F13"]
    assert len({f.id for f in facts}) == len(facts)


def test_code_differences(setup):
    _, diffs, _ = setup
    d = {(x.candidate, x.attribute): x for x in diffs}
    assert d[(P75, "rated_pressure")].better == "target"
    assert d[(P75, "rated_pressure")].diff_pct == pytest.approx(-7.1)
    assert (P75, "max_speed") not in d                     # conflict -> code does not pick a value


# ---------------------------------------------------------------- guardrails
def test_good_answer_has_no_problems(setup):
    assert check(GOOD, setup) == []


def test_invented_number_is_caught(setup):
    p = check(bad(talking_points=[{"text": "Delivers 300 bar continuous.", "fact_ids": ["F10"]}]), setup)
    assert any("number 300" in x for x in p)


def test_unknown_fact_id_is_caught(setup):
    p = check(bad(best_match__fact_ids=["F1", "F999"]), setup)
    assert any("unknown fact F999" in x for x in p)


def test_fact_of_wrong_product_is_caught(setup):
    p = check(bad(points__0__candidate_facts=["F16"]), setup)          # F16 belongs to pump 45
    assert any("F16 is not a fact of" in x for x in p)


def test_missing_citation_is_caught(setup):
    p = check(bad(talking_points=[{"text": "Great pump.", "fact_ids": []}]), setup)
    assert any("no fact cited" in x for x in p)


def test_wrong_verdict_is_caught(setup):
    p = check(bad(points__1__verdict="candidate_better"), setup)       # 260 < 280 bar, higher is better
    assert any("code expects 'target_better'" in x for x in p)


def test_wrong_direction_is_caught(setup):
    p = check(bad(points__1__candidate_value_is="higher"), setup)
    assert any("code computes 'lower'" in x for x in p)


def test_similar_on_big_gap_is_caught(setup):
    a = bad(points__0__candidate_facts=["F22"], points__0__why="Size 100 vs 71 cc/rev.",   # +41 %
            points__0__verdict="similar")
    a["points"][0]["candidate"] = P100
    p = check(a, setup)
    assert any("verdict 'similar' but the difference" in x for x in p)


def test_product_not_on_shortlist_is_caught(setup):
    p = check(bad(best_match__product_id="Danfoss|Imaginary"), setup)
    assert any("not on the shortlist" in x for x in p)


def test_bad_shape_is_caught(setup):
    a = copy.deepcopy(GOOD)
    del a["verify"]
    a["overall_confidence"] = "very sure"
    p = check(a, setup)
    assert any("missing field 'verify'" in x for x in p)
    assert any("'very sure' is not one of" in x for x in p)


def test_not_comparable_needs_reason(setup):
    a = bad(best_match=None)
    assert any("comparable is true" in x for x in check(a, setup))
    a = bad(best_match=None, comparable=False, not_comparable_reason="")
    assert any("not_comparable_reason is empty" in x for x in check(a, setup))


def test_numbers_from_the_user_are_allowed(setup):
    a = bad(talking_points=[{"text": "Meets the 250 bar requirement.", "fact_ids": ["F10"]}])
    assert check(a, setup) != []
    assert check(a, setup, user_text="customer needs 250 bar") == []


def test_logic_thresholds_are_allowed(setup):
    # found in the first live run: the AI quoted the 3 % similarity band from the LOGIC file
    a = bad(points__0__why="75 vs 71 cc/rev (+5.7%), not within the 3% similarity band.")
    assert check(a, setup) == []


def test_numbers_in_names_and_codes_are_ignored():
    assert numbers_in("DEMO pump 75 replaces A71VO-DEMO [F12] at 4.33 in3/rev", ["DEMO pump 75"]) == ["4.33"]


def test_add_missing_verify(setup):
    facts, _, _ = setup
    a = add_missing_verify(copy.deepcopy(GOOD), facts, COMPX)
    ids = {v["fact_id"] for v in a["verify"]}
    assert {"F4", "F5", "F6"} <= ids                       # conflict + missing of the target
    assert any("added by code" in v["reason"] for v in a["verify"])


def test_extract_json_tolerates_fences():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Here you go: {"a": 1} done') == {"a": 1}
    with pytest.raises(ValueError):
        extract_json("no json here")


# ---------------------------------------------------------------- interpret: retry, problems, fallback, cache
def test_good_answer_first_time():
    ask = fake(GOOD)
    r = interpret(COMPX, None, "best replacement?", "OEM cranes", ask=ask)
    assert r.status == "ok" and r.problems == [] and r.meta["attempts"] == 1
    assert r.best_match_id == P75
    assert ask.calls[0]["schema"] is not None
    assert "General comparison logic" in ask.calls[0]["system"]           # LOGIC text is in the prompt
    assert "F4 | CompetitorX|DEMO pump 71 | max_speed" in ask.calls[0]["prompt"]


def test_retry_fixes_the_answer():
    wrong = bad(points__1__verdict="candidate_better")
    ask = fake(wrong, GOOD)
    r = interpret(COMPX, None, ask=ask)
    assert r.status == "ok" and r.meta["attempts"] == 2
    assert "code expects 'target_better'" in ask.calls[1]["prompt"]      # problems are sent back


def test_problems_stay_visible_after_second_failure():
    wrong = bad(tradeoffs=[{"text": "Costs 999 EUR less.", "fact_ids": ["F10"]}])
    r = interpret(COMPX, None, ask=fake(wrong, wrong))
    assert r.status == "ok_with_problems"
    assert any("number 999" in p for p in r.problems)
    assert r.interpretation is not None                                    # shown, but flagged


def test_unreadable_answer_falls_back():
    r = interpret(COMPX, None, ask=fake("sorry, no JSON", "still no JSON"))
    assert r.status == "no_ai" and r.problems
    assert r.facts and r.differences and r.comparison_rows


def test_fallback_without_api_key():
    r = interpret(COMPX, None)                    # real ask_claude, but no key in the test environment
    assert r.status == "no_ai" and "ANTHROPIC_API_KEY" in r.message
    assert r.best_match_id == P75                 # code ranking still works
    assert r.onepager["recommended"]["why"].startswith("Code ranking only")


@pytest.mark.parametrize("error", [LLMUnavailable("the AI did not answer within 90 s"), TimeoutError("slow"),
                                   RuntimeError("boom")])
def test_fallback_on_api_error_or_timeout(error):
    r = interpret(COMPX, None, ask=fake(error))
    assert r.status == "no_ai" and "not available" in r.message
    assert r.facts and r.comparison_rows


def test_cache_avoids_second_call():
    ask = fake(GOOD)
    interpret(COMPX, None, "q", "c", ask=ask)
    r = interpret(COMPX, None, "q", "c", ask=ask)
    assert len(ask.calls) == 1 and r.meta["cached"]
    interpret(COMPX, None, "another question", "c", ask=ask)
    assert len(ask.calls) == 2                    # different input -> new call


def test_focus_attributes_limit_the_facts_sent():
    ask = fake(GOOD)
    interpret(COMPX, None, ask=ask, focus_attributes=["rated pressure"])
    prompt = ask.calls[0]["prompt"]
    assert "| rated_pressure |" in prompt and "| weight |" not in prompt


def test_result_is_json_serialisable():
    r = interpret(COMPX, None, ask=fake(GOOD))
    d = r.to_dict()
    json.dumps(d)
    assert d["onepager"]["talking_points"][0]["sources"][0]["link"].endswith("#page=7")
    rows = {row["attribute"]: row for row in d["comparison_rows"]}
    assert rows["max_speed"]["target"]["display"].endswith("(sources disagree)")
    assert rows["rated_pressure"]["ai"]["verdict"] == "target_better"


# ---------------------------------------------------------------- analyse
def test_analyse_not_found():
    r = analyse("totally unknown pump")
    assert r.status == "not_found" and "Upload" in r.message


def test_analyse_needs_choice():
    r = analyse("danfoss demo pump")
    assert r.status == "needs_choice" and len(r.search_hits) == 3


def test_analyse_full_flow_with_fake_llm():
    r = analyse("compettorx pump 71", "best replacement?", "OEM, truck cranes", ask=fake(GOOD))
    assert r.status == "ok" and r.target["product_id"] == COMPX and r.best_match_id == P75


def test_analyse_without_ai():
    r = analyse(COMPX, use_ai=False)
    assert r.status == "no_ai" and "not requested" in r.message
