"""Uploading, against small FTP/FTPS servers running inside the tests."""
import csv
import hashlib
import os
import time

import pytest

from helpers import FTPTestServer, make_photo


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def wait(cond, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    raise AssertionError("timed out")


def all_complete(server_root, b):
    """Every file on the server is a complete, exact copy of a photo (never half a photo)."""
    return all(sha(os.path.join(server_root, f)) == b.sha[f] for f in os.listdir(server_root))


@pytest.fixture
def servers(tmp_path, cert):
    made = []

    def make(name, tls=False, **kw):
        root = tmp_path / f"server_{name}"
        root.mkdir()
        s = FTPTestServer(str(root), cert=cert if tls else None, **kw)
        s.root = str(root)
        made.append(s)
        return s
    yield make
    for s in made:
        s.close()


def use_sites(stockup, monkeypatch, sites):
    monkeypatch.setattr(stockup, "load_sites", lambda: sites)


def run(stockup, b, names):
    u = stockup.Uploader(b)
    assert u.start(only=names)
    wait(lambda: not u.status()["running"])
    return u


def photos(folder, n=3, noise=False, size=(2400, 1700)):
    for i in range(n):
        make_photo(folder, f"p{i}.jpg", seed=i, noise=noise, size=size)


def test_uploads_exact_files_with_and_without_encryption(stockup, monkeypatch, servers, batch_dir):
    photos(batch_dir)
    enc, plain = servers("enc", tls=True), servers("plain")
    use_sites(stockup, monkeypatch, [enc.site("dreamstime"), plain.site("shutterstock")])
    b = stockup.Batch(batch_dir)
    u = run(stockup, b, ["dreamstime", "shutterstock"])
    st = u.status()["sites"]
    assert st["dreamstime"]["encrypted"] is True and st["shutterstock"]["encrypted"] is False
    assert st["dreamstime"]["done"] == st["shutterstock"]["done"] == 3
    for s in (enc, plain):
        assert sorted(os.listdir(s.root)) == ["p0.jpg", "p1.jpg", "p2.jpg"] and all_complete(s.root, b)
    rows = list(csv.DictReader(open(u.receipt_path, encoding="utf-8")))
    assert len(rows) == 6 and {r["exact original"] for r in rows} == {"yes"}
    assert {r["encrypted"] for r in rows if r["site"] == "Dreamstime"} == {"yes"}


def test_encrypted_only_refuses_a_plain_server(stockup, monkeypatch, servers, batch_dir):
    photos(batch_dir, 1)
    plain = servers("plain")
    stockup.save_options({"encrypted_only": True})
    use_sites(stockup, monkeypatch, [plain.site("dreamstime")])
    u = run(stockup, stockup.Batch(batch_dir), ["dreamstime"])
    assert u.status()["sites"]["dreamstime"]["state"] == "error"
    assert os.listdir(plain.root) == []
    with pytest.raises(RuntimeError):
        stockup.FTPConn(plain.site("shutterstock", protocol="ftps"))  # FTPS is never downgraded


def test_wrong_password_stops_that_site(stockup, monkeypatch, servers, batch_dir):
    photos(batch_dir, 1)
    s = servers("enc", tls=True)
    use_sites(stockup, monkeypatch, [s.site("dreamstime", password="nope")])
    u = run(stockup, stockup.Batch(batch_dir), ["dreamstime"])
    st = u.status()["sites"]["dreamstime"]
    assert st["state"] == "error" and "530" in st["errors"][0]


def test_never_overwrites_a_file_already_on_the_server(stockup, monkeypatch, servers, batch_dir):
    photos(batch_dir, 2)
    s = servers("plain")
    with open(os.path.join(s.root, "p0.jpg"), "w") as fh:
        fh.write("someone else's file")
    use_sites(stockup, monkeypatch, [s.site("shutterstock")])
    u = run(stockup, stockup.Batch(batch_dir), ["shutterstock"])
    assert open(os.path.join(s.root, "p0.jpg")).read() == "someone else's file"
    assert any("already on" in n for n in u.status()["sites"]["shutterstock"]["notes"])
    assert "p1.jpg" in os.listdir(s.root)


def test_dropped_connection_retries_and_replaces_its_own_half_file(stockup, monkeypatch, servers, batch_dir):
    photos(batch_dir, 2, noise=True)
    s = servers("plain", drop_once={"p0.jpg"})
    use_sites(stockup, monkeypatch, [s.site("shutterstock")])
    b = stockup.Batch(batch_dir)
    u = run(stockup, b, ["shutterstock"])
    assert u.status()["sites"]["shutterstock"]["done"] == 2
    assert s.stors.count("p0.jpg") == 2 and all_complete(s.root, b)
    assert u._partials() == {}


@pytest.mark.parametrize("tls", [False, True])
def test_pause_and_resume_never_leave_half_a_photo(stockup, monkeypatch, servers, batch_dir, tls):
    photos(batch_dir, 3, noise=True)
    s = servers("srv", tls=tls, slow=0.01)
    use_sites(stockup, monkeypatch, [s.site("shutterstock")])
    stockup.save_options({"upload_order": "folder"})
    b = stockup.Batch(batch_dir)
    u = stockup.Uploader(b)
    u.start(only=["shutterstock"])
    wait(lambda: u.status()["sites"]["shutterstock"]["state"] == "uploading")
    time.sleep(0.3)
    u.pause(True)
    wait(lambda: u.status()["sites"]["shutterstock"]["state"] == "paused")
    assert u.status()["paused"]
    assert all_complete(s.root, b)  # nothing half-sent left behind while paused
    time.sleep(0.5)
    assert u.status()["sites"]["shutterstock"]["state"] == "paused"  # stays paused
    u.pause(False)
    wait(lambda: not u.status()["running"])
    assert u.status()["sites"]["shutterstock"]["done"] == 3
    assert sorted(os.listdir(s.root)) == ["p0.jpg", "p1.jpg", "p2.jpg"] and all_complete(s.root, b)
    assert u._partials() == {}


def test_stop_now_cleans_up_and_upload_carries_on_later(stockup, monkeypatch, servers, batch_dir):
    photos(batch_dir, 3, noise=True)
    s = servers("srv", slow=0.01)
    use_sites(stockup, monkeypatch, [s.site("shutterstock")])
    stockup.save_options({"upload_order": "folder"})
    b = stockup.Batch(batch_dir)
    u = stockup.Uploader(b)
    u.start(only=["shutterstock"])
    wait(lambda: u.status()["sites"]["shutterstock"]["state"] == "uploading")
    time.sleep(0.3)
    u.stop()
    wait(lambda: not u.status()["running"])
    assert u.status()["sites"]["shutterstock"]["state"] == "stopped"
    assert all_complete(s.root, b) and u._partials() == {}
    sent_first = set(os.listdir(s.root))
    before = len(s.stors)
    u2 = run(stockup, b, ["shutterstock"])
    assert sorted(os.listdir(s.root)) == ["p0.jpg", "p1.jpg", "p2.jpg"] and all_complete(s.root, b)
    assert not sent_first & set(s.stors[before:])  # finished photos weren't sent twice
    assert u2.status()["sites"]["shutterstock"]["errors"] == []


def test_photos_sent_from_an_earlier_batch_are_not_sent_again(stockup, monkeypatch, servers, tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    make_photo(str(first), "a.jpg")
    import shutil
    shutil.copy(first / "a.jpg", second / "a_renamed.jpg")
    make_photo(str(second), "new.jpg", seed=5)
    s = servers("plain")
    use_sites(stockup, monkeypatch, [s.site("shutterstock")])
    run(stockup, stockup.Batch(str(first)), ["shutterstock"])
    run(stockup, stockup.Batch(str(second)), ["shutterstock"])
    assert s.stors == ["a.jpg", "new.jpg"]


def test_one_site_at_a_time(stockup, monkeypatch, servers, batch_dir):
    photos(batch_dir, 2, noise=True)
    a, c = servers("a", slow=0.005), servers("c", slow=0.005)
    use_sites(stockup, monkeypatch, [a.site("shutterstock"), c.site("dreamstime")])
    stockup.save_options({"upload_pacing": "one_by_one"})
    b = stockup.Batch(batch_dir)
    u = stockup.Uploader(b)
    u.start(only=["shutterstock", "dreamstime"])
    wait(lambda: u.status()["sites"]["shutterstock"]["state"] == "uploading")
    assert u.status()["sites"]["dreamstime"]["state"] == "waiting"
    wait(lambda: not u.status()["running"])
    assert len(os.listdir(a.root)) == len(os.listdir(c.root)) == 2


def test_dreamstime_spreadsheet_is_sent_to_its_ftp(stockup, monkeypatch, servers, batch_dir):
    photos(batch_dir, 1)
    s = servers("dt", tls=True)
    use_sites(stockup, monkeypatch, [s.site("dreamstime")])
    stockup.save_options({"my_sites": ["dreamstime"]})
    b = stockup.Batch(batch_dir)
    stockup.build_outputs(b)
    r = stockup.send_sheet(b, "dreamstime")
    assert "encrypted" in r["message"]
    assert open(os.path.join(s.root, "dreamstime.csv"), encoding="utf-8").read().startswith("Filename,Image Name")
