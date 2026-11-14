"""Bridge from the teammates' converter to the app's facts CSVs. No AI: converter rows are written by hand."""
from comparison_engine.bridge import load_converter, match_products, to_facts
from comparison_engine.repository import load_repository, validate_csv


def row(**kw):
    base = {"manufacturer": "Danfoss", "product_family": "PVG", "product": "PVG 32", "variant": "",
            "attribute": "rated_flow_port_P", "value": "100", "unit": "l/min", "condition": "", "fact_type": "spec",
            "page": 12, "quote": "100 l/min", "uncertain": False, "quote_verified": True,
            "source_file": "BC152886483664en.pdf"}
    return {**base, **kw}


def test_owner_product_gets_the_catalog_id():
    df, rep = to_facts([row(), row(product="PVG 100", value="150", quote="150 l/min")],
                       owner_id="pvg32", owner_name="PVG 32", family="valve", brand="Danfoss")
    assert list(df["product_id"]) == ["pvg32", "Danfoss|PVG 100"]
    assert rep["owner_products"] == ["PVG 32"] and rep["other_products"] == ["PVG 100"]


def test_single_product_file_belongs_to_the_owner_even_with_another_name():
    # the catalog calls it "KMP", the leaflet calls it "KPES6"
    df, _ = to_facts([row(manufacturer="Kawasaki", product="KPES6")], "kawasaki-kmp", "KMP", "valve", "Kawasaki")
    assert df.iloc[0]["product_id"] == "kawasaki-kmp" and df.iloc[0]["brand"] == "Kawasaki"


def test_status_variant_condition_units_and_skips():
    rows = [row(),
            row(quote_verified=False, attribute="max_pressure_port_P", value="350", unit="bar"),
            row(uncertain=True, attribute="weight", value="3.05", unit="kg"),
            row(value="", attribute="price"),
            row(variant="Compensator S", attribute="rated_flow", value="125", condition="without compensator"),
            row(attribute="max_oil_temperature", value="90", unit="degrees C"),
            row(fact_type="ordering_code", attribute="ordering_code_spool", value="A;B;C")]
    df, rep = to_facts(rows, "pvg32", "PVG 32", "valve", "Danfoss")
    by = {r["attribute"]: r for _, r in df.iterrows()}
    assert by["rated_flow_port_p"]["status"] == "ok"
    assert by["max_pressure_port_p"]["status"] == "unverified"          # quote not on the page
    assert by["weight"]["status"] == "unverified"                       # converter was unsure
    assert by["price"]["status"] == "missing" and by["price"]["page"] == ""
    assert by["rated_flow_compensator_s"]["condition"] == "without compensator"
    assert by["max_oil_temperature"]["unit"] == "°C"
    assert rep["skipped_ordering_code"] == 1 and "ordering_code_spool" not in by
    assert validate_csv(df) == []


def test_facts_load_into_the_engine(tmp_path):
    rows = [row(), row(attribute="max_oil_temperature", value="90", unit="degrees C"),
            row(attribute="rated_flow_port_P", value="125", page=54, quote="125 l/min", condition="without compensator")]
    df, _ = to_facts(rows, "pvg32", "PVG 32", "valve", "Danfoss")
    df.to_csv(tmp_path / "pvg32__abc.csv", index=False)
    data = load_repository(tmp_path)
    temp = data[data["attribute"] == "max_oil_temperature"].iloc[0]
    assert temp["norm_value"] == 90 and temp["norm_unit"] == "°C" and temp["status"] == "ok"
    flow = data[data["attribute"] == "rated_flow_port_p"]
    assert set(flow["status"]) == {"ok"}                     # different conditions: NOT a conflict


def test_match_products():
    assert match_products(["PVG 16", "PVG 32", "PVG 32"], "PVG 32") == {"PVG 32"}
    assert match_products(["KPES6"], "KMP") == {"KPES6"}
    assert match_products(["A", "B"], "C") == set()


def test_the_teammates_converter_can_be_imported():
    conv = load_converter()
    assert callable(conv.process_pdf) and conv.parse_pages("1,3-4") == {1, 3, 4}
