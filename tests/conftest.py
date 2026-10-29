import importlib
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def stockup(tmp_path, monkeypatch):
    """A fresh StockUp whose settings, logins and history live in a temporary folder,
    so tests never touch your real StockUp settings."""
    import stockup as su
    su = importlib.reload(su)
    conf = tmp_path / "settings"
    conf.mkdir()
    monkeypatch.setattr(su, "CONF_DIR", str(conf))
    monkeypatch.setattr(su, "CONFIG_PATH", str(conf / "stockup_sites.ini"))
    monkeypatch.setattr(su, "OPTIONS_PATH", str(conf / "stockup_options.json"))
    monkeypatch.setattr(su, "HISTORY_PATH", str(conf / "upload_history.json"))
    monkeypatch.setattr(su, "_keyring", lambda: None)  # never touch your real keychain
    su._update_cache.update(t=0, data=None)
    return su


@pytest.fixture
def batch_dir(tmp_path):
    d = tmp_path / "batch"
    d.mkdir()
    return str(d)


@pytest.fixture
def cert(tmp_path):
    from helpers import make_cert
    c = make_cert(str(tmp_path))
    if not c:
        pytest.skip("openssl isn't installed, so the encrypted-connection tests can't run")
    return c
