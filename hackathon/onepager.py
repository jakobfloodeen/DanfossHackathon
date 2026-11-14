"""
Builds the one-pager PDF for the salesperson (owner: P4).
Uses fpdf2's built-in fonts, so special characters are replaced by safe versions.
"""
from datetime import date

import pandas as pd
from fpdf import FPDF
from fpdf.fonts import FontFace

REPLACE = {"—": "-", "–": "-", "³": "3", "²": "2", "⁻¹": "-1", "≥": ">=", "≤": "<=",
           "’": "'", "‘": "'", "“": '"', "”": '"', "•": "-", "…": "...", "→": "->", "✓": "OK"}


def safe(text) -> str:
    s = "" if text is None or (isinstance(text, float) and pd.isna(text)) else str(text)
    for k, v in REPLACE.items():
        s = s.replace(k, v)
    return s.encode("latin-1", "replace").decode("latin-1")


def build_onepager(customer: str, danfoss_name: str, competitor_name: str,
                   comparison: pd.DataFrame, sales_notes: str = "",
                   customer_context: str = "") -> bytes:
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(True, margin=12)
    pdf.add_page()

    # Header
    pdf.set_fill_color(227, 6, 19)  # Danfoss red
    pdf.rect(0, 0, 210, 4, "F")
    pdf.set_fill_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 9, safe(f"Competitor one-pager: {customer or 'customer'}"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, safe(f"Danfoss {danfoss_name}  vs.  {competitor_name}   |   {date.today():%d %b %Y}"),
             new_x="LMARGIN", new_y="NEXT")
    if customer_context:
        pdf.set_font("Helvetica", "I", 9)
        pdf.multi_cell(0, 5, safe(f"Customer context: {customer_context}"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    # Comparison table
    pdf.set_font("Helvetica", "", 8)
    with pdf.table(col_widths=(8, 30, 30, 30, 32, 60), line_height=4.5, text_align="LEFT",
                   headings_style=FontFace(emphasis="BOLD", fill_color=(235, 235, 235)),
                   first_row_as_headings=True) as table:
        head = table.row()
        for h in ("ID", "Attribute", "Danfoss", "Competitor", "Verdict", "Sources"):
            head.cell(h)
        for _, r in comparison.iterrows():
            row = table.row()
            row.cell(safe(r["id"]))
            row.cell(safe(r["attribute"].replace("_", " ")))
            row.cell(safe(r["danfoss"]) + ("" if r["danfoss_status"] in ("ok", "manual", "calculated", "missing")
                                           else f" [{r['danfoss_status']}]"))
            row.cell(safe(r["competitor"]) + ("" if r["competitor_status"] in ("ok", "manual", "calculated", "missing")
                                              else f" [{r['competitor_status']}]"))
            row.cell(safe(r["verdict"]))
            row.cell(safe(f"D: {r['danfoss_source'] or '-'} | C: {r['competitor_source'] or '-'}"))
    pdf.ln(3)

    # Sales notes (from the AI, cites row ids)
    if sales_notes:
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 6, "Sales notes", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)
        pdf.multi_cell(0, 4.5, safe(sales_notes.replace("**", "").replace("#", "")),
                       new_x="LMARGIN", new_y="NEXT")

    # Footer
    pdf.set_auto_page_break(False)
    pdf.set_y(-18)
    pdf.set_font("Helvetica", "I", 7)
    pdf.multi_cell(0, 3.5, safe("Generated from public datasheets. Every value links to its source page. "
                                "Values marked missing/unverified/conflict must be checked before use with "
                                "the customer. The salesperson makes the final technical and commercial decision."))
    return bytes(pdf.output())
