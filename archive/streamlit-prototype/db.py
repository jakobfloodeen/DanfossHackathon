"""
THE LIBRARY - one place that keeps every document, page, product value and customer.

- Document files (PDFs) are kept in   static/docs/      -> the app can open them at the cited page
- Everything else lives in            data/library.db   -> SQLite: a database in one file, built into Python

Add things through the app's "Library" page, or from code with the functions below.
Tables (= the data model, put this on the architecture slide):

  documents          one row per stored file (datasheet, brochure, web-shop page, ...)
  pages              the text of every page        -> quote checks + full-text search
  products           one row per product (Danfoss or competitor)
  attributes         one row per product value, linked to document + page + quote
  customers          customer master data
  customer_products  which products a customer uses today / buys from Danfoss
"""
import hashlib
import re
import sqlite3
from pathlib import Path
from urllib.parse import quote as urlquote

import pandas as pd

ROOT = Path(__file__).parent
DB_PATH = ROOT / "data" / "library.db"
DOCS_DIR = ROOT / "static" / "docs"          # served by Streamlit at /app/static/docs/<file>

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name   TEXT UNIQUE,
    sha256      TEXT UNIQUE,          -- same file uploaded twice is stored once
    brand       TEXT,
    family      TEXT,
    doc_type    TEXT,                 -- datasheet / brochure / webshop / catalogue / demo
    title       TEXT,
    source_url  TEXT,
    added_at    TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS pages (
    doc_id INTEGER, page INTEGER, text TEXT,
    PRIMARY KEY (doc_id, page)
);
CREATE TABLE IF NOT EXISTS products (
    product_id TEXT PRIMARY KEY,      -- "Brand|Product name"
    brand TEXT, product_name TEXT, family TEXT
);
CREATE TABLE IF NOT EXISTS attributes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id TEXT, attribute TEXT, value TEXT, unit TEXT,
    doc_id INTEGER, page INTEGER, quote TEXT,
    status TEXT,                      -- ok / unverified / missing / manual
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS customers (
    customer TEXT PRIMARY KEY, type TEXT, homepage TEXT,
    applications TEXT, notes TEXT, source TEXT
);
CREATE TABLE IF NOT EXISTS customer_products (
    customer TEXT, product_id TEXT,
    relation TEXT,                    -- uses_competitor / buys_danfoss
    note TEXT, source TEXT,
    PRIMARY KEY (customer, product_id, relation)
);
"""

ATTR_COLS = ["product_id", "attribute", "value", "unit", "doc_id", "page", "quote", "status"]


def _none(v):
    """pandas NaN -> None, so SQLite stores NULL."""
    return None if v is None or (isinstance(v, float) and pd.isna(v)) else v


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    _seed(con)
    return con


def query(sql: str, params=()) -> pd.DataFrame:
    with connect() as con:
        return pd.read_sql_query(sql, con, params=params)


# ---------------------------------------------------------------- first-run seed
def _seed(con):
    """On the very first run, load the starter CSVs (customer list + DEMO products)."""
    if con.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 0:
        f = ROOT / "data" / "customers.csv"
        if f.exists():
            pd.read_csv(f).to_sql("customers", con, if_exists="append", index=False)

    if con.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
        f = ROOT / "data" / "products.csv"
        if f.exists():
            demo = pd.read_csv(f)
            doc_ids = {}
            for name, g in demo.groupby("source_file"):
                cur = con.execute(
                    "INSERT INTO documents (file_name, brand, family, doc_type, title) VALUES (?,?,?,?,?)",
                    (name, "various", g["family"].iloc[0], "demo", f"DEMO values ({name}) - made up"))
                doc_ids[name] = cur.lastrowid
            for _, r in demo.drop_duplicates("product_id").iterrows():
                con.execute("INSERT OR IGNORE INTO products VALUES (?,?,?,?)",
                            (r["product_id"], r["brand"], r["product_name"], r["family"]))
            for _, r in demo.iterrows():
                con.execute(f"INSERT INTO attributes ({','.join(ATTR_COLS)}) VALUES (?,?,?,?,?,?,?,?)",
                            (r["product_id"], r["attribute"], _none(r["value"]), _none(r["unit"]),
                             doc_ids[r["source_file"]], _none(r["page"]), _none(r["quote"]), r["status"]))
    con.commit()


# ---------------------------------------------------------------- documents
def add_document(data: bytes, file_name: str, brand: str, family: str,
                 doc_type: str = "datasheet", source_url: str = "", title: str = "") -> tuple[int, bool]:
    """
    Store a file in the library and index its pages. Returns (doc_id, is_new).
    PDFs are split into pages; .txt/.md/.html (e.g. a copied web-shop page) become one page.
    """
    sha = hashlib.sha256(data).hexdigest()
    with connect() as con:
        row = con.execute("SELECT doc_id FROM documents WHERE sha256=?", (sha,)).fetchone()
        if row:
            return row[0], False

        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", file_name)
        if (DOCS_DIR / safe).exists():
            safe = f"{sha[:8]}_{safe}"
        (DOCS_DIR / safe).write_bytes(data)

        if safe.lower().endswith(".pdf"):
            import pymupdf
            pages = [p.get_text() for p in pymupdf.open(stream=data, filetype="pdf")]
        else:
            text = data.decode("utf-8", errors="replace")
            pages = [re.sub(r"<[^>]+>", " ", text)]   # crude HTML -> text

        cur = con.execute(
            "INSERT INTO documents (file_name, sha256, brand, family, doc_type, title, source_url) "
            "VALUES (?,?,?,?,?,?,?)", (safe, sha, brand, family, doc_type, title or file_name, source_url))
        doc_id = cur.lastrowid
        con.executemany("INSERT INTO pages VALUES (?,?,?)",
                        [(doc_id, i + 1, t) for i, t in enumerate(pages)])
        return doc_id, True


def add_text_document(text: str, name: str, brand: str, family: str,
                      doc_type: str = "webshop", source_url: str = "") -> tuple[int, bool]:
    """For web pages: paste the text + URL instead of scraping."""
    fname = name if name.endswith(".txt") else f"{name}.txt"
    return add_document(f"SOURCE: {source_url}\n\n{text}".encode(), fname, brand, family, doc_type, source_url, name)


def get_pages(doc_id: int) -> dict[int, str]:
    df = query("SELECT page, text FROM pages WHERE doc_id=? ORDER BY page", (doc_id,))
    return dict(zip(df["page"], df["text"]))


def list_documents() -> pd.DataFrame:
    return query("""
        SELECT d.doc_id, d.title, d.brand, d.family, d.doc_type, d.file_name, d.source_url, d.added_at,
               (SELECT COUNT(*) FROM pages p WHERE p.doc_id = d.doc_id) AS pages,
               (SELECT GROUP_CONCAT(DISTINCT a.product_id) FROM attributes a WHERE a.doc_id = d.doc_id) AS products
        FROM documents d ORDER BY d.added_at DESC, d.doc_id DESC""")


def search_pages(term: str, limit: int = 30) -> pd.DataFrame:
    """Full-text search over every stored page. Returns file, page and a snippet."""
    df = query("""SELECT d.doc_id, d.title, d.file_name, d.doc_type, p.page, p.text
                  FROM pages p JOIN documents d USING (doc_id)
                  WHERE p.text LIKE ? LIMIT ?""", (f"%{term}%", limit))

    def snippet(t):
        i = t.lower().find(term.lower())
        return "…" + re.sub(r"\s+", " ", t[max(0, i - 80): i + 120]) + "…"

    df["snippet"] = df["text"].map(snippet)
    df["link"] = [doc_link(f, p) for f, p in zip(df["file_name"], df["page"])]
    return df.drop(columns=["text"])


def doc_link(file_name, page=None):
    """URL that opens the stored file at the right page (None for DEMO rows)."""
    if not file_name or not (DOCS_DIR / str(file_name)).exists():
        return None
    url = f"/app/static/docs/{urlquote(str(file_name))}"
    return url + (f"#page={int(page)}" if _none(page) is not None and str(file_name).lower().endswith(".pdf") else "")


# ---------------------------------------------------------------- products & attributes
def save_attributes(df: pd.DataFrame, brand: str, product_name: str, family: str, doc_id: int):
    """Store extracted values for one product from one document (replaces an earlier run)."""
    pid = f"{brand}|{product_name}"
    with connect() as con:
        con.execute("INSERT OR IGNORE INTO products VALUES (?,?,?,?)", (pid, brand, product_name, family))
        con.execute("DELETE FROM attributes WHERE product_id=? AND doc_id=?", (pid, doc_id))
        con.executemany(f"INSERT INTO attributes ({','.join(ATTR_COLS)}) VALUES (?,?,?,?,?,?,?,?)",
                        [(pid, r["attribute"], _none(r["value"]), _none(r["unit"]), doc_id,
                          _none(r["page"]), _none(r["quote"]), r["status"]) for _, r in df.iterrows()])
    return pid


def already_extracted(product_id: str, doc_id: int) -> bool:
    return not query("SELECT 1 FROM attributes WHERE product_id=? AND doc_id=? LIMIT 1",
                     (product_id, doc_id)).empty


def load_attributes() -> pd.DataFrame:
    """Everything the comparison needs, one row per product value."""
    return query("""
        SELECT a.id, a.product_id, p.brand, p.product_name, p.family, a.attribute, a.value, a.unit,
               a.doc_id, d.file_name AS source_file, d.source_url AS url, d.doc_type,
               a.page, a.quote, a.status
        FROM attributes a
        JOIN products p USING (product_id)
        LEFT JOIN documents d USING (doc_id)""")


def update_attribute_rows(edited: pd.DataFrame):
    """Save manual corrections from the Library page (rows identified by id)."""
    with connect() as con:
        for _, r in edited.iterrows():
            con.execute("UPDATE attributes SET value=?, unit=?, page=?, quote=?, status=?, "
                        "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (_none(r["value"]), _none(r["unit"]), _none(r["page"]), _none(r["quote"]),
                         r["status"], int(r["id"])))


def remove_demo_data():
    with connect() as con:
        con.execute("DELETE FROM attributes WHERE doc_id IN (SELECT doc_id FROM documents WHERE doc_type='demo')")
        con.execute("DELETE FROM documents WHERE doc_type='demo'")
        con.execute("DELETE FROM products WHERE product_id NOT IN (SELECT DISTINCT product_id FROM attributes)")


# ---------------------------------------------------------------- customers
def get_customers() -> pd.DataFrame:
    return query("SELECT customer, type, homepage, applications, notes, source FROM customers ORDER BY customer")


def save_customers(df: pd.DataFrame):
    df = df.dropna(subset=["customer"])
    df = df[df["customer"].astype(str).str.strip() != ""].drop_duplicates("customer")
    with connect() as con:
        con.execute("DELETE FROM customers")
        df.to_sql("customers", con, if_exists="append", index=False)


def get_customer_products(customer: str | None = None) -> pd.DataFrame:
    if customer:
        return query("SELECT * FROM customer_products WHERE customer=?", (customer,))
    return query("SELECT * FROM customer_products ORDER BY customer")


def save_customer_products(df: pd.DataFrame):
    df = df.dropna(subset=["customer", "product_id", "relation"]).drop_duplicates(
        ["customer", "product_id", "relation"])
    with connect() as con:
        con.execute("DELETE FROM customer_products")
        df.to_sql("customer_products", con, if_exists="append", index=False)


def get_products() -> pd.DataFrame:
    return query("SELECT * FROM products ORDER BY brand, product_name")
