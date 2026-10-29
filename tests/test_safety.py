"""Your originals are never changed, and by default every site gets your exact files."""
import hashlib
import os

from PIL import Image

from helpers import ADOBE_ICC, make_photo


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def problem_batch(folder):
    """Photos that trip most of the checks."""
    make_photo(folder, "adobe_rgb.jpg", icc=ADOBE_ICC, progressive=True)
    make_photo(folder, "gps.jpg", gps=True, seed=1)
    make_photo(folder, "Café nom très long pour Adobe Stock.jpg", seed=2,
               title="A very long title " * 6, desc="A very long description " * 12)
    make_photo(folder, "big.jpg", size=(6000, 4000), seed=3)
    return {f: sha(os.path.join(folder, f)) for f in os.listdir(folder)}


def test_exact_files_is_the_default(stockup):
    assert stockup.load_options()["photo_mode"] == "untouched"


def test_every_site_gets_the_exact_original_bytes_by_default(stockup, batch_dir):
    orig = problem_batch(batch_dir)
    b = stockup.Batch(batch_dir)
    b.act([], "*", "*", "apply")  # even with every offered fix applied
    for f in b.files:
        for site in stockup.SITE_ORDER:
            p = b.plan(f, site)
            assert not p["ops"], (f, site, p["ops"])
            assert sha(b.site_file(f, site, p)) == orig[f], (f, site)


def test_fixes_that_change_the_picture_are_locked_in_exact_mode(stockup, batch_dir):
    problem_batch(batch_dir)
    b = stockup.Batch(batch_dir)
    issue = next(i for i in b.plan("adobe_rgb.jpg", "shutterstock")["issues"] if i["fix"] == "srgb")
    assert issue["locked"] and not issue["applied"]
    b.act(["adobe_rgb.jpg"], "shutterstock", "srgb", "apply")
    assert "shutterstock:srgb" not in b.state["adobe_rgb.jpg"]["fixes"]


def test_renamed_copies_are_real_byte_identical_copies(stockup, batch_dir):
    orig = problem_batch(batch_dir)
    b = stockup.Batch(batch_dir)
    f = "Café nom très long pour Adobe Stock.jpg"
    b.act([f], "shutterstock", "rename", "apply")
    p = b.plan(f, "shutterstock")
    copy = b.site_file(f, "shutterstock", p)
    assert os.path.basename(copy) == p["name"] != f
    assert sha(copy) == orig[f]
    assert not os.path.samefile(copy, os.path.join(batch_dir, f))  # not a link to your original
    with open(copy, "ab") as fh:  # so changing the copy can't change the original
        fh.write(b"x")
    assert sha(os.path.join(batch_dir, f)) == orig[f]


def test_originals_never_change_through_the_whole_workflow(stockup, batch_dir):
    orig = problem_batch(batch_dir)
    stockup.save_options({"photo_mode": "adjusted", "resize_mode": "megapixels", "resize_value": "8",
                          "strip_gps": True, "my_sites": list(stockup.SITES)})
    b = stockup.Batch(batch_dir)
    b.act([], "*", "*", "apply")
    r = stockup.build_outputs(b)
    assert r["changed_originals"] == []
    assert {f: sha(os.path.join(batch_dir, f)) for f in orig} == orig
    assert set(os.listdir(batch_dir)) - set(orig) <= {"stockup_output", "stockup.json",
                                                      ".stockup_fingerprints.json"}


def test_a_changed_original_is_reported(stockup, batch_dir):
    p = make_photo(batch_dir, "a.jpg")
    b = stockup.Batch(batch_dir)
    with open(p, "ab") as fh:
        fh.write(b"edited elsewhere")
    os.utime(p, ns=(1, 1))
    assert b.changed_originals() == ["a.jpg"]


def test_gps_removal_keeps_every_pixel(stockup, batch_dir):
    make_photo(batch_dir, "gps.jpg", gps=True)
    stockup.save_options({"strip_gps": True})  # works even in exact-files mode: pixels untouched
    b = stockup.Batch(batch_dir)
    p = b.plan("gps.jpg", "shutterstock")
    assert p["ops"] == ["nogps"]
    out = b.site_file("gps.jpg", "shutterstock", p)
    raw, new = open(f"{batch_dir}/gps.jpg", "rb").read(), open(out, "rb").read()
    assert len(raw) == len(new)
    assert raw[raw.find(b"\xff\xda"):] == new[new.find(b"\xff\xda"):]  # image data untouched
    assert Image.open(out).tobytes() == Image.open(f"{batch_dir}/gps.jpg").tobytes()
    assert stockup.read_photo(out)[0]["info"]["gps"] is False
    assert stockup.read_photo(f"{batch_dir}/gps.jpg")[0]["info"]["gps"] is True


def test_resize_never_enlarges_and_never_goes_below_a_sites_minimum(stockup, batch_dir):
    make_photo(batch_dir, "big.jpg", size=(6000, 4000))
    make_photo(batch_dir, "small.jpg", size=(2500, 1700), seed=1)
    stockup.save_options({"photo_mode": "adjusted", "resize_mode": "megapixels", "resize_value": "2"})
    b = stockup.Batch(batch_dir)
    for site in ("shutterstock", "123rf", "pond5", "dreamstime"):
        rules = stockup.SITES[site]
        out = b.site_file("big.jpg", site)
        info = stockup.read_photo(out)[0]["info"]
        assert info["width"] * info["height"] >= rules.get("min_px", 0)
        assert info["width"] <= 6000
    assert not b.plan("small.jpg", "shutterstock")["ops"]  # already near the minimum: left alone
    stockup.save_options({"resize_value": "100"})
    assert not b.plan("big.jpg", "shutterstock")["ops"]  # bigger than the photo: no enlarging


def test_istock_folders_hold_real_copies(stockup, batch_dir):
    make_photo(batch_dir, "a.jpg")
    stockup.save_options({"my_sites": ["istock"]})
    b = stockup.Batch(batch_dir)
    stockup.build_outputs(b)
    copy = f"{batch_dir}/stockup_output/istock/creative/a.jpg"
    assert sha(copy) == sha(f"{batch_dir}/a.jpg")
    assert not os.path.samefile(copy, f"{batch_dir}/a.jpg")


def test_logins_file_is_private(stockup):
    stockup.save_logins({"shutterstock": {"username": "me", "password": "secret"}})
    if os.name != "nt":
        assert os.stat(stockup.CONFIG_PATH).st_mode & 0o077 == 0
