"""Per-site checks, editorial, duplicates and photos sent before."""
import shutil

from helpers import ADOBE_ICC, make_photo


def statuses(b, f, sites):
    return {s: b.plan(f, s)["status"] for s in sites}


def test_too_small_is_skipped_only_where_its_too_small(stockup, batch_dir):
    make_photo(batch_dir, "small.jpg", size=(1800, 1200))  # 2.2 MP
    b = stockup.Batch(batch_dir)
    st = statuses(b, "small.jpg", ["shutterstock", "dreamstime", "pond5", "istock"])
    assert st["shutterstock"] == st["dreamstime"] == "skip"
    assert st["pond5"] != "skip" and st["istock"] != "skip"
    b.act(["small.jpg"], "shutterstock", "*", "include")  # Send anyway
    assert b.plan("small.jpg", "shutterstock")["include"]


def test_dont_send_skips_one_site(stockup, batch_dir):
    make_photo(batch_dir, "a.jpg")
    b = stockup.Batch(batch_dir)
    b.act(["a.jpg"], "adobe", "*", "skip")
    assert b.plan("a.jpg", "adobe")["status"] == "skip"
    assert b.plan("a.jpg", "shutterstock")["include"]


def test_colour_profile_checks_follow_each_sites_rules(stockup, batch_dir):
    make_photo(batch_dir, "a.jpg", icc=ADOBE_ICC)
    b = stockup.Batch(batch_dir)
    fixes = lambda s: [i["fix"] for i in b.plan("a.jpg", s)["issues"]]
    assert "srgb" in fixes("shutterstock")
    assert "srgb" not in fixes("pond5")  # Pond5 accepts Adobe RGB


def test_dreamstime_low_quality_note(stockup, batch_dir):
    make_photo(batch_dir, "low.jpg", quality=70)
    make_photo(batch_dir, "high.jpg", quality=95, seed=1)
    b = stockup.Batch(batch_dir)
    has = lambda f: any("JPEG quality" in i["text"] for i in b.plan(f, "dreamstime")["issues"])
    assert has("low.jpg") and not has("high.jpg")


def test_editorial_is_marked_automatically(stockup, batch_dir):
    make_photo(batch_dir, "kw.jpg", keywords=["street", "editorial", "crowd"])
    make_photo(batch_dir, "cap.jpg", seed=1, desc="CAMDEN, MAINE - JULY 4, 2026: Boats leave the harbor")
    make_photo(batch_dir, "plain.jpg", seed=2)
    b = stockup.Batch(batch_dir)
    assert b.state["kw.jpg"]["editorial"] and b.state["cap.jpg"]["editorial"]
    assert not b.state["plain.jpg"]["editorial"]
    assert "editorial" not in b.plan("kw.jpg", "shutterstock")["keywords"]  # marker left out


def test_editorial_captions(stockup, batch_dir):
    make_photo(batch_dir, "new.jpg", desc="Boats leave the harbor for the regatta")
    make_photo(batch_dir, "ready.jpg", seed=1, desc="CAMDEN, MAINE - JULY 4, 2026: Boats leave the harbor")
    b = stockup.Batch(batch_dir)
    for f in b.files:
        b.state[f].update(editorial=True, ed_city="Camden", ed_region="Maine", ed_date="2026-07-04")
    assert b.plan("new.jpg", "shutterstock")["desc"] == \
        "CAMDEN, MAINE - JULY 4, 2026: Boats leave the harbor for the regatta"
    assert b.plan("new.jpg", "pond5")["desc"].startswith("CAMDEN, MAINE - 2026/07/04: ")
    assert b.plan("ready.jpg", "shutterstock")["desc"] == \
        "CAMDEN, MAINE - JULY 4, 2026: Boats leave the harbor"  # kept exactly as written
    stockup.save_options({"caption_mode": "keep"})
    assert b.plan("new.jpg", "shutterstock")["desc"] == "Boats leave the harbor for the regatta"


def test_photo_sent_before_from_another_batch_is_skipped(stockup, tmp_path, batch_dir):
    p = make_photo(batch_dir, "a.jpg")
    other = tmp_path / "older batch"
    other.mkdir()
    shutil.copy(p, other / "renamed.jpg")
    stockup.record_history("shutterstock", str(other), "renamed.jpg",
                           stockup.file_sha256(p), stockup.picture_hash(p))
    b = stockup.Batch(batch_dir)
    assert b.plan("a.jpg", "shutterstock")["status"] == "skip"
    assert b.plan("a.jpg", "dreamstime")["status"] != "skip"  # only that site
    stockup.save_options({"skip_sent": False})
    assert b.plan("a.jpg", "shutterstock")["include"]


def test_photo_that_only_looks_the_same_is_flagged_not_skipped(stockup, tmp_path, batch_dir):
    p = make_photo(batch_dir, "a.jpg", quality=95)
    from PIL import Image
    other = tmp_path / "re-export.jpg"
    Image.open(p).save(other, quality=80)  # same picture, different file
    stockup.record_history("shutterstock", str(tmp_path), "re-export.jpg",
                           stockup.file_sha256(str(other)), stockup.picture_hash(str(other)))
    b = stockup.Batch(batch_dir)
    plan = b.plan("a.jpg", "shutterstock")
    assert plan["include"]
    assert any("looks the same" in i["text"] for i in plan["issues"])


def test_identical_files_in_one_batch_are_flagged(stockup, batch_dir):
    p = make_photo(batch_dir, "a.jpg")
    shutil.copy(p, f"{batch_dir}/a_copy.jpg")
    b = stockup.Batch(batch_dir)
    assert b.twins["a.jpg"] == ["a_copy.jpg"]


def test_batch_opens_before_the_full_check_and_remembers_it(stockup, batch_dir):
    for i in range(5):
        make_photo(batch_dir, f"{i}.jpg", seed=i)
    b = stockup.Batch(batch_dir, background=True)
    assert b.files and len(b.meta) == 5  # usable straight away
    import time
    for _ in range(200):
        if b.scan["ready"]:
            break
        time.sleep(0.05)
    assert b.scan["ready"] and all(b.sha.values())
    calls = []
    real = stockup.file_sha256
    stockup.file_sha256 = lambda p: calls.append(p) or real(p)
    try:
        b2 = stockup.Batch(batch_dir)
    finally:
        stockup.file_sha256 = real
    assert calls == [] and b2.sha == b.sha  # reopened from memory
