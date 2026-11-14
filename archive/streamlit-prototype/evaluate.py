"""
Accuracy check: AI-extracted values vs. the hand-checked golden.csv (owner: P1).
Gives you the pitch number: "X of Y attributes correct".

Usage:  python evaluate.py
"""
import pandas as pd

import db
from compare import normalize
from schema import attributes

gold = pd.read_csv("golden.csv").dropna(subset=["true_value"])
extracted = db.load_attributes()
extracted = extracted[extracted["doc_type"] != "demo"]

correct = total = 0
for _, g in gold.iterrows():
    total += 1
    e = extracted[(extracted["product_id"] == g["product_id"]) & (extracted["attribute"] == g["attribute"])]
    if e.empty:
        print(f"MISSING  {g['product_id']} {g['attribute']}")
        continue
    e = e.iloc[0]
    family = e["family"]
    unit = attributes(family)[g["attribute"]]["unit"]
    if unit:  # numeric: compare after unit conversion, 1% tolerance
        t, x = normalize(g["true_value"], g["unit"], unit), normalize(e["value"], e["unit"], unit)
        ok = t is not None and x is not None and abs(t - x) <= 0.01 * abs(t)
    else:     # text: simple contains check
        ok = str(g["true_value"]).strip().lower() in str(e["value"]).strip().lower()
    correct += ok
    print(f"{'OK   ' if ok else 'WRONG'}    {g['product_id']} {g['attribute']}: true={g['true_value']} ai={e['value']} ({e['status']})")

print(f"\nAccuracy: {correct}/{total} = {correct / max(total, 1):.0%}")
