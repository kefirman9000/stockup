"""StockUp's own window. The real window needs a screen, so these tests use a stand-in for the
window library; the release builds also open the real window on Mac and Windows."""
import json
import os
import sys
import threading
import types
import urllib.request

import pytest

from helpers import make_photo


class FakeEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, h):
        self.handlers.append(h)
        return self

    def set(self):
        return all(h() is not False for h in self.handlers)


class FakeWindow:
    def __init__(self, url):
        self.url, self.js, self.destroyed = url, [], False
        self.events = types.SimpleNamespace(closing=FakeEvent())
        self.picks, self.confirm_answer, self.confirm_asked = [], True, False
        self.dom = types.SimpleNamespace(document=types.SimpleNamespace(
            events=types.SimpleNamespace(drop=FakeEvent())))

    def evaluate_js(self, code):
        self.js.append(code)

    def create_file_dialog(self, kind, allow_multiple=False, file_types=()):
        return tuple(self.picks)

    def create_confirmation_dialog(self, title, message):
        self.confirm_asked = True
        return self.confirm_answer

    def destroy(self):
        self.destroyed = True


@pytest.fixture
def fake_webview(monkeypatch):
    mod = types.ModuleType("webview")
    mod.FileDialog = types.SimpleNamespace(OPEN=10, FOLDER=20)
    made = {}

    def create_window(title, url, **kw):
        made["window"] = FakeWindow(url)
        return made["window"]

    def start(func, window, **kw):
        func(window)  # like the real thing: runs once the window is up, then returns when it closes
    mod.create_window, mod.start = create_window, start
    dom = types.ModuleType("webview.dom")
    dom.DOMEventHandler = lambda handler, prevent_default, stop_propagation: handler
    monkeypatch.setitem(sys.modules, "webview", mod)
    monkeypatch.setitem(sys.modules, "webview.dom", dom)
    return made


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / "Pictures").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    return h


def drop_event(*paths):
    return {"dataTransfer": {"files": [{"name": os.path.basename(p), "pywebviewFullPath": p}
                                       for p in paths]}}


def last_result(window):
    done = [c for c in window.js if c.startswith("nativeDropDone(")]
    return json.loads(done[-1][len("nativeDropDone("):-1])


def test_opens_in_its_own_window(stockup, fake_webview):
    app = stockup.App()
    assert stockup.open_window(app, "http://127.0.0.1:1/") is True
    w = fake_webview["window"]
    assert w.url == "http://127.0.0.1:1/" and app.window is w
    assert len(w.dom.document.events.drop.handlers) == 1  # drops come with real locations


def test_falls_back_to_the_browser_without_a_window_library(stockup, monkeypatch):
    monkeypatch.setitem(sys.modules, "webview", None)  # as if it isn't installed
    assert stockup.open_window(stockup.App(), "http://127.0.0.1:1/") is False


def test_a_dropped_folder_opens_in_place(stockup, fake_webview, tmp_path, home):
    folder = tmp_path / "Trip"
    folder.mkdir()
    for i in range(2):
        make_photo(str(folder), f"{i}.jpg", seed=i)
    app = stockup.App()
    stockup.open_window(app, "u")
    w = fake_webview["window"]
    stockup.handle_native_drop(app, w, drop_event(str(folder)))
    assert app.batch.folder == str(folder) and app.batch.files == ["0.jpg", "1.jpg"]
    assert "nothing was copied" in last_result(w)["message"]
    assert not (home / "Pictures" / "StockUp Batches").exists()


def test_dropped_photos_from_one_folder_open_in_place(stockup, fake_webview, tmp_path, home):
    a = make_photo(str(tmp_path), "a.jpg")
    make_photo(str(tmp_path), "b.jpg", seed=1)
    app = stockup.App()
    stockup.open_window(app, "u")
    stockup.handle_native_drop(app, fake_webview["window"], drop_event(a))
    assert app.batch.folder == str(tmp_path) and app.batch.files == ["a.jpg"]


def test_photos_from_several_places_are_gathered_into_a_copy(stockup, fake_webview, tmp_path, home):
    (tmp_path / "x").mkdir()
    (tmp_path / "y").mkdir()
    a = make_photo(str(tmp_path / "x"), "a.jpg")
    b = make_photo(str(tmp_path / "y"), "b.jpg", seed=1)
    app = stockup.App()
    stockup.open_window(app, "u")
    w = fake_webview["window"]
    stockup.handle_native_drop(app, w, drop_event(a, b))
    assert stockup.in_drop_root(app.batch.folder) and app.batch.files == ["a.jpg", "b.jpg"]
    assert "Copied 2 photos" in last_result(w)["message"]


def test_dropping_non_photos_or_during_an_upload_is_explained(stockup, fake_webview, tmp_path):
    txt = tmp_path / "notes.txt"
    txt.write_text("x")
    app = stockup.App()
    stockup.open_window(app, "u")
    w = fake_webview["window"]
    stockup.handle_native_drop(app, w, drop_event(str(txt)))
    assert "None of those" in last_result(w)["error"]
    app.build_state["running"] = True
    stockup.handle_native_drop(app, w, drop_event(str(txt)))
    assert "Wait for the upload" in last_result(w)["error"]


def test_closing_during_an_upload_asks_first(stockup, fake_webview):
    app = stockup.App()
    stockup.open_window(app, "u")
    w = fake_webview["window"]
    assert w.events.closing.set() and not w.confirm_asked  # nothing running: just closes
    app.build_state["running"] = True
    w.confirm_answer = False
    assert w.events.closing.set() is False and w.confirm_asked  # you said no: stays open


def test_choose_buttons_use_the_computers_own_windows(stockup, fake_webview, tmp_path):
    (tmp_path / "Trip").mkdir()
    a = make_photo(str(tmp_path / "Trip"), "a.jpg")
    make_photo(str(tmp_path / "Trip"), "b.jpg", seed=1)
    app = stockup.App()
    stockup.open_window(app, "u")
    w = fake_webview["window"]
    server = stockup.LocalServer(("127.0.0.1", 0), stockup.make_handler(app))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    post = lambda p: json.loads(urllib.request.urlopen(
        urllib.request.Request(url + p, data=b"{}", method="POST"), timeout=10).read())
    try:
        w.picks = [str(tmp_path / "Trip")]
        assert post("/api/pick_folder")["ok"] and app.batch.files == ["a.jpg", "b.jpg"]
        w.picks = [a]
        r = post("/api/pick_photos")
        assert r["ok"] and r["in_place"] and app.batch.files == ["a.jpg"]
        w.picks = []
        assert post("/api/pick_photos")["cancelled"]
        assert json.loads(urllib.request.urlopen(url + "/api/items", timeout=10).read())["native"]
    finally:
        server.shutdown()
        server.server_close()
