"""StockUp's page: only StockUp's own page may control it."""
import json
import threading
import urllib.error
import urllib.request

import pytest

from helpers import make_photo


@pytest.fixture
def page(stockup, batch_dir):
    make_photo(batch_dir, "a.jpg")
    batch = stockup.Batch(batch_dir)
    app = stockup.App(batch)
    server = stockup.LocalServer(("127.0.0.1", 0), stockup.make_handler(app))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", batch
    server.shutdown()
    server.server_close()


def call(url, body=None, headers=None):
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                 headers=headers or {}, method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_page_and_photo_list_load(page):
    url, _ = page
    code, html = call(url + "/")
    assert code == 200 and b"<title>StockUp</title>" in html
    code, body = call(url + "/api/items")
    items = json.loads(body)
    assert code == 200 and items["items"][0]["file"] == "a.jpg" and items["scan"]["ready"]


def test_other_websites_are_refused(page):
    url, _ = page
    port = url.rsplit(":", 1)[1]
    assert call(url + "/api/items", headers={"Host": f"evil.example:{port}"})[0] == 403
    assert call(url + "/api/build", {}, headers={"Origin": "https://evil.example"})[0] == 403
    assert call(url + "/api/build", {})[0] == 200


def test_upload_waits_for_the_photo_check(page):
    url, batch = page
    batch.scan["ready"] = False
    code, body = call(url + "/api/upload", {"sites": ["shutterstock"]})
    assert b"still checking" in body


def test_starts_without_waiting_on_the_network(stockup, monkeypatch):
    """Python's standard server looks up the computer's network name when it starts, which
    hung for 35 seconds on a Mac. StockUp's server must never wait for that."""
    import socket
    import time

    def slow_lookup(*a, **kw):
        time.sleep(10)
        return "slow.example"
    monkeypatch.setattr(socket, "getfqdn", slow_lookup)
    monkeypatch.delenv("STOCKUP_PORT", raising=False)
    t = time.time()
    server = stockup.start_server(stockup.App())
    took = time.time() - t
    server.shutdown()
    server.server_close()
    assert took < 1, f"starting took {took:.1f}s"
