"""
The web app (owner: P4).   Run it with:   streamlit run app.py

Two pages:
  🔍 Compare  - customer + competitor product -> Danfoss matches, sourced comparison, one-pager
  📚 Library  - add/browse documents, search all pages, fix values, manage customers
"""
import pandas as pd
import streamlit as st

import db
from compare import compare, load_products, match
from llm import explain, has_api_key
from onepager import build_onepager
from schema import FAMILIES, attributes, numeric_attributes

st.set_page_config(page_title="Competitor Intelligence Assistant", page_icon="⚙️", layout="wide")

LINK = st.column_config.LinkColumn
STATUS_OPTIONS = ["ok", "manual", "unverified", "missing", "conflict"]


# =====================================================================================
#  COMPARE PAGE
# =====================================================================================
def compare_page():
    st.title("🔍 Competitor comparison")
    st.caption("Customer + competitor product in → matched Danfoss product, sourced comparison and one-pager out.")

    df = load_products()
    if df.empty:
        st.info("The library has no product data yet. Go to 📚 Library and add a datasheet.")
        return
    if (df["doc_type"] == "demo").any():
        st.warning("Some values are DEMO data (made-up). Remove them in 📚 Library once real datasheets are in.")

    customers = db.get_customers()

    # ---------------- sidebar inputs
    with st.sidebar:
        st.header("1 · Customer")
        names = customers["customer"].tolist()
        customer = st.selectbox("Customer", names + ["Other…"])
        if customer == "Other…":
            customer = st.text_input("Customer name (add it permanently in 📚 Library)", "")
        ctx, uses = "", []
        if customer in names:
            c = customers[customers["customer"] == customer].iloc[0]
            parts = [c["type"], f"Applications: {c['applications']}" if pd.notna(c["applications"]) else "",
                     f"Notes: {c['notes']}" if pd.notna(c["notes"]) else "", f"Source: {c['source']}"]
            ctx = ". ".join(str(p) for p in parts if pd.notna(p) and p)
            st.caption(ctx)
            cp = db.get_customer_products(customer)
            uses = cp.loc[cp["relation"] == "uses_competitor", "product_id"].tolist()

        st.header("2 · Product the customer uses today")
        competitors = sorted(df.loc[df["brand"] != "Danfoss", "product_id"].unique())
        if not competitors:
            st.info("No competitor products in the library yet.")
            return
        default = competitors.index(uses[0]) if uses and uses[0] in competitors else 0
        target = st.selectbox("Competitor product", competitors, index=default,
                              format_func=lambda p: p.replace("|", " · "))
        if uses:
            st.caption("Known from library: " + ", ".join(u.replace("|", " ") for u in uses))
        family = df.loc[df["product_id"] == target, "family"].iloc[0]

        st.header("3 · What matters (weights)")
        weights = {a: st.slider(a.replace("_", " "), 0, 5, 1 if a == "weight" else 3, key=f"w_{family}_{a}")
                   for a in numeric_attributes(family)}

        st.header("4 · Customer must-haves")
        constraints = {}
        for a in numeric_attributes(family):
            unit = attributes(family)[a]["unit"]
            v = st.number_input(f"min {a.replace('_', ' ')} ({unit})", value=0.0, step=10.0, key=f"min_{family}_{a}")
            if v > 0:
                constraints[a] = v

    # ---------------- match
    st.subheader("Best Danfoss matches")
    matches = match(df, target, weights, constraints)
    if matches.empty:
        st.info("No Danfoss product of this type meets the must-haves (or there is no data). "
                "Relax the constraints or add Danfoss datasheets in 📚 Library.")
        return
    st.dataframe(matches, hide_index=True, width="stretch",
                 column_config={"score": st.column_config.ProgressColumn("score", min_value=0, max_value=100,
                                                                         format="%d")})
    danfoss_id = st.radio("Compare with", matches["product_id"].tolist(), horizontal=True,
                          format_func=lambda p: p.replace("|", " · "))

    # ---------------- compare
    comp = compare(df, danfoss_id, target)
    st.subheader(f"Danfoss {danfoss_id.split('|')[1]}  vs.  {target.replace('|', ' ')}")

    def colour(v):
        v = str(v)
        if v.startswith("Danfoss better"):
            return "background-color:#d4edda"
        if v.startswith("Competitor better"):
            return "background-color:#f8d7da"
        if v == "missing data":
            return "background-color:#fff3cd"
        return ""

    show = comp[["id", "attribute", "danfoss", "competitor", "verdict", "danfoss_link", "competitor_link",
                 "danfoss_source", "competitor_source", "danfoss_status", "competitor_status"]]
    st.dataframe(show.style.map(colour, subset=["verdict"]), hide_index=True, width="stretch",
                 column_config={"danfoss_link": LINK("Danfoss source", display_text="open page"),
                                "competitor_link": LINK("Competitor source", display_text="open page")})

    with st.expander("🔎 Evidence: exact quotes from the stored documents"):
        for _, r in comp.iterrows():
            dl = f"[{r['danfoss_source']}]({r['danfoss_link']})" if r["danfoss_link"] else (r["danfoss_source"] or "—")
            cl = f"[{r['competitor_source']}]({r['competitor_link']})" if r["competitor_link"] else (r["competitor_source"] or "—")
            st.markdown(f"**{r['id']} {r['attribute']}**  \n"
                        f"Danfoss ({dl}): _{r['danfoss_quote'] or 'not found'}_  \n"
                        f"Competitor ({cl}): _{r['competitor_quote'] or 'not found'}_")

    good = {"ok", "manual", "calculated"}
    flags = comp[~(comp["danfoss_status"].isin(good) & comp["competitor_status"].isin(good))]
    if len(flags):
        st.warning("⚠️ Check before using with the customer: " +
                   ", ".join(f"{r.id} {r.attribute} ({r.danfoss_status}/{r.competitor_status})"
                             for r in flags.itertuples()))

    # ---------------- notes + one-pager
    st.subheader("Sales notes & one-pager")
    key = f"notes_{customer}_{danfoss_id}_{target}"
    if st.button("✍️ Write sales notes (AI, uses only the table above)", disabled=not has_api_key()):
        with st.spinner("Writing…"):
            rows = comp.drop(columns=["danfoss_quote", "competitor_quote", "danfoss_link", "competitor_link"])
            st.session_state[key] = explain(rows.to_dict("records"), ctx)
    if not has_api_key():
        st.caption("Add ANTHROPIC_API_KEY to your .env file to enable AI notes.")
    notes = st.text_area("Notes (edit before exporting)", st.session_state.get(key, ""), height=220)

    pdf = build_onepager(customer, danfoss_id.split("|")[1], target.replace("|", " "), comp, notes, ctx)
    st.download_button("📄 Download one-pager (PDF)", pdf, mime="application/pdf",
                       file_name=f"onepager_{customer or 'customer'}_{danfoss_id.split('|')[1]}.pdf")


