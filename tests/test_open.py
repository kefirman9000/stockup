"""Opening batches: no folder at start, choosing, recent batches, and photos dropped in."""
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

from helpers import make_photo


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A pretend home folder, so drops and searches never touch your real one."""
    h = tmp_path / "home"
    (h / "Pictures").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    return h


@pytest.fixture
def page(stockup, home):
    app = stockup.App()
    server = stockup.LocalServer(("127.0.0.1", 0), stockup.make_handler(app))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", app
    server.shutdown()
    server.server_close()


def call(url, body=None, raw=None):
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    req = urllib.request.Request(url, data=data, method="GET" if data is None else "POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def drop(url, files, add=False):
    start = call(url + "/api/drop/start", {"add": add})
    results = [call(f"{url}/api/drop/file?token={start['token']}&name={urllib.parse.quote(name)}",
                    raw=data) for name, data in files]
    return start, results, call(url + "/api/drop/finish", {"token": start["token"]})


def test_starts_without_a_folder(page):
    url, _ = page
    r = call(url + "/api/items")
    assert r["no_batch"] and r["recent"] == []
    assert "Open a batch first" in call(url + "/api/build", {})["error"]


def test_open_a_folder_and_it_appears_in_recent(page, tmp_path):
    url, app = page
    folder = tmp_path / "Trip"
    folder.mkdir()
    make_photo(str(folder), "a.jpg")
    assert call(url + "/api/open", {"folder": str(folder)})["ok"]
    assert [i["file"] for i in call(url + "/api/items")["items"]] == ["a.jpg"]
    call(url + "/api/another", {})
    rec = call(url + "/api/items")["recent"]
    assert rec[0]["folder"] == str(folder) and rec[0]["photos"] == 1


def test_dropped_photos_are_copied_exactly_and_safely(page, tmp_path, home):
    url, app = page
    src = tmp_path / "elsewhere"
    src.mkdir()
    a = make_photo(str(src), "a.jpg")
    b = make_photo(str(src), "b.jpg", seed=2)
    files = [("a.jpg", open(a, "rb").read()),
             ("../../escape.jpg", open(b, "rb").read()),   # a path, not a name: only the name is used
             ("notes.txt", b"hello"),                       # not a JPEG: left out
             ("a.jpg", open(a, "rb").read()),               # the same photo twice: kept once
             ("a.jpg", open(b, "rb").read())]               # a different photo, same name: renamed
    start, results, done = drop(url, files)
    folder = start["folder"]
    assert done["ok"] and folder.startswith(str(home / "Pictures" / "StockUp Batches"))
    assert sorted(x for x in os.listdir(folder) if x.endswith(".jpg")) == ["a (2).jpg", "a.jpg", "escape.jpg"]
    assert results[2] == {"skipped": "not a JPEG"} and results[3].get("same")
    assert sha(os.path.join(folder, "a.jpg")) == sha(a) and sha(os.path.join(folder, "escape.jpg")) == sha(b)
    assert not (tmp_path / "escape.jpg").exists()
    assert app.batch.folder == folder


def test_dropping_more_photos_can_add_to_a_dropped_batch(page, tmp_path):
    url, app = page
    a = make_photo(str(tmp_path), "a.jpg")
    b = make_photo(str(tmp_path), "b.jpg", seed=3)
    s1, _, _ = drop(url, [("a.jpg", open(a, "rb").read())])
    s2, _, _ = drop(url, [("b.jpg", open(b, "rb").read())], add=True)
    assert s2["adding"] and s2["folder"] == s1["folder"]
    assert app.batch.files == ["a.jpg", "b.jpg"]


def test_a_drop_with_no_photos_leaves_nothing_behind(page, home):
    url, _ = page
    start, _, done = drop(url, [("readme.txt", b"x")])
    assert "None of those" in done["error"] and not os.path.exists(start["folder"])


def test_dropped_photos_found_on_disk_open_in_place(stockup, home):
    trip = home / "Pictures" / "2026" / "Maine trip"
    trip.mkdir(parents=True)
    for i in range(3):
        make_photo(str(trip), f"IMG_{i}.jpg", seed=i)
    info = lambda n: {"name": n, "size": os.path.getsize(trip / n), "mtime": os.path.getmtime(trip / n) * 1000}
    names = sorted(os.listdir(trip))
    assert stockup.locate_dropped("Maine trip", [info(n) for n in names]) == (str(trip), None)
    assert stockup.locate_dropped("", [info("IMG_1.jpg")]) == (str(trip), ["IMG_1.jpg"])
    wrong = dict(info("IMG_1.jpg"), size=123)
    assert stockup.locate_dropped("", [wrong]) == (None, None)  # not the same photo: never guessed
    t = time.time()
    stockup.locate_dropped("Not there", [info("IMG_1.jpg")], budget_seconds=1)
    assert time.time() - t < 5


def test_opening_some_photos_keeps_the_rest_of_the_folders_work(stockup, batch_dir):
    for i in range(3):
        make_photo(batch_dir, f"{i}.jpg", seed=i)
    full = stockup.Batch(batch_dir)
    full.save({"0.jpg": dict(full.state["0.jpg"], editorial=True)})
    part = stockup.Batch(batch_dir, only=["1.jpg"])
    assert part.files == ["1.jpg"]
    part.save({"1.jpg": dict(part.state["1.jpg"], editorial=True)})
    again = stockup.Batch(batch_dir)
    assert again.state["0.jpg"]["editorial"] and again.state["1.jpg"]["editorial"]
    assert again.sha["0.jpg"]  # fingerprints of the other photos are kept too


def test_things_dropped_on_the_app_icon(stockup, home, tmp_path):
    one = tmp_path / "one"
    two = tmp_path / "two"
    one.mkdir()
    two.mkdir()
    a = make_photo(str(one), "a.jpg")
    b = make_photo(str(one), "b.jpg", seed=1)
    c = make_photo(str(two), "c.jpg", seed=2)
    assert stockup.import_paths([str(one)]) == (str(one), None)
    assert stockup.import_paths([a, b]) == (str(one), ["a.jpg", "b.jpg"])  # in place, no copying
    folder, only = stockup.import_paths([a, c])  # from two places: gathered into one batch
    assert only is None and stockup.in_drop_root(folder)
    assert sha(os.path.join(folder, "a.jpg")) == sha(a) and sha(os.path.join(folder, "c.jpg")) == sha(c)


def test_thumbnails_are_kept_by_the_browser(page, tmp_path):
    url, app = page
    make_photo(str(tmp_path), "a.jpg")
    call(url + "/api/open", {"folder": str(tmp_path)})
    with urllib.request.urlopen(url + "/thumb/a.jpg", timeout=10) as r:
        assert "max-age" in r.headers["Cache-Control"]
    with urllib.request.urlopen(url + "/api/items", timeout=10) as r:
        assert r.headers["Cache-Control"] == "no-store"


def test_look_alike_check_stays_fast_with_a_long_history(stockup, batch_dir):
    import random
    r = random.Random(1)
    looks = []
    for i in range(20000):
        looks.append(["%016x" % r.getrandbits(64), "shutterstock", "2026-01-01", "/old", f"o{i}.jpg"])
    p = make_photo(batch_dir, "a.jpg")
    near = "%016x" % (int(stockup.picture_hash(p), 16) ^ 0b101)  # 2 bits apart
    looks.append([near, "shutterstock", "2026-01-02", "/old", "match.jpg"])
    with open(stockup.HISTORY_PATH, "w") as fh:
        json.dump({"sha": {}, "looks": looks}, fh)
    for i in range(1, 40):
        make_photo(batch_dir, f"{i}.jpg", seed=i)
    b = stockup.Batch(batch_dir)
    t = time.time()
    b.items()
    assert time.time() - t < 3
    assert b.sent_before("a.jpg", "shutterstock") == (
        "looks", {"date": "2026-01-02", "batch": "/old", "file": "match.jpg"})


def test_checking_logins_tests_sites_at_the_same_time(stockup, monkeypatch):
    calls = []

    def slow_connect(site, timeout=60):
        calls.append(time.time())
        time.sleep(1)
        raise RuntimeError("no answer")
    monkeypatch.setattr(stockup, "connect", slow_connect)
    monkeypatch.setattr(stockup, "load_sites", lambda: [
        {"name": n, "username": "u", "password": "p"} for n in ("shutterstock", "dreamstime", "alamy")])
    stockup.save_options({"my_sites": ["shutterstock", "dreamstime", "alamy"]})
    t = time.time()
    res = stockup.test_sites()
    assert time.time() - t < 2.5 and len(res) == 3
