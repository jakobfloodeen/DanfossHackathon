"""
Danfoss VPS Competitor Intelligence Assistant
Step 1: upload product datasheets, store them on disk, and extract text per page
so every fact can later be traced back to (file, page).
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import pdfplumber
import streamlit as st

# ---------- Storage locations ----------
DATA_DIR = Path("data")
UPLOAD_DIR = DATA_DIR / "uploads"        # original PDFs
EXTRACT_DIR = DATA_DIR / "extracted"     # page-by-page text (JSON)
LIBRARY_FILE = DATA_DIR / "library.json" # metadata about every document

for folder in (UPLOAD_DIR, EXTRACT_DIR):
    folder.mkdir(parents=True, exist_ok=True)


# ---------- Library helpers ----------
def load_library() -> list[dict]:
    if LIBRARY_FILE.exists():
        return json.loads(LIBRARY_FILE.read_text(encoding="utf-8"))
    return []


def save_library(library: list[dict]) -> None:
    LIBRARY_FILE.write_text(json.dumps(library, indent=2, ensure_ascii=False), encoding="utf-8")


def extract_pages(pdf_path: Path) -> list[dict]:
    """Return [{'page': 1, 'text': '...', 'tables': [...]}, ...]"""
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            pages.append({
                "page": i,
                "text": page.extract_text() or "",
                "tables": page.extract_tables() or [],
            })
    return pages


def store_document(uploaded_file, manufacturer: str, product: str, is_danfoss: bool) -> tuple[bool, str]:
    data = uploaded_file.getvalue()
    doc_id = hashlib.sha256(data).hexdigest()[:16]  # same file twice -> same id

    library = load_library()
    if any(d["id"] == doc_id for d in library):
        return False, f"'{uploaded_file.name}' is already in the library."

    # 1) Save the original PDF
    stored_name = f"{doc_id}_{uploaded_file.name}"
    pdf_path = UPLOAD_DIR / stored_name
    pdf_path.write_bytes(data)

    # 2) Extract text page by page (keeps page numbers for source links)
    pages = extract_pages(pdf_path)
    (EXTRACT_DIR / f"{doc_id}.json").write_text(
        json.dumps(pages, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # 3) Record metadata
    library.append({
        "id": doc_id,
        "filename": uploaded_file.name,
        "stored_as": stored_name,
        "manufacturer": manufacturer,
        "product": product,
        "source_type": "Danfoss" if is_danfoss else "Competitor",
        "pages": len(pages),
        "uploaded_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    })
    save_library(library)
    return True, f"Stored '{uploaded_file.name}' ({len(pages)} pages)."


def delete_document(doc_id: str) -> None:
    library = load_library()
    doc = next((d for d in library if d["id"] == doc_id), None)
    if doc:
        (UPLOAD_DIR / doc["stored_as"]).unlink(missing_ok=True)
        (EXTRACT_DIR / f"{doc_id}.json").unlink(missing_ok=True)
        save_library([d for d in library if d["id"] != doc_id])


# ---------- UI ----------
st.set_page_config(page_title="Competitor Intelligence Assistant", layout="wide")
st.title("Danfoss VPS Competitor Intelligence Assistant")

with st.sidebar:
    st.header("📄 Upload datasheets")
    with st.form("upload_form", clear_on_submit=True):
        files = st.file_uploader("PDF files", type=["pdf"], accept_multiple_files=True)
        manufacturer = st.text_input("Manufacturer", placeholder="e.g. Danfoss, Bosch Rexroth, Parker")
        product = st.text_input("Product / series", placeholder="e.g. PVG 32")
        is_danfoss = st.checkbox("This is a Danfoss product")
        submitted = st.form_submit_button("Upload & store")

    if submitted:
        if not files:
            st.warning("Choose at least one PDF.")
        elif not manufacturer or not product:
            st.warning("Fill in manufacturer and product.")
        else:
            for f in files:
                with st.spinner(f"Processing {f.name}..."):
                    ok, msg = store_document(f, manufacturer.strip(), product.strip(), is_danfoss)
                (st.success if ok else st.info)(msg)

# ---------- Library view ----------
st.subheader("📚 Document library")
library = load_library()

if not library:
    st.info("No documents yet. Upload a datasheet in the sidebar.")
else:
    df = pd.DataFrame(library)[["filename", "manufacturer", "product", "source_type", "pages", "uploaded_at"]]
    st.dataframe(df, use_container_width=True, hide_index=True)

    for doc in library:
        with st.expander(f"{doc['source_type']} · {doc['manufacturer']} {doc['product']} · {doc['filename']}"):
            col1, col2 = st.columns([1, 1])
            pdf_bytes = (UPLOAD_DIR / doc["stored_as"]).read_bytes()
            col1.download_button("⬇️ Download PDF", pdf_bytes, file_name=doc["filename"],
                                 mime="application/pdf", key=f"dl_{doc['id']}")
            if col2.button("🗑️ Delete", key=f"del_{doc['id']}"):
                delete_document(doc["id"])
                st.rerun()

            pages = json.loads((EXTRACT_DIR / f"{doc['id']}.json").read_text(encoding="utf-8"))
            page_no = st.number_input("Preview page", 1, len(pages), 1, key=f"pg_{doc['id']}")
            p = pages[page_no - 1]
            st.text_area("Extracted text", p["text"], height=250, key=f"txt_{doc['id']}_{page_no}")
            for t_idx, table in enumerate(p["tables"], start=1):
                if table:
                    st.caption(f"Table {t_idx}")
                    st.dataframe(pd.DataFrame(table), hide_index=True)