# =====================================================================================
#  LIBRARY PAGE
# =====================================================================================
def library_page():
    st.title("📚 Library")
    st.caption("Every document, product value and customer the assistant knows - and where it came from.")

    docs, prods, custs = db.list_documents(), db.get_products(), db.get_customers()
    c1, c2, c3 = st.columns(3)
    c1.metric("Documents", len(docs))
    c2.metric("Products", len(prods))
    c3.metric("Customers", len(custs))

    t_docs, t_search, t_prod, t_cust = st.tabs(["📄 Documents", "🔎 Search all pages", "📦 Products", "👥 Customers"])

    # ---------------- documents
    with t_docs:
        docs["open"] = [db.doc_link(f) for f in docs["file_name"]]
        st.dataframe(docs[["doc_id", "title", "brand", "family", "doc_type", "pages", "products", "open",
                           "source_url", "added_at"]], hide_index=True, width="stretch",
                     column_config={"open": LINK("file", display_text="open"),
                                    "source_url": LINK("original URL")})

        st.markdown("#### ➕ Add a document")
        with st.form("add_doc", clear_on_submit=True):
            up = st.file_uploader("Datasheet / brochure (PDF) or saved text", type=["pdf", "txt", "md", "html"])
            a, b, c = st.columns(3)
            brand = a.text_input("Brand", "Danfoss")
            family = b.selectbox("Family", list(FAMILIES))
            doc_type = c.selectbox("Type", ["datasheet", "brochure", "catalogue", "webshop", "other"])
            url = st.text_input("Original URL (for traceability)")
            products = st.text_input("Products/sizes in this document (comma-separated), e.g. S45 J 45, S45 J 75")
            run_ai = st.checkbox("Run AI extraction now", value=has_api_key(), disabled=not has_api_key())
            if st.form_submit_button("Add to library") and up:
                _add_and_extract(up.getvalue(), up.name, brand, family, doc_type, url, products, run_ai)

        with st.expander("Add a web page (e.g. distributor web shop) by pasting its text"):
            with st.form("add_web", clear_on_submit=True):
                name = st.text_input("Name, e.g. Hydraquip - Rexroth A10VO 71")
                a, b = st.columns(2)
                brand_w = a.text_input("Brand of the product", "")
                family_w = b.selectbox("Family ", list(FAMILIES))
                url_w = st.text_input("URL")
                text_w = st.text_area("Paste the page text", height=150)
                products_w = st.text_input("Products on this page (comma-separated)")
                run_ai_w = st.checkbox("Run AI extraction now ", value=has_api_key(), disabled=not has_api_key())
                if st.form_submit_button("Add web page") and name and text_w:
                    data = f"SOURCE: {url_w}\n\n{text_w}".encode()
                    _add_and_extract(data, f"{name}.txt", brand_w, family_w, "webshop", url_w, products_w, run_ai_w)

        if (docs["doc_type"] == "demo").any():
            if st.button("🗑️ Remove DEMO data"):
                db.remove_demo_data()
                st.rerun()

    # ---------------- search
    with t_search:
        term = st.text_input("Search every stored page", placeholder="e.g. load sensing, 350 bar, SAE B")
        if term:
            res = db.search_pages(term)
            st.caption(f"{len(res)} pages found")
            st.dataframe(res[["title", "page", "snippet", "link"]], hide_index=True, width="stretch",
                         column_config={"link": LINK("open", display_text="open page")})

    # ---------------- products
    with t_prod:
        allv = db.load_attributes()
        if allv.empty:
            st.info("No products yet.")
        else:
            summary = allv.pivot_table(index="product_id", columns="status", values="attribute",
                                       aggfunc="count", fill_value=0).reset_index()
            st.dataframe(summary, hide_index=True, width="stretch")
            pid = st.selectbox("Review / correct a product", sorted(allv["product_id"].unique()))
            rows = allv[allv["product_id"] == pid][["id", "attribute", "value", "unit", "page", "quote",
                                                     "status", "source_file"]].copy()
            rows["value"] = rows["value"].astype("string")
            edited = st.data_editor(rows, hide_index=True, width="stretch", key=f"edit_{pid}",
                                    disabled=["id", "attribute", "source_file"],
                                    column_config={"status": st.column_config.SelectboxColumn(options=STATUS_OPTIONS)})
            st.caption("Corrected a value by hand after checking the PDF? Set status to **manual**.")
            if st.button("💾 Save corrections"):
                db.update_attribute_rows(edited)
                st.success("Saved.")

    # ---------------- customers
    with t_cust:
        st.markdown("#### Customers")
        edited_c = st.data_editor(custs, num_rows="dynamic", hide_index=True, width="stretch", key="cust_edit",
                                  column_config={"type": st.column_config.SelectboxColumn(
                                      options=["OEM", "Distributor", "Other"])})
        if st.button("💾 Save customers"):
            db.save_customers(edited_c)
            st.success("Saved.")
            st.rerun()

        st.markdown("#### What each customer uses / buys")
        st.caption("Link a customer to the competitor product they use today. The Compare page then pre-selects it.")
        cp = db.get_customer_products()
        edited_cp = st.data_editor(
            cp, num_rows="dynamic", hide_index=True, width="stretch", key="cp_edit",
            column_config={
                "customer": st.column_config.SelectboxColumn(options=custs["customer"].tolist()),
                "product_id": st.column_config.SelectboxColumn(options=prods["product_id"].tolist()),
                "relation": st.column_config.SelectboxColumn(options=["uses_competitor", "buys_danfoss"]),
            })
        if st.button("💾 Save customer products"):
            db.save_customer_products(edited_cp)
            st.success("Saved.")


def _add_and_extract(data, file_name, brand, family, doc_type, url, products_text, run_ai):
    doc_id, is_new = db.add_document(data, file_name, brand, family, doc_type, url)
    st.success(f"{'Added' if is_new else 'Already in library'}: {file_name} (doc {doc_id})")
    products = [p.strip() for p in products_text.split(",") if p.strip()]
    if run_ai and products:
        from extract import extract_product
        for p in products:
            with st.spinner(f"AI is reading the document for {p}…"):
                out = extract_product(doc_id, brand, p, family)
            st.write(f"**{brand} {p}**")
            st.dataframe(out[["attribute", "value", "unit", "page", "status"]], hide_index=True)


# =====================================================================================
nav = st.navigation([
    st.Page(compare_page, title="Compare", icon="🔍", url_path="compare", default=True),
    st.Page(library_page, title="Library", icon="📚", url_path="library"),
])
nav.run()
