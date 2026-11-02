"""The script sets up its own window the first time, instead of opening the browser, and
after updating itself it reopens in that window. pip is never really run here: a stand-in
puts a pretend window library where pip would."""
import os
import sys

import pytest


@pytest.fixture
def no_webview(stockup, tmp_path, monkeypatch):
    for name in [m for m in sys.modules if m == "webview" or m.startswith("webview.")]:
        monkeypatch.delitem(sys.modules, name)
    lib = tmp_path / "window-lib"
    monkeypatch.setattr(stockup, "window_lib_dir", lambda: str(lib))
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setenv("DISPLAY", ":0")  # a screen to show the window on
    monkeypatch.setattr(stockup, "_linux_window_works", lambda lib: True)
    real = stockup._window_lib_importable
    # only the pretend library under lib counts, never one installed on this computer
    monkeypatch.setattr(stockup, "_window_lib_importable",
                        lambda: str(lib) in sys.path and os.path.isdir(lib / "webview") and real())
    yield lib
    for name in [m for m in sys.modules if m == "webview" or m.startswith("webview.")]:
        sys.modules.pop(name, None)


def fake_pip(calls, works=True):
    def run(cmd):
        calls.append(cmd)
        if not works:
            return False
        target = cmd[cmd.index("--target") + 1]
        pkg = os.path.join(target, "webview")
        os.makedirs(pkg)
        open(os.path.join(pkg, "__init__.py"), "w").close()
        open(os.path.join(pkg, "dom.py"), "w").write("DOMEventHandler = object\n")
        return True
    return run


def test_sets_up_its_own_window_once(stockup, no_webview):
    calls = []
    assert stockup.ensure_window_library(fake_pip(calls)) is True
    assert len(calls) == 1 and "pywebview" in " ".join(calls[0])
    assert calls[0][:4] == [sys.executable, "-m", "pip", "install"]  # the same Python, not yours
    assert (no_webview / "webview").is_dir()
    assert not os.path.exists(str(no_webview) + ".new")
    # the next start finds it straight away
    assert stockup.ensure_window_library(fake_pip(calls)) is True and len(calls) == 1


def test_uses_the_browser_if_setup_fails_and_waits_before_trying_again(stockup, no_webview):
    calls = []
    assert stockup.ensure_window_library(fake_pip(calls, works=False)) is False
    assert stockup.ensure_window_library(fake_pip(calls)) is False  # not every launch
    assert len(calls) == 1
    old = os.path.getmtime(str(no_webview) + ".failed") - 8 * 86400
    os.utime(str(no_webview) + ".failed", (old, old))
    assert stockup.ensure_window_library(fake_pip(calls)) is True  # a week later it tries again
    assert not os.path.exists(str(no_webview) + ".failed")


def test_no_screen_means_the_browser_without_installing_anything(stockup, no_webview, monkeypatch):
    monkeypatch.setattr(stockup.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    calls = []
    assert stockup.ensure_window_library(fake_pip(calls)) is False and calls == []


def test_restarts_after_an_update(stockup, monkeypatch):
    seen = {}

    class Server:
        server_address = ("127.0.0.1", 4321)

        def shutdown(self):
            pass

        def server_close(self):
            pass

    def fake_exec(exe, args):
        seen.update(args=args, env=dict(os.environ))
        raise SystemExit
    monkeypatch.setattr(stockup.os, "execv", fake_exec)
    monkeypatch.setattr(stockup.os, "name", "posix")
    monkeypatch.setattr(stockup.time, "sleep", lambda s: None)
    import threading
    started = []
    monkeypatch.setattr(threading.Thread, "start", lambda self: started.append(self))

    stockup.restart_program(Server(), "/photos/batch", app_in_browser=False)
    try:
        started[-1].run()
    except SystemExit:
        pass
    assert seen["args"][-2:] == [os.path.abspath(stockup.__file__), "/photos/batch"]
    assert seen["env"]["STOCKUP_PORT"] == "4321"
    assert "STOCKUP_REOPEN_TAB" not in seen["env"]  # in its own window: a fresh window opens

    monkeypatch.delenv("STOCKUP_PORT", raising=False)
    stockup.restart_program(Server(), None, app_in_browser=True)
    try:
        started[-1].run()
    except SystemExit:
        pass
    assert seen["env"]["STOCKUP_REOPEN_TAB"] == "1"  # in a browser: the same tab reconnects
    assert seen["env"]["STOCKUP_PORT"] == "4321"
    for k in ("STOCKUP_PORT", "STOCKUP_REOPEN_TAB"):
        os.environ.pop(k, None)
