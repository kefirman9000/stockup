"""Remakes every screenshot in this folder from the sample photos in sample-batch/.

    python screenshots/make_screenshots.py

Needs Pillow and Playwright (pip install pillow playwright; playwright install chromium).
Nothing here touches your real StockUp settings, logins or keychain: it all runs in a
temporary folder, and "uploads" go to small pretend FTP servers on this computer.
The sample photos are drawn landscapes, made for this, so they're free to show anywhere.
"""
import os
import shutil
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path[:0] = [ROOT, os.path.join(ROOT, "tests")]

import stockup  # noqa: E402
from helpers import FTPTestServer  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

WIDE, TALL = {"width": 1360, "height": 900}, {"width": 1360, "height": 760}


def setup():
    tmp = tempfile.mkdtemp(prefix="stockup-shots-")
    home = os.path.join(tmp, "home")
    batch = os.path.join(home, "Pictures", "2026", "Maine trip")
    shutil.copytree(os.path.join(HERE, "sample-batch"), batch)
    conf = os.path.join(tmp, "settings")
    os.makedirs(conf)
    os.environ["HOME"] = os.environ["USERPROFILE"] = home
    stockup.CONF_DIR = conf
    stockup.CONFIG_PATH = os.path.join(conf, "stockup_sites.ini")
    stockup.OPTIONS_PATH = os.path.join(conf, "stockup_options.json")
    stockup.HISTORY_PATH = os.path.join(conf, "upload_history.json")
    stockup._keyring = lambda: None  # never your real keychain
    stockup.save_options({"my_sites": ["shutterstock", "adobe", "dreamstime", "alamy", "istock",
                                       "depositphotos", "pond5"]})
    # Pretend sites: slow enough that the progress bars and Pause show up.
    servers = {}
    for site in ("shutterstock", "dreamstime", "alamy"):
        root = os.path.join(tmp, "server-" + site)
        os.makedirs(root)
        servers[site] = FTPTestServer(root, slow=0.12)
    stockup.save_logins({s: {"username": "demo", "password": "pw"} for s in servers})
    ini = open(stockup.CONFIG_PATH).read()
    for site, srv in servers.items():
        host = stockup.SITES[site]["ftp"]["host"]
        ini = ini.replace(f"host = {host}\nport = 21", f"host = 127.0.0.1\nport = {srv.port}")
    ini = ini.replace("protocol = ftps", "protocol = ftp").replace("username = demo", "username = u")
    open(stockup.CONFIG_PATH, "w").write(ini)
    return tmp, batch, servers


def main():
    tmp, folder, servers = setup()
    app = stockup.App()
    server = stockup.LocalServer(("127.0.0.1", 0), stockup.make_handler(app))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    home = os.environ["HOME"]

    def shot(pg, name):
        # Back to the top, and a tidy example location instead of the temporary folder.
        pg.evaluate("""([home]) => { scrollTo(0, 0); document.querySelectorAll('#grid, .grid, main')
            .forEach(e => e.scrollTop = 0); const w = document.createTreeWalker(document.body, 4);
            while (w.nextNode()) w.currentNode.nodeValue = w.currentNode.nodeValue
                .split(home).join('/Users/demo'); }""", [home])
        pg.screenshot(path=os.path.join(HERE, name + ".png"))
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()

        def page(scheme, size=WIDE):
            pg = browser.new_page(viewport=size, color_scheme=scheme)
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(url)
            pg.wait_for_timeout(1200)
            return pg

        # Welcome screen, with the sample batch in Recent.
        pg = page("dark")
        pg.evaluate(f"api('/api/open',{{folder:{folder!r}}}).then(()=>api('/api/another',{{}}))")
        pg.wait_for_timeout(800)
        pg.close()
        for scheme in ("dark", "light"):
            pg = page(scheme, TALL)
            shot(pg, f"welcome-{scheme}")
            pg.close()

        # The batch, with categories set.
        pg = page("dark")
        pg.evaluate(f"api('/api/open',{{folder:{folder!r}}}).then(load)")
        pg.wait_for_function("!document.getElementById('uploadBtn').disabled", timeout=60000)
        pg.click("text=All")
        pg.select_option("#ss1", "Nature")
        pg.select_option("#ss2", "Parks/Outdoor")
        pg.click("#ssGroup > button")
        pg.wait_for_timeout(900)
        pg.select_option("#ad", "11")
        pg.click("#adGroup > button")
        pg.wait_for_timeout(900)
        pg.click("text=None")
        cards = pg.locator(".card")
        cards.nth(0).click()
        cards.nth(2).click(modifiers=["Shift"])
        pg.wait_for_timeout(500)
        shot(pg, "review-dark")
        light = page("light")
        shot(light, "review-light")
        light.close()

        pg.click("#checksBtn")
        pg.wait_for_timeout(500)
        shot(pg, "checks-dark")
        pg.click("#panel >> text=Close")
        pg.locator('.card:has-text("DSC_4166") [data-detail]').click()
        pg.wait_for_timeout(400)
        shot(pg, "details-dark")
        pg.click("#panel >> text=Close")
        pg.click("header >> text=Settings")
        pg.wait_for_timeout(300)
        shot(pg, "settings-dark")
        pg.click("#panel >> text=Close")
        pg.click("header >> text=Logins")
        pg.wait_for_timeout(400)
        shot(pg, "logins-dark")
        pg.click("#panel >> text=Close")

        # Uploading: preview, progress, pause, then the site steps at the end.
        pg.click("footer >> text=Upload")
        pg.wait_for_timeout(800)
        shot(pg, "preview-dark")
        pg.click("text=Start upload")
        pg.wait_for_timeout(4000)
        shot(pg, "upload-dark")
        pg.click("#dock >> text=Pause")
        pg.wait_for_timeout(3000)
        shot(pg, "paused-dark")
        pg.click("#dock >> text=Resume")
        pg.wait_for_selector("#panel:has-text('one last step')", timeout=180000)
        pg.wait_for_timeout(500)
        shot(pg, "steps-dark")
        browser.close()
    for s in servers.values():
        s.close()
    server.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)
    if errors:
        sys.exit("Page errors: " + "; ".join(errors))
    print("Screenshots saved in", HERE)


if __name__ == "__main__":
    main()
