"""
Tiny example page: how the website calls the comparison engine.  (Not the real website - an example.)

    REPO_DIR=examples/repository_demo streamlit run examples/demo_page.py

The engine does all the work; this page only shows result.status, result.comparison_rows,
result.interpretation, result.problems and result.onepager.
"""
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))      # so 'comparison_engine' imports
from comparison_engine import analyse, has_api_key, repository_report  # noqa: E402

st.set_page_config(page_title="Comparison engine demo", layout="wide")
st.title("Competitor comparison - engine demo")

with st.sidebar:
    st.caption("Repository")
    st.dataframe(repository_report()[["file", "loaded", "reason"]], hide_index=True)
    st.caption("AI key found" if has_api_key() else "No AI key - code-only results")

query = st.text_input("Competitor product (name or model code)", "competitorx pump 71")
question = st.text_input("Question", "Which Danfoss pump is the best replacement, and why?")
customer = st.text_input("Customer context", "OEM, truck-mounted cranes, needs load sensing")
min_pressure = st.number_input("Must-have: rated pressure at least (bar, 0 = none)", 0, 1000, 0)
must = {"rated_pressure": min_pressure} if min_pressure else None

col1, col2 = st.columns(2)
show_facts = col1.button("Show facts (free, no AI)")
ask_ai = col2.button("Ask AI (1 API call, cached)", type="primary")   # AI only on an explicit click

if show_facts or ask_ai:
    result = analyse(query, question, customer, must_haves=must, use_ai=ask_ai)
    st.session_state["result"] = result
result = st.session_state.get("result")

if result:
    if result.status == "not_found":
        st.warning(result.message)
        st.file_uploader("Upload the datasheet (PDF)", type="pdf")      # the converter team plugs in here
        st.stop()
    if result.status == "needs_choice":
        st.info(result.message)
        st.dataframe(result.search_hits, hide_index=True)
        st.stop()

    {"ok": st.success, "ok_with_problems": st.warning}.get(result.status, st.info)(result.message)
    for p in result.problems:
        st.error(p)

    st.subheader(f"{result.target['product_id']}  vs  {result.best_match_id}")
    table = [{"attribute": r["attribute"], "competitor": r["target"]["display"],
              "Danfoss": r["candidate"]["display"], "code": r["code_verdict"],
              "AI": (r["ai"] or {}).get("verdict", ""), "flags": ", ".join(r["flags"]),
              "sources": " | ".join(s["text"] for s in r["target"]["sources"] + r["candidate"]["sources"])}
             for r in result.comparison_rows]
    st.dataframe(table, hide_index=True, width="stretch")

    one = result.onepager
    if result.interpretation:
        st.markdown(f"**Recommendation:** {one['recommended']['product_id']} - {one['recommended']['why']}")
        st.markdown("**Talking points**")
        for t in one["talking_points"]:
            links = ", ".join(f"[{s['fact_id']}]({s['link']})" if s["link"] else s["fact_id"] for s in t["sources"])
            st.markdown(f"- {t['text']} ({links})")
    st.markdown("**Verify before the meeting**")
    for v in one["verify"]:
        f = v["fact"] or {}
        st.markdown(f"- {f.get('product_id')} · {f.get('attribute')}: {v['reason']}")
    with st.expander("All facts"):
        st.dataframe([f.to_dict() for f in result.facts], hide_index=True)
