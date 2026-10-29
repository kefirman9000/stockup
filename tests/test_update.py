"""Updating: the script and the Mac/Windows apps update themselves, but only to a download
that matches the release's published fingerprint, and never leave StockUp half-updated."""
import hashlib
import http.server
import json
import os
import subprocess
import sys
import threading
import time
import zipfile

import pytest


@pytest.fixture
def release(tmp_path, monkeypatch, stockup):
    """A pretend GitHub release, served from this computer."""
    root = tmp_path / "release"
    root.mkdir()

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(root), **kw)

        def log_message(self, *a):
            pass
    srv = stockup.LocalServer(("127.0.0.1", 0), Quiet)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    files = {}

    def publish(version, assets, sums=True, bad_sum_for=None):
        for name, data in assets.items():
            (root / name).write_bytes(data)
        if sums:
            lines = []
            for name, data in assets.items():
                d = hashlib.sha256(data).hexdigest()
                if name == bad_sum_for:
                    d = "0" * 64
                lines.append(f"{d}  {name}")
            (root / "SHA256SUMS").write_text("\n".join(lines) + "\n")
            assets = dict(assets, SHA256SUMS=b"")
        files["api"] = json.dumps({"tag_name": f"v{version}", "html_url": base, "body": "notes",
                                   "assets": [{"name": n, "browser_download_url": f"{base}/{n}"}
                                              for n in assets]}).encode()
    real_get = stockup._http_get
    monkeypatch.setattr(stockup, "_http_get", lambda url, timeout=15:
                        files["api"] if "api.github.com" in url else real_get(url, timeout))
    yield publish
    srv.shutdown()
    srv.server_close()


def fake_app(folder, version, kind):
    """A pretend installed app that knows its version."""
    if kind == "mac":
        exe = folder / "StockUp.app" / "Contents" / "MacOS" / "StockUp"
    else:
        exe = folder / "StockUp" / "StockUp.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text(f"version {version}")
    return exe


def zip_app(path, version, kind):
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        name = "StockUp.app/Contents/MacOS/StockUp" if kind == "mac" else "StockUp/StockUp.exe"
        z.writestr(name, f"version {version}")
    return buf.getvalue()


def wait(cond, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    raise AssertionError("timed out")


@pytest.fixture
def frozen_app(stockup, monkeypatch, tmp_path):
    kind = "win" if os.name == "nt" else "mac"
    asset = "StockUp-Windows.zip" if kind == "win" else "StockUp-Mac.zip"
    installed = tmp_path / "Applications"
    exe = fake_app(installed, "1.0.0", kind)
    path = str(exe.parent) if kind == "win" else str(installed / "StockUp.app")
    monkeypatch.setattr(stockup, "FROZEN", True)
    monkeypatch.setattr(stockup, "VERSION", "1.0.0")
    monkeypatch.setattr(stockup, "APP_ASSET", asset)
    monkeypatch.setattr(stockup, "installed_app", lambda: (kind, path))
    # the pretend apps are text files, so read their version instead of running them
    monkeypatch.setattr(stockup, "app_version", lambda exe, work: open(exe).read().split()[-1])
    exits = []
    monkeypatch.setattr(os, "_exit", lambda code: exits.append(code))
    return {"kind": kind, "asset": asset, "path": path, "exe": exe, "exits": exits}


def run_update(stockup, app):
    up = app.updater
    up.start(app)
    wait(lambda: up.state["stage"] in ("error", "restarting"))
    return up.state


def test_app_updates_itself_keeps_a_backup_and_reopens(stockup, release, frozen_app, tmp_path, monkeypatch):
    kind = frozen_app["kind"]
    release("9.9.9", {frozen_app["asset"]: zip_app(None, "9.9.9", kind)})
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1)"])
    threading.Thread(target=sleeper.wait, daemon=True).start()  # tidied away once it quits, as the OS does
    monkeypatch.setenv("STOCKUP_TEST_WAITPID", str(sleeper.pid))  # "StockUp" quitting
    marker = tmp_path / "reopened.txt"
    mark = tmp_path / "mark.py"
    mark.write_text(f"open(r'{marker}', 'w').write('yes')")
    monkeypatch.setenv("STOCKUP_TEST_RELAUNCH", "\x1f".join([sys.executable, str(mark)]))
    app = stockup.App()
    state = run_update(stockup, app)
    assert state["stage"] == "restarting", state
    work = (stockup.CONF_DIR + "/update") if kind == "mac" else os.path.join(
        os.path.dirname(frozen_app["path"]), "StockUp-update")
    log = os.path.join(work, "update.log")
    try:
        wait(lambda: marker.exists())  # swapped, then reopened
    except AssertionError:
        out = os.path.join(work, "swap-output.txt")
        raise AssertionError("didn't reopen. Update log:\n" + (open(log).read() if os.path.exists(log)
                             else "(no log: the helper never ran)") + "\nHelper output:\n" +
                             (open(out, errors="replace").read() if os.path.exists(out) else "(none)") +
                             "\nFolder: " + repr(os.listdir(work) if os.path.isdir(work) else None))
    exe = frozen_app["exe"]
    assert open(exe).read() == "version 9.9.9"  # the new version is in place
    backup = os.path.join(work, "previous", os.path.basename(frozen_app["path"]))
    old_exe = os.path.join(backup, "Contents", "MacOS", "StockUp") if kind == "mac" \
        else os.path.join(backup, "StockUp.exe")
    assert open(old_exe).read() == "version 1.0.0"  # and the old one is kept
    wait(lambda: frozen_app["exits"])


