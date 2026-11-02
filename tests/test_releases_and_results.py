"""Model/property releases, and importing the sites' own reports (rejections, sales)."""
import csv
import os

from helpers import make_photo


def plan_texts(b, f, site):
    return " ".join(i["text"] for i in b.plan(f, site)["issues"])


def test_people_and_property_are_spotted(stockup):
    n = stockup.release_needs({"title": "Smiling woman in her kitchen", "description": "",
                               "keywords": ["portrait", "interior"]})
    assert "woman" in n["model"] and "portrait" in n["model"]
    assert "kitchen" in n["property"] and "interior" in n["property"]
    assert stockup.release_needs({"title": "Harbor at sunset", "description": "", "keywords": ["sea"]}) \
        == {"model": [], "property": []}
    assert stockup.clean_releases("Jane MR, House PR, jane mr ,") == ["Jane MR", "House PR"]


def test_release_warning_and_spreadsheet_column(stockup, batch_dir):
    make_photo(batch_dir, "a.jpg", title="Woman reading a book in a park",
               keywords=["woman", "reading", "book", "park", "relax", "summer", "outdoors", "leisure"])
    b = stockup.Batch(batch_dir)
    assert "model release" in plan_texts(b, "a.jpg", "adobe")
    b.save({"a.jpg": dict(b.state["a.jpg"], releases=["Anna MR"])})
    assert "model release" not in plan_texts(b, "a.jpg", "adobe")
    assert "Anna MR" in plan_texts(b, "a.jpg", "shutterstock")      # attach it on the site
    stockup.save_options({"my_sites": ["adobe"]})
    out = stockup.build_outputs(b)
    sheet = [x for x in os.listdir(os.path.join(out["folder"], "adobe")) if x.endswith(".csv")][0]
    with open(os.path.join(out["folder"], "adobe", sheet), encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["Releases"] == "Anna MR"
    b2 = stockup.Batch(batch_dir)                                    # kept after reopening
    assert b2.state["a.jpg"]["releases"] == ["Anna MR"]


def test_no_warning_when_editorial_or_not_needed(stockup, batch_dir):
    make_photo(batch_dir, "a.jpg", title="Crowd at the city marathon",
               keywords=["crowd", "marathon", "runner", "race", "city", "street", "sport", "event"])
    b = stockup.Batch(batch_dir)
    b.save({"a.jpg": dict(b.state["a.jpg"], editorial=True)})
    assert "release" not in plan_texts(b, "a.jpg", "shutterstock")
    b.save({"a.jpg": dict(b.state["a.jpg"], editorial=False, no_release=True)})
    assert "release" not in plan_texts(b, "a.jpg", "shutterstock")


def write(path, header, rows):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    return path


def test_import_report_matches_uploads(stockup, batch_dir, tmp_path):
    for i, kws in enumerate((["harbor", "boat", "sea"], ["harbor", "boat", "sunset"], ["harbor", "dog", "beach"])):
        make_photo(batch_dir, f"p{i}.jpg", title=f"Photo {i}", keywords=kws)
        stockup.record_history("adobe", batch_dir, f"p{i}.jpg", f"sha{i}", "")
    rep = write(str(tmp_path / "adobe_stock_report.csv"), ["Original Filename", "Status", "Reason"],
                [["p0.jpg", "Approved", ""], ["P1.JPG", "Rejected", "Quality issues"],
                 ["p2.jpg", "Rejected", "Quality issues"], ["old.jpg", "Approved", ""]])
    r = stockup.import_report(rep)
    assert r["site"] == "adobe" and r["photos"] == 4 and r["matched"] == 3
    assert stockup.import_report(rep)["already"]                     # importing twice changes nothing
    s = stockup.results_summary(min_photos=2)["sites"][0]
    assert (s["accepted"], s["rejected"], s["unmatched"]) == (2, 2, 1)
    assert s["reasons"][0] == ("Quality issues", 2)
    harbor = next(k for k in s["rejected_most"] if k["keyword"] == "harbor")
    assert harbor["photos"] == 3 and abs(harbor["rate"] - 1 / 3) < 0.01


def test_sales_report_one_row_per_sale(stockup, batch_dir, tmp_path):
    make_photo(batch_dir, "a.jpg", title="A", keywords=["x"])
    stockup.record_history("shutterstock", batch_dir, "a.jpg", "sha", "")
    rep = write(str(tmp_path / "earnings.csv"), ["Date", "File name", "Earnings"],
                [["2026-10-01", "a.jpg", "$0.38"], ["2026-10-02", "a.jpg", "$1,20"]])
    r = stockup.import_report(rep, "shutterstock")
    assert r["matched"] == 1
    s = stockup.results_summary()["sites"][0]
    assert s["sales"] == 2 and abs(s["earn"] - 1.58) < 0.001


def test_report_without_a_file_column_says_why(stockup, tmp_path):
    import pytest
    rep = write(str(tmp_path / "pond5.csv"), ["Date", "Amount"], [["2026-10-01", "3"]])
    with pytest.raises(ValueError, match="file name column"):
        stockup.import_report(rep)
