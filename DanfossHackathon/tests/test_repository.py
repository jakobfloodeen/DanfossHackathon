import pandas as pd

from comparison_engine.repository import (load_repository, products, repository_report, validate_csv,
                                          validate_csv_file)

COMPX = "CompetitorX|DEMO pump 71"


def rows(data, pid, attr):
    return data[(data["product_id"] == pid) & (data["attribute"] == attr)]


def test_broken_csv_is_skipped_and_reported(demo_repo):
    report = repository_report(demo_repo)
    broken = report[report["file"] == "broken_example.csv"].iloc[0]
    assert not broken["loaded"]
    assert "missing columns" in broken["reason"]
    assert report["loaded"].sum() == 3


def test_original_and_normalised_values_are_kept(demo_repo):
    data = load_repository(demo_repo)
    r = rows(data, COMPX, "max_displacement").iloc[0]
    assert r["value"] == "4,33" and r["unit"] == "in3/rev"
    assert abs(r["norm_value"] - 70.956) < 0.01 and r["norm_unit"] == "cc/rev"
    p = rows(data, COMPX, "rated_pressure").iloc[0]
    assert abs(p["norm_value"] - 279.93) < 0.01 and p["norm_unit"] == "bar"


def test_text_values_are_not_numbers(demo_repo):
    data = load_repository(demo_repo)
    r = rows(data, COMPX, "control_options").iloc[0]
    assert r["value"] == "PC, LS" and pd.isna(r["norm_value"])
    r = rows(data, COMPX, "model_code_example").iloc[0]
    assert pd.isna(r["norm_value"])


def test_missing_value_has_status_missing(demo_repo):
    data = load_repository(demo_repo)
    assert rows(data, COMPX, "weight").iloc[0]["status"] == "missing"


def test_conflict_keeps_all_values(demo_repo):
    data = load_repository(demo_repo)
    speed = rows(data, COMPX, "max_speed")
    assert len(speed) == 2
    assert set(speed["status"]) == {"conflict"}
    assert sorted(speed["norm_value"]) == [2200, 2550]
    assert set(speed["source_file"]) == {"DEMO_compx.pdf", "DEMO_webshop.txt"}


def test_calculated_flow_marked_and_not_from_conflicting_inputs(demo_repo):
    data = load_repository(demo_repo)
    flow75 = rows(data, "Danfoss|DEMO pump 75", "max_flow").iloc[0]
    assert flow75["status"] == "calculated" and flow75["norm_value"] == 180.0
    assert flow75["source_file"] == "calculated" and "x" in flow75["quote"]
    assert flow75["based_on"] == ["max_displacement", "max_speed"]
    # CompetitorX speed is disputed -> no calculated flow (we never build on a conflict)
    assert rows(data, COMPX, "max_flow").empty


def test_products(demo_repo):
    p = products(load_repository(demo_repo))
    assert set(p["product_id"]) == {COMPX, "Danfoss|DEMO pump 45", "Danfoss|DEMO pump 75",
                                    "Danfoss|DEMO pump 100"}


def test_auto_reload_when_a_file_is_added(repo_copy):
    before = load_repository(repo_copy)
    (repo_copy / "new.csv").write_text(
        "product_id,brand,product_name,family,attribute,value,unit,source_file,page,quote,status\n"
        ",Danfoss,DEMO pump 60,Axial Piston Pump,max_displacement,60,cc,new.pdf,2,DEMO 60 cc,ok\n")
    after = load_repository(repo_copy)
    assert len(after) == len(before) + 1
    new = after[after["product_id"] == "Danfoss|DEMO pump 60"].iloc[0]
    assert new["family"] == "pump"                       # alias from rules/_general.md


def test_ok_without_quote_is_downgraded(repo_copy):
    (repo_copy / "noquote.csv").write_text(
        "brand,product_name,family,attribute,value,unit,source_file,page,quote,status\n"
        "Danfoss,DEMO pump 60,pump,max_speed,2500,rpm,x.pdf,,,ok\n")
    data = load_repository(repo_copy)
    assert rows(data, "Danfoss|DEMO pump 60", "max_speed").iloc[0]["status"] == "unverified"


def test_unknown_unit_status(repo_copy):
    (repo_copy / "odd.csv").write_text(
        "brand,product_name,family,attribute,value,unit,source_file,page,quote,status\n"
        "Danfoss,DEMO pump 60,pump,length,12,furlong,x.pdf,1,DEMO 12 furlong,ok\n")
    data = load_repository(repo_copy)
    r = rows(data, "Danfoss|DEMO pump 60", "length").iloc[0]
    assert r["status"] == "unknown unit" and r["value"] == "12"


def test_validate_csv_good_file(demo_repo):
    assert validate_csv_file(demo_repo / "danfoss_DEMO_pumps.csv") == []


def test_validate_csv_finds_problems():
    df = pd.DataFrame([
        {"brand": "X", "product_name": "P", "family": "pump", "attribute": "a", "value": "1", "unit": "bar",
         "source_file": "f.pdf", "page": "", "quote": "", "status": "ok"},
        {"brand": "X", "product_name": "P", "family": "pump", "attribute": "b", "value": "1", "unit": "parsec",
         "source_file": "f.pdf", "page": "x", "quote": "q", "status": "great"},
    ])
    problems = validate_csv(df)
    text = " | ".join(problems)
    assert "needs a page and a quote" in text
    assert "unknown unit 'parsec'" in text
    assert "status 'great'" in text
    assert "page 'x' is not a number" in text


def test_validate_csv_missing_columns():
    assert validate_csv(pd.DataFrame({"name": ["x"]}))[0].startswith("missing columns")


def test_semicolon_csv_is_read(repo_copy):
    (repo_copy / "excel.csv").write_text(
        "brand;product_name;family;attribute;value;unit;source_file;page;quote;status\n"
        "Danfoss;DEMO pump 60;pump;max_speed;2500;rpm;x.pdf;1;DEMO 2500;ok\n")
    report = repository_report(repo_copy)
    assert report[report["file"] == "excel.csv"].iloc[0]["loaded"]


def test_two_pages_of_one_file_can_conflict(repo_copy):
    (repo_copy / "pages.csv").write_text(
        "brand,product_name,family,attribute,value,unit,source_file,page,quote,status\n"
        "Danfoss,DEMO valve,valves,rated_flow,100,l/min,doc.pdf,12,DEMO 100,ok\n"
        "Danfoss,DEMO valve,valves,rated_flow,125,l/min,doc.pdf,54,DEMO 125,ok\n")
    data = load_repository(repo_copy)
    assert set(rows(data, "Danfoss|DEMO valve", "rated_flow")["status"]) == {"conflict"}