def test_a_download_that_doesnt_match_its_fingerprint_is_never_installed(stockup, release, frozen_app):
    release("9.9.9", {frozen_app["asset"]: zip_app(None, "9.9.9", frozen_app["kind"])},
            bad_sum_for=frozen_app["asset"])
    state = run_update(stockup, stockup.App())
    assert state["stage"] == "error" and "fingerprint" in state["error"]
    assert open(frozen_app["exe"]).read() == "version 1.0.0"


def test_a_release_without_fingerprints_is_never_installed(stockup, release, frozen_app):
    release("9.9.9", {frozen_app["asset"]: zip_app(None, "9.9.9", frozen_app["kind"])}, sums=False)
    state = run_update(stockup, stockup.App())
    assert state["stage"] == "error" and "fingerprint" in state["error"]
    assert open(frozen_app["exe"]).read() == "version 1.0.0"


def test_an_app_reporting_the_wrong_version_is_not_installed(stockup, release, frozen_app):
    release("9.9.9", {frozen_app["asset"]: zip_app(None, "8.0.0", frozen_app["kind"])})
    state = run_update(stockup, stockup.App())
    assert state["stage"] == "error" and "8.0.0" in state["error"]
    assert open(frozen_app["exe"]).read() == "version 1.0.0"


def test_already_up_to_date(stockup, release, frozen_app):
    release("1.0.0", {frozen_app["asset"]: zip_app(None, "1.0.0", frozen_app["kind"])})
    state = run_update(stockup, stockup.App())
    assert state["stage"] == "error" and "latest version" in state["error"]


def test_explains_when_it_cant_replace_itself(stockup, tmp_path):
    assert "Applications folder" in stockup.update_blocker(
        ("mac", "/private/var/folders/x/AppTranslocation/ABC/d/StockUp.app"))
    assert "Download" in stockup.update_blocker(None)
    if os.name != "nt" and os.geteuid() != 0:
        locked = tmp_path / "locked"
        (locked / "StockUp.app").mkdir(parents=True)
        os.chmod(locked, 0o555)
        try:
            assert "permission" in stockup.update_blocker(("mac", str(locked / "StockUp.app")))
        finally:
            os.chmod(locked, 0o755)


def test_the_script_refuses_a_download_that_doesnt_match(stockup, release, tmp_path, monkeypatch):
    me = tmp_path / "stockup.py"
    me.write_text('VERSION = "1.0.0"\n')
    monkeypatch.setattr(stockup, "__file__", str(me))
    monkeypatch.setattr(stockup, "VERSION", "1.0.0")
    release("9.9.9", {"stockup.py": b'VERSION = "9.9.9"\n'}, bad_sum_for="stockup.py")
    with pytest.raises(RuntimeError, match="fingerprint"):
        stockup.install_update()
    assert me.read_text() == 'VERSION = "1.0.0"\n'
    release("9.9.9", {"stockup.py": b'VERSION = "9.9.9"\n'})
    assert stockup.install_update()["updated"]
    assert me.read_text() == 'VERSION = "9.9.9"\n'


def test_the_app_can_report_its_version(tmp_path):
    out = tmp_path / "v.txt"
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    subprocess.run([sys.executable, os.path.join(root, "stockup.py"), "--version-to", str(out)],
                   check=True, timeout=60)
    import re
    src = open(os.path.join(root, "stockup.py"), encoding="utf-8").read()
    assert out.read_text() == re.search(r'^VERSION = "([^"]+)"', src, re.M).group(1)
