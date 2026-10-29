"""Category picking, spreadsheets for each site, and upload order."""
import csv
import os

from helpers import make_photo


def test_category_codes_are_all_real(stockup):
    dt = stockup.DREAMSTIME_CODES
    for words, ss, adobe, dtc, dted in stockup.TOPICS:
        assert ss in stockup.SHUTTERSTOCK_CATEGORIES
        assert adobe in stockup.ADOBE_CATEGORIES
        assert dtc in dt and dted in dt


def test_categories_from_titles_and_keywords(stockup):
    s = stockup.suggest_categories
    r = s("Golden retriever puppy playing in autumn leaves", ["dog", "puppy", "autumn", "pet"])
    assert r["ss"][0] == "Animals/Wildlife" and r["adobe"] == 1 and r["dt"][0] == 30
    r = s("Sailboats anchored on a calm bay at sunset", ["sailboat", "sunset", "sea", "harbor"])
    assert r["ss"][0] == "Nature" and r["adobe"] == 11 and 19 in r["dt"]
    r = s("Colorful noise texture background", ["noise", "texture", "background", "digital"])
    assert r["ss"] == ["Backgrounds/Textures"] and 111 not in r["dt"]
    assert s("Untitled", ["img"]) == {"ss": [], "adobe": None, "dt": [], "why": []}
    r = s("Runners at the city marathon", ["marathon", "runner", "race"], editorial=True)
    assert r["dt"][0] in range(178, 186)  # a Dreamstime editorial category comes first


def test_your_category_choices_are_never_overwritten(stockup, batch_dir):
    make_photo(batch_dir, "a.jpg")
    b = stockup.Batch(batch_dir)
    st = b.state["a.jpg"]
    assert st["ss_cats"] and set(st["auto_cats"]) == {"ss", "adobe", "dt"}
    b.save({"a.jpg": dict(st, ss_cats=["Vintage"])})
    assert b.state["a.jpg"]["auto_cats"] == ["adobe", "dt"]
    b2 = stockup.Batch(batch_dir)
    assert b2.state["a.jpg"]["ss_cats"] == ["Vintage"]  # still yours after reopening
    b2.autopick(["a.jpg"])  # unless you press Auto-pick for that photo
    assert b2.state["a.jpg"]["ss_cats"] != ["Vintage"]


def test_auto_categories_can_be_turned_off(stockup, batch_dir):
    stockup.save_options({"auto_categories": False})
    make_photo(batch_dir, "a.jpg")
    b = stockup.Batch(batch_dir)
    assert b.state["a.jpg"]["ss_cats"] == [] and b.state["a.jpg"]["auto_cats"] == []


def read(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def test_spreadsheets_for_each_site(stockup, batch_dir):
    make_photo(batch_dir, "news.jpg", title="Regatta, Camden harbor", desc="Boats leave the harbor for the regatta")
    make_photo(batch_dir, "calm.jpg", seed=1)
    stockup.save_options({"my_sites": ["shutterstock", "adobe", "dreamstime", "depositphotos",
                                       "123rf", "pond5", "istock", "alamy"]})
    b = stockup.Batch(batch_dir)
    b.state["news.jpg"].update(editorial=True, ed_city="Camden", ed_region="Maine", ed_date="2026-07-04")
    r = stockup.build_outputs(b)
    out = r["folder"]
    ss = {row["Filename"]: row for row in read(f"{out}/shutterstock/shutterstock.csv")}
    assert ss["news.jpg"]["Editorial"] == "Yes" and ss["calm.jpg"]["Editorial"] == "No"
    assert ss["news.jpg"]["Description"].startswith("CAMDEN, MAINE - JULY 4, 2026: ")
    assert ss["calm.jpg"]["Categories"]
    adobe_csv = next(s for s in r["sites"] if s["site"] == "adobe")["files"][0]
    ad = {row["Filename"]: row for row in read(f"{out}/adobe/{adobe_csv}")}
    assert "," not in ad["news.jpg"]["Title"]
    dt = {row["Filename"]: row for row in read(f"{out}/dreamstime/dreamstime.csv")}
    assert list(read(f"{out}/dreamstime/dreamstime.csv")[0].keys())[:7] == [
        "Filename", "Image Name", "Description", "Category 1", "Category 2", "Category 3", "keywords"]
    assert dt["news.jpg"]["Editorial"] == "1" and dt["calm.jpg"]["Editorial"] == "0"
    assert int(dt["calm.jpg"]["Category 1"]) in stockup.DREAMSTIME_CODES
    dp = {row["Filename"]: row for row in read(f"{out}/depositphotos/depositphotos.csv")}
    assert dp["news.jpg"]["Editorial"] == "yes"
    p5 = {row["originalfilename"]: row for row in read(f"{out}/pond5/pond5.csv")}
    assert p5["news.jpg"]["editorial"] == "yes"
    assert os.path.exists(f"{out}/istock/editorial/news.jpg")
    assert os.path.exists(f"{out}/istock/creative/calm.jpg")
    assert open(f"{out}/alamy/editorial_list.txt").read().split() == ["news.jpg"]


def test_shuffled_order_keeps_look_alikes_apart(stockup):
    looks, files = {}, []
    for g, h in {"A": 0xFFFF0000FFFF0000, "B": 0x0F0F0F0F0F0F0F0F, "C": 0x00FF00FF00FF00FF}.items():
        for i in range(5):
            files.append(f"{g}{i}.jpg")
            looks[f"{g}{i}.jpg"] = "%016x" % (h ^ (1 << i))
    for seed in ("one", "two", "three"):
        order = stockup.spread_order(files, looks, seed)
        assert sorted(order) == sorted(files)
        assert all(a[0] != b[0] for a, b in zip(order, order[1:])), order
    assert stockup.spread_order(files, looks, "x") != stockup.spread_order(files, looks, "y")


def test_shuffled_order_without_picture_hashes_splits_series(stockup):
    files = [f"IMG_{n:04d}.jpg" for n in (1, 2, 3, 4, 10, 11, 12, 50)]
    order = stockup.spread_order(files, {}, "z")
    nums = [int(f[4:8]) for f in order]
    assert sum(1 for a, b in zip(nums, nums[1:]) if abs(a - b) <= 3) <= 1
