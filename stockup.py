#!/usr/bin/env python3
"""
StockUp - send one batch of keyworded photos to several stock agencies.

  python stockup.py "/path/to/batch folder"

Opens a page in your browser where you can:
  * see every photo with the title and keywords already embedded in it
  * mark editorial or creative in bulk
  * see, for each site, what it would reject, and fix it in a click
  * make each site's spreadsheet, and upload to every FTP site at once

Works with just the Python standard library. Optional extras:
  pip install pillow    faster thumbnails, and the fixes that make new copies
                        (sRGB, smaller file, baseline JPEG, resize)
  pip install keyring   keeps your passwords in the system keychain
  pip install paramiko  only if Adobe Stock gave you SFTP details
"""

import configparser
import hashlib
import csv
import ftplib
import json
import math
import random
import os
import re
import shutil
import ssl
import secrets
import struct
import sys
import threading
import time
import unicodedata
import webbrowser
import xml.etree.ElementTree as ET
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from urllib.parse import unquote, urlparse

VERSION = "1.5.1"
REPO = "kefirman9000/stockup"
FROZEN = bool(getattr(sys, "frozen", False))  # running as the Mac/Windows app


def _config_dir():
    if not FROZEN:
        return os.path.dirname(os.path.abspath(__file__))
    if sys.platform == "darwin":
        d = os.path.expanduser("~/Library/Application Support/StockUp")
    elif os.name == "nt":
        d = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "StockUp")
    else:
        d = os.path.join(os.path.expanduser("~"), ".config", "stockup")
    os.makedirs(d, exist_ok=True)
    return d


CONF_DIR = _config_dir()
CONFIG_PATH = os.path.join(CONF_DIR, "stockup_sites.ini")
OPTIONS_PATH = os.path.join(CONF_DIR, "stockup_options.json")
STATE_NAME = "stockup.json"
OUT_NAME = "stockup_output"
UPLOAD_LOG_NAME = ".stockup_uploaded.json"
HISTORY_PATH = os.path.join(CONF_DIR, "upload_history.json")
IMAGE_EXT = (".jpg", ".jpeg")
KEYRING_SERVICE = "StockUp"

# --------------------------------------------------------------------------
# What each site accepts. Sources: each site's contributor help pages.
# min_px: fewest pixels (width x height). max_mb: biggest file. max_px: most pixels.
# --------------------------------------------------------------------------

SITES = {
    "shutterstock": {
        "label": "Shutterstock", "short": "SS",
        "ftp": {"protocol": "ftps", "host": "ftps.shutterstock.com", "port": 21},
        "login_hint": "Your Shutterstock contributor email or username, and password.",
        "min_px": 4_000_000, "min_label": "4 MP", "max_mb": 50, "srgb": True,
        "kw_min": 7, "kw_max": 50, "desc_max": 200, "desc_min_words": 5,
        "safe_names": True, "caption": "ss",
        "csv_note": "On Shutterstock's Submit page, click CSV and choose shutterstock.csv.",
    },
    "adobe": {
        "label": "Adobe Stock", "short": "AD",
        "ftp": {"protocol": "sftp", "host": "sftp.contributor.adobestock.com", "port": 22},
        "login_hint": "Only if Adobe's Upload page shows SFTP details. Copy the username "
                      "and password from there (pip install paramiko if you run the script).",
        "min_px": 3_840_000, "min_label": "4 MP", "max_px": 100_000_000, "max_mb": 45,
        "srgb": True, "kw_min": 5, "kw_max": 49, "title_max": 70, "name_max": 30,
        "editorial_note": True,
        "csv_note": "In Adobe's Uploaded Files > New, click Upload CSV and choose the adobe CSV. "
                    "Tick editorial for the files in editorial_list.txt.",
    },
    "dreamstime": {
        "label": "Dreamstime", "short": "DT",
        "ftp": {"protocol": "ftp", "host": "upload.dreamstime.com", "port": 21},
        "login_hint": "Your Dreamstime user ID (the number shown on Dreamstime's FTP page), "
                      "and your Dreamstime account password.",
        "min_px": 3_000_000, "min_label": "3 MP", "max_mb": 1024, "min_quality": 90,
        "sheet": "dreamstime.csv",
        "csv_note": "After the photos show up in Dreamstime's Unfinished section, click "
                    "\u201cSend spreadsheet\u201d here (or upload dreamstime.csv to Dreamstime's FTP "
                    "yourself). It fills in titles, keywords, categories and the editorial flag.",
    },
    "alamy": {
        "label": "Alamy", "short": "AL",
        "ftp": {"protocol": "ftp", "host": "upload.alamy.com", "port": 21},
        "login_hint": "The email you sign in to Alamy with, and your Alamy password.",
        "min_px": 17_000_000 // 3, "min_label": "17 MB when opened (about 6 MP)",
        "baseline": True,
        "csv_note": "Alamy reads your titles and keywords from the photos. In Alamy Image "
                    "Manager, tick 'Sell for editorial only' for the files in editorial_list.txt.",
    },
    "istock": {
        "label": "iStock", "short": "IS", "ftp": None, "max_mb": 50,
        "csv_note": "In iStock's upload tool, drag the creative folder into a Creative batch "
                    "and the editorial folder into an Editorial batch.",
    },
    "depositphotos": {
        "label": "Depositphotos", "short": "DP",
        "ftp": {"protocol": "ftp", "host": "ftp.depositphotos.com", "port": 21},
        "login_hint": "Your Depositphotos username and password (the same as the website).",
        "min_px": 3_840_000, "min_label": "3.8 MP (2400 x 1600)", "max_mb": 50,
        "kw_min": 8, "kw_max": 50, "desc_max": 250, "safe_names": True, "caption": "ss",
        "csv_note": "In Depositphotos' upload tool, upload depositphotos.csv. It sets the "
                    "editorial flag too.",
    },
    "123rf": {
        "label": "123RF", "short": "RF",
        "ftp": {"protocol": "ftp", "host": "ftp.123rf.com", "port": 21},
        "login_hint": "Your 123RF username and password.",
        "min_px": 6_000_000, "min_label": "6 MP", "max_mb": 30, "kw_min": 7,
        "desc_max": 180, "batch_gb": 4, "caption": "ss",
        "csv_note": "Upload 123rf.csv in 123RF's contributor area. Tick editorial for the "
                    "files in editorial_list.txt.",
    },
    "pond5": {
        "label": "Pond5", "short": "P5",
        "ftp": {"protocol": "ftp", "host": "ftp.pond5.com", "port": 21},
        "login_hint": "Your Pond5 FTP username and FTP password. Set these in Pond5's "
                      "Dashboard > Account > FTP; the password is different from your login.",
        "min_px": 2_000_000, "min_side": 1200, "min_label": "2 MP, 1200 px on each side",
        "max_px": 170_000_000, "srgb": "pond5", "kw_min": 5, "kw_max": 50,
        "title_max": 80, "caption": "p5",
        "csv_note": "In Pond5, go to Uploads > Apply CSV and choose pond5.csv. Pond5 only "
                    "documents its video spreadsheet, so check the first batch carefully.",
    },
}
SITE_ORDER = list(SITES)
DEFAULT_MY_SITES = ["shutterstock", "adobe", "dreamstime", "alamy", "istock"]

SHUTTERSTOCK_CATEGORIES = [
    "Abstract", "Animals/Wildlife", "Arts", "Backgrounds/Textures",
    "Beauty/Fashion", "Buildings/Landmarks", "Business/Finance", "Celebrities",
    "Education", "Food and drink", "Healthcare/Medical", "Holidays",
    "Industrial", "Interiors", "Miscellaneous", "Nature", "Objects",
    "Parks/Outdoor", "People", "Religion", "Science", "Signs/Symbols",
    "Sports/Recreation", "Technology", "Transportation", "Vintage",
]

ADOBE_CATEGORIES = {
    1: "Animals", 2: "Buildings and Architecture", 3: "Business", 4: "Drinks",
    5: "The Environment", 6: "States of Mind", 7: "Food",
    8: "Graphic Resources", 9: "Hobbies and Leisure", 10: "Industry",
    11: "Landscapes", 12: "Lifestyle", 13: "People", 14: "Plants and Flowers",
    15: "Culture and Religion", 16: "Science", 17: "Social Issues",
    18: "Sports", 19: "Technology", 20: "Transport", 21: "Travel",
}

# Dreamstime categories and codes, from Dreamstime's Image_spreadsheet_template.xls
DREAMSTIME_CATEGORIES = [
    ("Abstract -> Aerial", 211), ("Abstract -> Backgrounds", 112), ("Abstract -> Blurs", 39),
    ("Abstract -> Colors", 164), ("Abstract -> Competition", 40),
    ("Abstract -> Craftsmanship", 41), ("Abstract -> Danger", 42),
    ("Abstract -> Exploration", 43), ("Abstract -> Fun", 158), ("Abstract -> Help", 44),
    ("Abstract -> Love", 149), ("Abstract -> Luxury", 45), ("Abstract -> Mobile", 187),
    ("Abstract -> Peace", 46), ("Abstract -> Planetarium", 165), ("Abstract -> Power", 47),
    ("Abstract -> Purity", 48), ("Abstract -> Religion", 128),
    ("Abstract -> Seasonal & Holiday", 155), ("Abstract -> Security", 49),
    ("Abstract -> Sports", 50), ("Abstract -> Stress", 51), ("Abstract -> Success", 52),
    ("Abstract -> Teamwork", 53), ("Abstract -> Textures", 141), ("Abstract -> Unique", 54),
    ("Animals -> Birds", 31), ("Animals -> Farm", 33), ("Animals -> Insects", 36),
    ("Animals -> Mammals", 32), ("Animals -> Marine life", 34), ("Animals -> Pets", 30),
    ("Animals -> Reptiles & Amphibians", 35), ("Animals -> Rodents", 37),
    ("Animals -> Wildlife", 168), ("Arts & Architecture -> Details", 124),
    ("Arts & Architecture -> Generic architecture", 71),
    ("Arts & Architecture -> Historic buildings", 132), ("Arts & Architecture -> Home", 153),
    ("Arts & Architecture -> Indoor", 73), ("Arts & Architecture -> Landmarks", 70),
    ("Arts & Architecture -> Modern buildings", 131),
    ("Arts & Architecture -> Night scenes", 130), ("Arts & Architecture -> Outdoor", 72),
    ("Arts & Architecture -> Ruins & Ancient", 174),
    ("Arts & Architecture -> Work places", 154), ("Business -> Communications", 79),
    ("Business -> Computers", 78), ("Business -> Finance", 80), ("Business -> Industries", 77),
    ("Business -> Metaphors", 83), ("Business -> Objects", 84), ("Business -> People", 75),
    ("Business -> Still-life", 81), ("Business -> Teams", 76),
    ("Business -> Transportation", 82), ("Business -> Travel", 85),
    ("Editorial -> Celebrities", 178), ("Editorial -> Commercial", 185),
    ("Editorial -> Events", 179), ("Editorial -> Landmarks", 184),
    ("Editorial -> People", 180), ("Editorial -> Politics", 181), ("Editorial -> Sports", 182),
    ("Editorial -> Weather & Environment", 183), ("Holidays -> Chinese New Year", 204),
    ("Holidays -> Christmas", 190), ("Holidays -> Cinco de Mayo", 207),
    ("Holidays -> Diwali", 203), ("Holidays -> Easter", 193), ("Holidays -> Fathers Day", 196),
    ("Holidays -> Halloween", 192), ("Holidays -> Hanukkah", 208),
    ("Holidays -> Mardi Gras", 206), ("Holidays -> Mothers Day", 195),
    ("Holidays -> New Years", 189), ("Holidays -> Other", 202), ("Holidays -> Ramadan", 205),
    ("Holidays -> Thanksgiving", 191), ("Holidays -> Valentines Day", 194),
    ("IT & C -> Artificial Intelligence", 210), ("IT & C -> Connectivity", 110),
    ("IT & C -> Equipment", 113), ("IT & C -> Internet", 111), ("IT & C -> Networking", 109),
    ("Illustrations & Clipart -> AI generated", 212),
    ("Illustrations & Clipart -> 3D & Computer generated", 166),
    ("Illustrations & Clipart -> Hand drawn & Artistic", 167),
    ("Illustrations & Clipart -> Illustrations", 163),
    ("Illustrations & Clipart -> Vector", 186), ("Industries -> Agriculture", 101),
    ("Industries -> Architecture", 89), ("Industries -> Banking", 87),
    ("Industries -> Cargo & Shipping", 93), ("Industries -> Communications", 94),
    ("Industries -> Computers", 91), ("Industries -> Construction", 90),
    ("Industries -> Education", 150), ("Industries -> Entertainment", 136),
    ("Industries -> Environment", 99), ("Industries -> Food & Beverages", 127),
    ("Industries -> Healthcare & Medical", 92), ("Industries -> Insurance", 96),
    ("Industries -> Legal", 95), ("Industries -> Manufacturing", 100),
    ("Industries -> Military", 102), ("Industries -> Oil and gas", 161),
    ("Industries -> Power and energy", 97), ("Industries -> Sports", 157),
    ("Industries -> Transportation", 98), ("Industries -> Travel", 88),
    ("Nature -> Clouds and skies", 22), ("Nature -> Deserts", 17), ("Nature -> Details", 14),
    ("Nature -> Fields & Meadows", 27), ("Nature -> Flowers & Gardens", 25),
    ("Nature -> Food ingredients", 28), ("Nature -> Forests", 18),
    ("Nature -> Fruits & Vegetables", 137), ("Nature -> Generic vegetation", 11),
    ("Nature -> Geologic and mineral", 143), ("Nature -> Lakes and rivers", 16),
    ("Nature -> Landscapes", 146), ("Nature -> Mountains", 15),
    ("Nature -> Plants and trees", 12), ("Nature -> Sea & Ocean", 19),
    ("Nature -> Seasons specific", 26), ("Nature -> Sunsets & Sunrises", 23),
    ("Nature -> Tropical", 20), ("Nature -> Water", 171), ("Nature -> Waterfalls", 24),
    ("Objects -> Clothing & Accessories", 142), ("Objects -> Electronics", 147),
    ("Objects -> Home related", 138), ("Objects -> Isolated", 135),
    ("Objects -> Music and sound", 151), ("Objects -> Other", 145), ("Objects -> Retro", 152),
    ("Objects -> Sports", 156), ("Objects -> Still life", 144), ("Objects -> Tools", 140),
    ("Objects -> Toys", 134), ("People -> Active", 123), ("People -> Body parts", 139),
    ("People -> Children", 119), ("People -> Cosmetic & Makeup", 175),
    ("People -> Couples", 115), ("People -> Diversity", 122), ("People -> Expressions", 159),
    ("People -> Families", 118), ("People -> Men", 117), ("People -> Nudes", 173),
    ("People -> Portraits", 162), ("People -> Seniors", 121), ("People -> Teens", 120),
    ("People -> Women", 116), ("People -> Workers", 160), ("Technology -> Computers", 105),
    ("Technology -> Connections", 106), ("Technology -> Electronics", 129),
    ("Technology -> Other", 148), ("Technology -> Retro", 107), ("Technology -> Science", 209),
    ("Technology -> Telecommunications", 104), ("Travel -> Africa", 56),
    ("Travel -> America", 58), ("Travel -> Antarctica", 176),
    ("Travel -> Arts & Architecture", 65), ("Travel -> Asia", 57),
    ("Travel -> Australasian", 60), ("Travel -> Cruise", 62), ("Travel -> Cuisine", 63),
    ("Travel -> Currencies", 67), ("Travel -> Destination scenics", 61),
    ("Travel -> Europe", 59), ("Travel -> Flags", 68), ("Travel -> Resorts", 64),
    ("Travel -> Tropical", 66), ("Web Design Graphics -> Banners", 201),
    ("Web Design Graphics -> Buttons", 200),
    ("Web Design Graphics -> Web Backgrounds & Textures", 199),
    ("Web Design Graphics -> Web Icons", 198),
]
DREAMSTIME_CODES = {c for _, c in DREAMSTIME_CATEGORIES}


# Fixes StockUp can make, with the button label shown to you
FIXES = {
    "shorten_title": "Shorten the title",
    "shorten_desc": "Shorten the description",
    "rename": "Use a safe file name",
    "srgb": "Make an sRGB copy",
    "smaller": "Make a smaller file",
    "resize": "Make a resized copy",
    "baseline": "Make a standard (baseline) JPEG copy",
}
COPY_FIXES = {"srgb", "smaller", "resize", "baseline"}  # these need Pillow

DEFAULT_OPTIONS = {
    "caption_mode": "auto",          # "auto" or "keep"
    "editorial_keyword": "editorial",
    "editorial_label": "",
    "flag_formatted": True,
    "drop_marker_keyword": True,
    "adobe_strip_commas": True,
    "upload_sites": [],
    "my_sites": DEFAULT_MY_SITES,
    # Safety. "untouched": every site gets your exact original file (byte for byte).
    # "adjusted": StockUp may make new copies for upload (fixes, resizing). Originals
    # are never changed either way.
    "photo_mode": "untouched",
    "resize_mode": "off",            # "off", "long_side" (pixels) or "megapixels"
    "resize_value": "",
    "strip_gps": False,              # leave the GPS location out of what's uploaded
    "encrypted_only": False,         # refuse sites that can't do an encrypted connection
    "skip_sent": True,               # skip photos already sent to that site before
    "upload_order": "spread",        # "spread": shuffled, look-alikes kept apart; "folder"
    "upload_pacing": "together",     # "together": all sites at once; "one_by_one"
    "auto_categories": True,         # fill in categories from titles and keywords when empty
    "recent": [],                    # batch folders you opened lately
}


def load_options():
    opts = json.loads(json.dumps(DEFAULT_OPTIONS))
    try:
        with open(OPTIONS_PATH, encoding="utf-8") as fh:
            saved = json.load(fh)
        opts.update({k: v for k, v in saved.items() if k in DEFAULT_OPTIONS})
    except (OSError, ValueError):
        pass
    opts["my_sites"] = [s for s in SITE_ORDER if s in opts["my_sites"]] or list(DEFAULT_MY_SITES)
    return opts


def save_options(new):
    opts = load_options()
    for k, v in new.items():
        if k not in DEFAULT_OPTIONS:
            continue
        want = type(DEFAULT_OPTIONS[k])
        if want is bool:
            v = bool(v)
        elif want is list:
            v = [str(x) for x in v] if isinstance(v, list) else []
        else:
            v = str(v).strip()
        opts[k] = v
    if opts["caption_mode"] not in ("auto", "keep"):
        opts["caption_mode"] = "auto"
    if opts["photo_mode"] not in ("untouched", "adjusted"):
        opts["photo_mode"] = "untouched"
    if opts["resize_mode"] not in ("off", "long_side", "megapixels"):
        opts["resize_mode"] = "off"
    if opts["upload_order"] not in ("spread", "folder"):
        opts["upload_order"] = "spread"
    if opts["upload_pacing"] not in ("together", "one_by_one"):
        opts["upload_pacing"] = "together"
    opts["my_sites"] = [s for s in SITE_ORDER if s in opts["my_sites"]] or list(DEFAULT_MY_SITES)
    with open(OPTIONS_PATH, "w", encoding="utf-8") as fh:
        json.dump(opts, fh, indent=1)
    return opts


def have_pillow():
    try:
        import PIL  # noqa: F401
        return True
    except ImportError:
        return False


# --------------------------------------------------------------------------
# Reading the photos: metadata (XMP, IPTC, EXIF) and image facts - no dependencies
# --------------------------------------------------------------------------

NS = {
    "x": "adobe:ns:meta/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "photoshop": "http://ns.adobe.com/photoshop/1.0/",
    "xmp": "http://ns.adobe.com/xap/1.0/",
}


def jpeg_segments(path):
    """Yield (marker, payload) for the header segments of a JPEG."""
    with open(path, "rb") as f:
        if f.read(2) != b"\xff\xd8":
            return
        while True:
            b = f.read(1)
            while b and b != b"\xff":
                b = f.read(1)
            while b == b"\xff":
                b = f.read(1)
            if not b:
                return
            marker = b[0]
            if marker in (0xD9, 0xDA):
                return
            if 0xD0 <= marker <= 0xD7 or marker == 0x01:
                continue
            ln = f.read(2)
            if len(ln) < 2:
                return
            yield marker, f.read(int.from_bytes(ln, "big") - 2)


def _xmp_value(root, tag):
    prefix, local = tag.split(":")
    full = "{%s}%s" % (NS[prefix], local)
    for desc in root.iter("{%s}Description" % NS["rdf"]):
        if full in desc.attrib:
            return desc.attrib[full].strip()
        el = desc.find(full)
        if el is not None:
            if el.text and el.text.strip():
                return el.text.strip()
            for li in el.iter("{%s}li" % NS["rdf"]):
                if li.text and li.text.strip():
                    return li.text.strip()
    return ""


def parse_xmp(payload):
    data = payload.split(b"\x00", 1)[1] if b"\x00" in payload[:40] else payload
    start = data.find(b"<x:xmpmeta")
    if start < 0:
        start = data.find(b"<rdf:RDF")
    end = data.rfind(b"</x:xmpmeta>")
    if end > 0:
        data = data[start:end + len(b"</x:xmpmeta>")]
    elif start >= 0:
        data = data[start:data.rfind(b"</rdf:RDF>") + len(b"</rdf:RDF>")]
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return {}
    out = {k: _xmp_value(root, t) for k, t in (
        ("title", "dc:title"), ("description", "dc:description"),
        ("city", "photoshop:City"), ("state", "photoshop:State"),
        ("country", "photoshop:Country"), ("date", "photoshop:DateCreated"),
        ("label", "xmp:Label"))}
    kws = []
    for desc in root.iter("{%s}Description" % NS["rdf"]):
        subj = desc.find("{%s}subject" % NS["dc"])
        if subj is not None:
            kws += [li.text.strip() for li in subj.iter("{%s}li" % NS["rdf"])
                    if li.text and li.text.strip()]
    out["keywords"] = kws
    return out


def parse_iptc(payload):
    """APP13 Photoshop IRB -> IPTC-IIM record 2 fields."""
    if not payload.startswith(b"Photoshop 3.0\x00"):
        return {}
    data, iim, i = payload[14:], None, 0
    while i + 12 <= len(data) and data[i:i + 4] == b"8BIM":
        rid = int.from_bytes(data[i + 4:i + 6], "big")
        nlen = data[i + 6]
        j = i + 7 + nlen + ((1 + nlen) % 2)
        size = int.from_bytes(data[j:j + 4], "big")
        if rid == 0x0404:
            iim = data[j + 4:j + 4 + size]
        i = j + 4 + size + (size % 2)
    if not iim:
        return {}
    fields, utf8, k = {}, False, 0
    while k + 5 <= len(iim) and iim[k] == 0x1C:
        rec, ds = iim[k + 1], iim[k + 2]
        size = int.from_bytes(iim[k + 3:k + 5], "big")
        val = iim[k + 5:k + 5 + size]
        k += 5 + size
        if rec == 1 and ds == 90 and b"\x1b%G" in val:
            utf8 = True
        if rec == 2:
            fields.setdefault(ds, []).append(val)

    def dec(b):
        if utf8:
            return b.decode("utf-8", "replace")
        try:
            return b.decode("utf-8")
        except UnicodeDecodeError:
            return b.decode("latin-1")

    def first(ds):
        return dec(fields[ds][0]).strip() if ds in fields else ""

    d = first(55)
    if re.fullmatch(r"\d{8}", d):
        d = "%s-%s-%s" % (d[:4], d[4:6], d[6:])
    return {"title": first(5), "description": first(120),
            "keywords": [dec(v).strip() for v in fields.get(25, []) if v.strip()],
            "city": first(90), "state": first(95), "country": first(101), "date": d}


def parse_exif(payload):
    """(DateTimeOriginal as YYYY-MM-DD or '', embedded thumbnail or None, has GPS location)"""
    if not payload.startswith(b"Exif\x00\x00"):
        return "", None, False
    t = payload[6:]
    if len(t) < 8:
        return "", None, False
    end = "<" if t[:2] == b"II" else ">"

    def u16(o):
        return struct.unpack(end + "H", t[o:o + 2])[0]

    def u32(o):
        return struct.unpack(end + "I", t[o:o + 4])[0]

    def ifd(off):
        tags = {}
        try:
            n = u16(off)
            for e in range(n):
                p = off + 2 + e * 12
                tags[u16(p)] = (u16(p + 2), u32(p + 4), p + 8)
            return tags, u32(off + 2 + n * 12)
        except Exception:
            return {}, 0

    try:
        ifd0, ifd1_off = ifd(u32(4))
        gps = False
        if 0x8825 in ifd0:
            g, _ = ifd(u32(ifd0[0x8825][2]))
            gps = 2 in g or 4 in g  # latitude or longitude present
        date = ""
        if 0x8769 in ifd0:
            sub, _ = ifd(u32(ifd0[0x8769][2]))
            if 0x9003 in sub:
                _, cnt, p = sub[0x9003]
                raw = t[u32(p):u32(p) + cnt].split(b"\x00")[0].decode("ascii", "ignore")
                m = re.match(r"(\d{4}):(\d{2}):(\d{2})", raw)
                if m:
                    date = "-".join(m.groups())
        thumb = None
        if ifd1_off:
            ifd1, _ = ifd(ifd1_off)
            if 0x0201 in ifd1 and 0x0202 in ifd1:
                o, ln = u32(ifd1[0x0201][2]), u32(ifd1[0x0202][2])
                if t[o:o + 2] == b"\xff\xd8":
                    thumb = t[o:o + ln]
        return date, thumb, gps
    except Exception:
        return "", None, False


def icc_info(icc):
    """('sRGB' | 'Adobe RGB' | 'ProPhoto RGB' | 'Display P3' | other name, colour space)"""
    if len(icc) < 132:
        return "", ""
    space = icc[16:20].decode("ascii", "ignore").strip()
    name = ""
    try:
        count = int.from_bytes(icc[128:132], "big")
        for n in range(count):
            p = 132 + n * 12
            sig, off, size = icc[p:p + 4], int.from_bytes(icc[p + 4:p + 8], "big"), \
                int.from_bytes(icc[p + 8:p + 12], "big")
            if sig != b"desc":
                continue
            tag = icc[off:off + size]
            if tag[:4] == b"desc":
                ln = int.from_bytes(tag[8:12], "big")
                name = tag[12:12 + ln].split(b"\x00")[0].decode("latin-1")
            elif tag[:4] == b"mluc":
                # first record: language(2) country(2) length(4) offset(4), from byte 16
                ln, so = int.from_bytes(tag[20:24], "big"), int.from_bytes(tag[24:28], "big")
                name = tag[so:so + ln].decode("utf-16-be", "ignore")
            break
    except Exception:
        pass
    return name.strip(), space


def color_kind(name, space, components):
    if components == 4 or space == "CMYK":
        return "CMYK"
    if components == 1 or space == "GRAY":
        return "Grayscale"
    low = name.lower()
    if not name or "srgb" in low or "61966" in low:
        return "sRGB" if name else "untagged"
    if "adobe rgb" in low or "adobergb" in low or "compatible with adobe" in low:
        return "Adobe RGB"
    if "prophoto" in low or "romm" in low:
        return "ProPhoto RGB"
    if "p3" in low:
        return "Display P3"
    return name


# Standard JPEG luminance table (ITU T.81, Annex K). Its sum tells us how hard a
# photo was compressed, whatever order the camera or app stored the table in.
_STD_LUMA_SUM = sum([
    16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55,
    14, 13, 16, 24, 40, 57, 69, 56, 14, 17, 22, 29, 51, 87, 80, 62,
    18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92,
    49, 64, 78, 87, 103, 121, 120, 101, 72, 92, 95, 98, 112, 100, 103, 99])


def jpeg_quality(dqt):
    """Rough 1-100 JPEG quality from the first luminance table (None if unknown)."""
    i = 0
    while i < len(dqt):
        pq, tq = dqt[i] >> 4, dqt[i] & 0x0F
        size = 128 if pq else 64
        table = dqt[i + 1:i + 1 + size]
        if len(table) < size:
            return None
        vals = [int.from_bytes(table[k:k + 2], "big") for k in range(0, 128, 2)] if pq else list(table)
        if tq == 0:
            return quality_from_table(vals)
        i += 1 + size
    return None


def quality_from_table(vals):
    scale = sum(vals) * 100 / _STD_LUMA_SUM
    q = (200 - scale) / 2 if scale <= 100 else 5000 / scale
    return max(1, min(100, round(q)))


def read_photo(path):
    xmp, iptc, exif_date, thumb = {}, {}, "", None
    info = {"width": 0, "height": 0, "components": 3, "progressive": False, "gps": False}
    icc = b""
    try:
        for marker, payload in jpeg_segments(path):
            if marker == 0xE1 and payload.startswith(b"http://ns.adobe.com/xap/1.0/"):
                xmp = parse_xmp(payload)
                if b"GPSLatitude" in payload or b"GPSLongitude" in payload:
                    info["gps"] = True
            elif marker == 0xE1 and payload.startswith(b"Exif"):
                exif_date, thumb, gps = parse_exif(payload)
                info["gps"] = info["gps"] or gps
            elif marker == 0xED:
                iptc = parse_iptc(payload)
            elif marker == 0xDB and "quality" not in info:
                q = jpeg_quality(payload)
                if q:
                    info["quality"] = q
            elif marker == 0xE2 and payload.startswith(b"ICC_PROFILE\x00"):
                icc += payload[14:]
            elif marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                            0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF) and len(payload) >= 6:
                info["height"] = int.from_bytes(payload[1:3], "big")
                info["width"] = int.from_bytes(payload[3:5], "big")
                info["components"] = payload[5]
                info["progressive"] = marker in (0xC2, 0xC6, 0xCA, 0xCE)
    except OSError:
        pass

    def pick(key):
        return xmp.get(key) or iptc.get(key) or ""

    kws = xmp.get("keywords") or iptc.get("keywords") or []
    seen, clean = set(), []
    for k in kws:
        if k.lower() not in seen:
            seen.add(k.lower())
            clean.append(k)
    name, space = icc_info(icc)
    info["color"] = color_kind(name, space, info["components"])
    info["bytes"] = os.path.getsize(path)
    return {
        "title": pick("title"), "description": pick("description"), "keywords": clean,
        "city": pick("city"), "state": pick("state"), "country": pick("country"),
        "date": (pick("date") or exif_date)[:10], "label": xmp.get("label", ""),
        "info": info,
    }, thumb


# "BOSTON, MASSACHUSETTS - JULY 4, 2026: ..." / "Rome, Italy - 04.09.2026: ..."
_EDITORIAL_RE = re.compile(
    r"^\s*[^:\n]{2,100}?\s[-–—]\s+[^:\n]{0,40}?\b(?:19|20)\d{2}\b[^:\n]{0,12}:\s*\S")


def looks_editorial(text):
    """True if a caption already starts with a location and a dated year."""
    return bool(text and _EDITORIAL_RE.match(text))


def shorten(text, limit):
    """Cut at a word break so it fits, without leaving dangling punctuation."""
    if len(text) <= limit:
        return text
    cut = text[:limit + 1]
    sp = cut.rfind(" ")
    cut = cut[:sp] if sp > limit * 0.6 else text[:limit]
    cut = cut.rstrip(" ,;:-–—")
    small = {"a", "an", "the", "and", "or", "of", "with", "in", "on", "at", "to", "for", "by", "from"}
    words = cut.split(" ")
    while len(words) > 3 and words[-1].lower() in small:  # don't end on "…and a"
        words.pop()
    return " ".join(words).rstrip(" ,;:-–—")


def safe_name(name, max_len=None):
    stem, ext = os.path.splitext(name)
    stem = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode()
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-") or "photo"
    ext = ext.lower() if ext.lower() in IMAGE_EXT else ".jpg"
    if max_len and len(stem) + len(ext) > max_len:
        stem = stem[:max_len - len(ext)].rstrip("._-")
    return stem + ext


def editorial_caption(text, city, region, date, style="ss"):
    """ss: 'CITY, STATE - MONTH D, YYYY: text'   p5: 'CITY, STATE - YYYY/MM/DD: text'"""
    if looks_editorial(text):
        return text
    place = ", ".join(p.upper() for p in (city, region) if p)
    when = ""
    try:
        d = datetime.strptime(date[:10], "%Y-%m-%d")
        when = d.strftime("%Y/%m/%d") if style == "p5" else \
            "%s %d, %d" % (d.strftime("%B").upper(), d.day, d.year)
    except ValueError:
        pass
    prefix = " - ".join(p for p in (place, when) if p)
    return "%s: %s" % (prefix, text) if prefix else text


# --------------------------------------------------------------------------
# A batch: photos, your choices, and the per-site plan
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Picking categories from your titles and keywords
# --------------------------------------------------------------------------

# (words that point to this subject, Shutterstock category, Adobe category,
#  Dreamstime category, Dreamstime editorial category when the photo is editorial)
TOPICS = [
    ("sunset sunrise dusk dawn twilight golden-hour blue-hour afterglow", "Nature", 11, 23, 183),
    ("sky cloud cloudy storm thunderstorm lightning rainbow overcast", "Nature", 11, 22, 183),
    ("mountain peak alpine alps hill hillside valley summit ridge volcano", "Nature", 11, 15, 183),
    ("sea ocean beach coast coastal wave surf shore shoreline bay harbor harbour cliff seaside "
     "lighthouse", "Nature", 11, 19, 183),
    ("lake river pond stream creek reflection lagoon", "Nature", 11, 16, 183),
    ("waterfall cascade rapids", "Nature", 11, 24, 183),
    ("forest woods woodland jungle rainforest pine oak", "Nature", 11, 18, 183),
    ("desert dune sand canyon arid", "Nature", 11, 17, 183),
    ("field meadow grass grassland prairie countryside vineyard wheat pasture rural farmland",
     "Nature", 11, 27, 183),
    ("landscape scenic scenery panorama panoramic vista nature outdoor wilderness", "Nature", 11, 146, 183),
    ("flower flowers bloom blossom garden rose tulip petal bouquet daisy sunflower lavender",
     "Nature", 14, 25, 183),
    ("plant leaf leaves fern moss succulent cactus tree foliage branch", "Nature", 14, 12, 183),
    ("snow winter autumn fall-foliage frost frozen ice icy spring summer seasonal", "Nature", 11, 26, 183),
    ("tropical palm island paradise exotic", "Nature", 21, 20, 183),
    ("water drop droplet splash ripple", "Nature", 5, 171, 183),
    ("night stars star milky-way astronomy moon galaxy astrophotography", "Nature", 11, 130, 183),
    ("aerial drone birds-eye top-view overhead", "Nature", 11, 211, 183),
    ("bird birds eagle owl gull seagull duck parrot heron hawk swan pigeon robin", "Animals/Wildlife", 1, 31, 179),
    ("dog puppy cat kitten pet pets", "Animals/Wildlife", 1, 30, 179),
    ("cow horse sheep goat pig chicken cattle lamb donkey", "Animals/Wildlife", 1, 33, 179),
    ("wildlife deer bear fox wolf lion elephant tiger zebra giraffe monkey squirrel rabbit safari",
     "Animals/Wildlife", 1, 168, 179),
    ("fish whale dolphin shark coral jellyfish seal octopus underwater", "Animals/Wildlife", 1, 34, 179),
    ("insect butterfly bee ant dragonfly spider beetle ladybug moth", "Animals/Wildlife", 1, 36, 179),
    ("snake lizard frog toad crocodile turtle tortoise reptile", "Animals/Wildlife", 1, 35, 179),
    ("food meal dish dinner lunch breakfast cooking cuisine recipe bread cake dessert pizza pasta "
     "salad soup meat cheese baking restaurant", "Food and drink", 7, 127, 185),
    ("fruit vegetable vegetables apple tomato berry berries orange lemon carrot produce banana grape",
     "Food and drink", 7, 137, 185),
    ("drink coffee tea wine beer cocktail juice beverage espresso", "Food and drink", 4, 127, 185),
    ("building skyscraper architecture tower modern facade office-building", "Buildings/Landmarks", 2, 131, 184),
    ("castle church cathedral temple historic palace monastery medieval old-town chapel mosque",
     "Buildings/Landmarks", 2, 132, 184),
    ("ruins ruin ancient archaeology", "Buildings/Landmarks", 2, 174, 184),
    ("landmark monument bridge statue famous memorial", "Buildings/Landmarks", 21, 70, 184),
    ("city street urban downtown skyline cityscape town village square alley", "Buildings/Landmarks", 21, 72, 179),
    ("interior room kitchen living-room bedroom bathroom apartment furniture indoor cozy",
     "Interiors", 2, 73, 185),
    ("home house cottage cabin real-estate", "Buildings/Landmarks", 2, 153, 185),
    ("travel tourism tourist vacation trip destination journey sightseeing", "Parks/Outdoor", 21, 61, 179),
    ("business office meeting corporate entrepreneur startup teamwork workplace coworker",
     "Business/Finance", 3, 75, 185),
    ("money finance bank banking coin cash investment dollar currency economy savings",
     "Business/Finance", 3, 80, 185),
    ("computer laptop code coding programming software keyboard", "Technology", 19, 105, 185),
    ("internet network data cloud-computing online cyber", "Technology", 19, 111, 185),
    ("smartphone phone mobile tablet electronics gadget device", "Technology", 19, 129, 185),
    ("artificial-intelligence ai robot robotics automation", "Technology", 19, 210, 185),
    ("science laboratory lab research microscope chemistry experiment scientist", "Science", 16, 209, 185),
    ("health medical doctor hospital nurse medicine pill healthcare clinic patient", "Healthcare/Medical",
     16, 92, 185),
    ("woman women girl female lady", "People", 13, 116, 180),
    ("man men boy male guy", "People", 13, 117, 180),
    ("child children kid kids baby toddler", "People", 13, 119, 180),
    ("family parents mother father", "People", 13, 118, 180),
    ("couple romance romantic love", "People", 6, 115, 180),
    ("senior elderly grandmother grandfather retired", "People", 13, 121, 180),
    ("teen teenager", "People", 13, 120, 180),
    ("portrait face people person crowd", "People", 13, 162, 180),
    ("happy smile smiling joy sad stress anxiety emotion lonely fear excited", "People", 6, 159, 180),
    ("fashion beauty makeup cosmetic style model dress clothing outfit", "Beauty/Fashion", 12, 175, 185),
    ("sport sports football soccer basketball tennis baseball golf hockey athlete stadium match",
     "Sports/Recreation", 18, 157, 182),
    ("running runner marathon race cycling cyclist swimming gym fitness yoga workout ski skiing "
     "surfing climbing", "Sports/Recreation", 18, 123, 182),
    ("hiking hiker camping park picnic kayak canoe trail fishing adventure", "Parks/Outdoor", 9, 123, 179),
    ("car boat ship sailboat yacht train plane airplane airport bus truck bicycle bike vehicle road "
     "highway traffic transport transportation ferry", "Transportation", 20, 98, 179),
    ("industry factory industrial warehouse manufacturing machinery steel", "Industrial", 10, 100, 185),
    ("construction crane building-site excavator worker", "Industrial", 10, 90, 185),
    ("energy solar wind-turbine power-plant electricity renewable", "Industrial", 10, 97, 183),
    ("oil gas refinery pipeline", "Industrial", 10, 161, 185),
    ("cargo shipping container port freight logistics", "Industrial", 10, 93, 185),
    ("agriculture farming harvest tractor crop farmer", "Industrial", 10, 101, 185),
    ("environment pollution climate recycling ecology sustainability conservation", "Nature", 5, 99, 183),
    ("education school student classroom study learning teacher university book", "Education", 12, 150, 185),
    ("religion religious prayer faith spiritual buddha worship", "Religion", 15, 128, 179),
    ("christmas xmas santa", "Holidays", 15, 190, 179),
    ("halloween pumpkin", "Holidays", 15, 192, 179),
    ("easter", "Holidays", 15, 193, 179),
    ("thanksgiving", "Holidays", 15, 191, 179),
    ("valentine valentines", "Holidays", 6, 194, 179),
    ("new-year fireworks", "Holidays", 15, 189, 179),
    ("holiday celebration festival parade carnival fourth-of-july independence-day", "Holidays", 15, 202, 179),
    ("protest demonstration rally homeless poverty refugee activism", "People", 17, 181, 181),
    ("abstract", "Abstract", 8, 112, 185),
    ("background backdrop wallpaper copy-space", "Backgrounds/Textures", 8, 112, 185),
    ("texture pattern grunge surface", "Backgrounds/Textures", 8, 141, 185),
    ("bokeh blur blurred defocused", "Backgrounds/Textures", 8, 39, 185),
    ("art painting sculpture artwork mural graffiti artist gallery museum", "Arts", 15, 124, 185),
    ("music guitar concert piano musician instrument band", "Arts", 9, 151, 179),
    ("isolated white-background cutout", "Objects", 8, 135, 185),
    ("tool tools hammer wrench", "Objects", 10, 140, 185),
    ("toy toys", "Objects", 9, 134, 185),
    ("still-life object objects product", "Objects", 12, 144, 185),
    ("vintage retro antique nostalgic old-fashioned", "Vintage", 12, 152, 185),
    ("sign symbol icon signage arrow", "Signs/Symbols", 8, 145, 185),
    ("flag flags", "Signs/Symbols", 15, 68, 179),
]

_TOPIC_WORDS = []
for _i, (_words, *_rest) in enumerate(TOPICS):
    for _w in _words.split():
        _TOPIC_WORDS.append((_w.replace("-", " "), _i))
_SINGLE = {}
_PHRASES = []
for _w, _i in _TOPIC_WORDS:
    if " " in _w:
        _PHRASES.append((_w, _i))
    else:
        _SINGLE.setdefault(_w, set()).add(_i)


def _forms(tok):
    yield tok
    if len(tok) > 3 and tok.endswith("ies"):
        yield tok[:-3] + "y"
    if len(tok) > 3 and tok.endswith("es"):
        yield tok[:-2]
    if len(tok) > 3 and tok.endswith("s"):
        yield tok[:-1]


def suggest_categories(title, keywords, editorial=False, description=""):
    """Best guesses for each site's categories, and the words they came from."""
    scores, why = {}, {}

    def score(text, weight):
        low = " " + re.sub(r"[^a-z0-9]+", " ", text.lower()).strip() + " "
        hit = set()
        for phrase, i in _PHRASES:
            if f" {phrase} " in low:
                hit.add((i, phrase))
        for tok in low.split():
            for form in _forms(tok):
                if form in _SINGLE:
                    for i in _SINGLE[form]:
                        hit.add((i, form))
                    break
        for i, word in hit:
            scores[i] = scores.get(i, 0) + weight
            why.setdefault(i, [])
            if word not in why[i]:
                why[i].append(word)

    score(title or "", 3)
    if description and description != title:
        score(description, 1)
    for n, k in enumerate(keywords):
        score(k, 2 if n < 10 else 1)
    if not scores:
        return {"ss": [], "adobe": None, "dt": [], "why": []}

    def best(index, limit, share):
        per = {}
        for i, sc in scores.items():
            per.setdefault(TOPICS[i][index], []).append(sc)
        # the strongest subject counts in full, related ones a little: a photo of a puppy in
        # autumn leaves is about the puppy, even though several nature words appear
        tally = {c: max(v) + 0.3 * (sum(v) - max(v)) for c, v in per.items() if c is not None}
        ranked = sorted(tally.items(), key=lambda kv: -kv[1])
        top = ranked[0][1] if ranked else 0
        return [c for c, sc in ranked[:limit] if sc >= max(2, top * share)]

    ss = best(1, 2, 0.5)
    adobe = (best(2, 1, 0) or [None])[0]
    dt = best(3, 3, 0.4)
    if editorial:
        ed = best(4, 1, 0)
        if ed and ed[0] not in dt:
            dt = (ed + dt)[:3]
    top_topics = sorted(scores, key=lambda i: -scores[i])[:4]
    words = [w for i in top_topics for w in why[i]][:6]
    return {"ss": ss, "adobe": adobe, "dt": dt, "why": words}


# --------------------------------------------------------------------------
# Fingerprints: to protect originals and to spot photos you've sent before
# --------------------------------------------------------------------------

def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def stat_key(path):
    st = os.stat(path)
    return [st.st_size, st.st_mtime_ns]


def picture_hash(path):
    """A 64-bit 'what it looks like' hash, so a re-export still matches (needs Pillow)."""
    try:
        from PIL import Image
        im = Image.open(path)
        im.draft("L", (128, 128))
        im = im.convert("L").resize((9, 8), Image.BILINEAR)
        px = list(im.tobytes())  # one brightness value per pixel
        bits = 0
        for r in range(8):
            for c in range(8):
                bits = (bits << 1) | (px[r * 9 + c] > px[r * 9 + c + 1])
        return "%016x" % bits
    except Exception:
        return ""


_history_lock = threading.Lock()


def load_history():
    try:
        with open(HISTORY_PATH, encoding="utf-8") as fh:
            h = json.load(fh)
        h.setdefault("sha", {})
        h.setdefault("looks", [])
        return h
    except (OSError, ValueError):
        return {"sha": {}, "looks": []}


def record_history(site, folder, f, sha, look):
    with _history_lock:
        h = load_history()
        entry = {"date": datetime.now().strftime("%Y-%m-%d"), "batch": folder, "file": f}
        h["sha"].setdefault(sha, {})[site] = entry
        if look:
            h["looks"].append([look, site, entry["date"], folder, f])
        tmp = HISTORY_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(h, fh)
        os.replace(tmp, HISTORY_PATH)


def _bits_apart(a, b):
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def _name_number(f):
    m = re.search(r"(\d+)(?!.*\d)", os.path.splitext(f)[0])
    return int(m.group(1)) if m else None


def spread_order(files, looks, seed):
    """Shuffle, keeping photos that look alike (or were shot one after another) apart,
    so a site's reviewers don't see a run of near-identical frames."""
    rnd = random.Random(seed)
    parent = {f: f for f in files}

    def find(f):
        while parent[f] != f:
            parent[f] = parent[parent[f]]
            f = parent[f]
        return f

    def join(a, b):
        parent[find(a)] = find(b)

    for i, a in enumerate(files):
        for b in files[i + 1:]:
            la, lb = looks.get(a), looks.get(b)
            if la and lb and _bits_apart(la, lb) <= 12:
                join(a, b)
    if not any(looks.get(f) for f in files):  # no picture hashes: frames 1-3 apart are a series
        nums = sorted((n, f) for f in files if (n := _name_number(f)) is not None)
        for (n1, a), (n2, b) in zip(nums, nums[1:]):
            if n2 - n1 <= 3:
                join(a, b)
    groups = {}
    for f in files:
        groups.setdefault(find(f), []).append(f)
    pools = list(groups.values())
    for g in pools:
        rnd.shuffle(g)
    rnd.shuffle(pools)
    order, recent = [], []
    while any(pools):
        live = [g for g in pools if g]
        far = [g for g in live if id(g) not in recent] or live
        most = max(len(g) for g in far)
        pick = rnd.choice([g for g in far if len(g) == most])  # biggest series first
        order.append(pick.pop())
        recent = (recent + [id(pick)])[-2:]  # not the same series within the next two
    return order


FINGERPRINTS_NAME = ".stockup_fingerprints.json"


class Batch:
    def __init__(self, folder, background=False, only=None):
        self.folder = os.path.abspath(folder)
        self.only = sorted(only) if only else None  # just these photos from the folder
        self.out = os.path.join(self.folder, OUT_NAME)
        self.state_path = os.path.join(self.folder, STATE_NAME)
        self.thumbs, self.meta = {}, {}
        self.lock = threading.RLock()
        self.files = sorted(
            (f for f in os.listdir(self.folder)
             if f.lower().endswith(IMAGE_EXT) and not f.startswith(".")
             and (not only or f in only)),
            key=str.lower)
        self.sha, self.look, self.stat, self.twins = {}, {}, {}, {}
        for f in self.files:  # quick: only reads the start of each file
            p = os.path.join(self.folder, f)
            m, thumb = read_photo(p)
            self.meta[f] = m
            if thumb:
                self.thumbs[f] = thumb
            self.stat[f] = stat_key(p)
        self.scan = {"done": 0, "total": len(self.files), "ready": False}
        self.sent = self._load_sent()
        self.history = load_history()
        self._looks_idx, self._looks, self._looks_cache = None, [], {}
        self.state = self._load_state()
        if background:
            threading.Thread(target=self._fingerprint, daemon=True).start()
        else:
            self._fingerprint()

    def _fingerprint(self):
        """Read every photo in full (in the background): a fingerprint to spot duplicates and
        photos sent before, and a 'looks like' hash. Remembered, so reopening is quick."""
        cache_path = os.path.join(self.folder, FINGERPRINTS_NAME)
        try:
            with open(cache_path, encoding="utf-8") as fh:
                cache = json.load(fh)
        except (OSError, ValueError):
            cache = {}
        fresh = {k: v for k, v in cache.items() if k not in self.stat}  # keep other photos' entries
        for i, f in enumerate(self.files):
            p = os.path.join(self.folder, f)
            c = cache.get(f)
            if c and c[:2] == self.stat[f] and c[2] and (c[3] or not have_pillow()):
                sha, look = c[2], c[3]
            else:
                try:
                    sha, look = file_sha256(p), picture_hash(p)
                except OSError:
                    sha, look = "", ""
            self.sha[f], self.look[f] = sha, look
            fresh[f] = self.stat[f] + [sha, look]
            self.scan["done"] = i + 1
        by_sha = {}
        for f in self.files:
            if self.sha[f]:
                by_sha.setdefault(self.sha[f], []).append(f)
        self.twins = {f: [g for g in by_sha.get(self.sha[f], []) if g != f] for f in self.files}
        try:
            tmp = cache_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(fresh, fh)
            os.replace(tmp, cache_path)
        except OSError:
            pass
        self.scan["ready"] = True

    def _load_sent(self):
        try:
            with open(os.path.join(self.folder, UPLOAD_LOG_NAME), encoding="utf-8") as fh:
                return {k: set(v) for k, v in json.load(fh).items()}
        except (OSError, ValueError):
            return {}

    def changed_originals(self):
        """Originals that changed since StockUp opened them (StockUp never writes to them)."""
        bad = []
        for f in self.files:
            p = os.path.join(self.folder, f)
            try:
                if stat_key(p) != self.stat[f]:
                    bad.append(f)
            except OSError:
                bad.append(f)
        return bad

    def sent_before(self, f, site):
        """(how it matched, entry) if this photo went to this site from another batch."""
        e = self.history["sha"].get(self.sha.get(f), {}).get(site)
        if e and not (e["batch"] == self.folder and e["file"] == f):
            return "exact", e
        look = self.look.get(f)
        if look:
            key = (f, site, look)
            if key not in self._looks_cache:
                self._looks_cache[key] = self._find_look_alike(int(look, 16), site, f)
            hit = self._looks_cache[key]
            if hit:
                return "looks", hit
        return None, None

    def _find_look_alike(self, look, site, f):
        """Photos sent before whose 'looks like' hash is within 4 bits of this one.
        Two hashes that close must share at least 4 of their 8 bytes, so only photos sharing
        a byte in the same place are compared. Fast even with a history of many thousands."""
        index = self._looks_index()
        seen = set()
        for pos in range(8):
            for i in index.get((site, pos, (look >> (8 * pos)) & 0xFF), ()):
                if i in seen:
                    continue
                seen.add(i)
                lk, st, date, folder, name = self._looks[i]
                if bin(look ^ lk).count("1") <= 4 and not (folder == self.folder and name == f):
                    return {"date": date, "batch": folder, "file": name}
        return None

    def _looks_index(self):
        if self._looks_idx is None:
            self._looks, idx = [], {}
            for lk, st, date, folder, name in self.history["looks"]:
                try:
                    v = int(lk, 16)
                except (TypeError, ValueError):
                    continue
                self._looks.append((v, st, date, folder, name))
                for pos in range(8):
                    idx.setdefault((st, pos, (v >> (8 * pos)) & 0xFF), []).append(len(self._looks) - 1)
            self._looks_idx = idx
        return self._looks_idx

    # ---- state -------------------------------------------------------------
    def _load_state(self):
        st = {}
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, encoding="utf-8") as fh:
                    st = json.load(fh).get("files", {})
            except (OSError, ValueError):
                st = {}
        # work saved for photos that aren't open this time is kept as it is
        self._others = {f: v for f, v in st.items() if f not in self.meta}
        opts = load_options()
        for f in self.files:
            m, s = self.meta[f], st.setdefault(f, {})
            if "editorial" not in s:
                s["editorial"] = bool(self.auto_reason(f, opts))
            s.setdefault("ss_cats", [])
            s.setdefault("adobe_cat", None)
            s.setdefault("dt_cats", [])
            s.setdefault("ed_city", m["city"])
            s.setdefault("ed_region", m["country"] or m["state"])
            s.setdefault("ed_date", m["date"])
            s.setdefault("auto_cats", [])  # which of ss/adobe/dt StockUp picked (not you)
            s.setdefault("cat_why", [])
            s.setdefault("fixes", [])   # ["adobe:shorten_title", ...]
            s.setdefault("skip", [])    # sites you chose not to send this photo to
            s.setdefault("force", [])   # sites to send to even though a check says no
        state = {f: st[f] for f in self.files}
        if opts["auto_categories"]:
            for f in self.files:
                self._autofill(f, state[f], overwrite=False)
        return state

    def _autofill(self, f, s, overwrite):
        """Fill categories from the title and keywords. Never replaces a choice you made,
        unless you asked for it (overwrite=True, from the Auto-pick button)."""
        m = self.meta[f]
        sug = suggest_categories(m["title"] or m["description"], m["keywords"], s["editorial"],
                                 m["description"])
        n = 0
        for key, field, val in (("ss", "ss_cats", sug["ss"]), ("adobe", "adobe_cat", sug["adobe"]),
                                ("dt", "dt_cats", sug["dt"])):
            empty = not s[field]
            if val and (overwrite or empty or key in s["auto_cats"]) and s[field] != val:
                s[field] = val
                n += 1
            if val and (overwrite or empty or key in s["auto_cats"]) and key not in s["auto_cats"]:
                s["auto_cats"].append(key)
        if n or overwrite:
            s["cat_why"] = sug["why"]
        return n

    def autopick(self, files):
        n = 0
        with self.lock:
            for f in (files or self.files):
                if f in self.state:
                    self._autofill(f, self.state[f], overwrite=True)
                    n += 1
        self.save()
        return n

    def save(self, incoming=None):
        with self.lock:
            for f, s in (incoming or {}).items():
                if f not in self.state:
                    continue
                cat = s.get("adobe_cat")
                old = dict(self.state[f])
                self.state[f].update({
                    "editorial": bool(s.get("editorial")),
                    "ss_cats": [c for c in s.get("ss_cats", []) if c in SHUTTERSTOCK_CATEGORIES][:2],
                    "adobe_cat": int(cat) if cat and int(cat) in ADOBE_CATEGORIES else None,
                    "dt_cats": [int(c) for c in s.get("dt_cats", [])
                                if str(c).isdigit() and int(c) in DREAMSTIME_CODES][:3],
                    "ed_city": str(s.get("ed_city", "")).strip(),
                    "ed_region": str(s.get("ed_region", "")).strip(),
                    "ed_date": str(s.get("ed_date", "")).strip(),
                })
                cur = self.state[f]
                for key, field in (("ss", "ss_cats"), ("adobe", "adobe_cat"), ("dt", "dt_cats")):
                    if cur[field] != old.get(field) and key in cur["auto_cats"]:
                        cur["auto_cats"].remove(key)  # you chose this one yourself
                if cur["editorial"] != old.get("editorial") and "dt" in cur["auto_cats"]:
                    m = self.meta[f]  # Dreamstime's editorial categories follow the editorial flag
                    cur["dt_cats"] = suggest_categories(m["title"] or m["description"], m["keywords"],
                                                        cur["editorial"], m["description"])["dt"]
            tmp = self.state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"version": VERSION, "files": {**self._others, **self.state}}, fh, indent=1)
            os.replace(tmp, self.state_path)

    def auto_reason(self, f, opts):
        m = self.meta[f]
        kw = opts.get("editorial_keyword", "").strip().lower()
        if kw and kw in (k.lower() for k in m["keywords"]):
            return f'keyword "{kw}"'
        lab = opts.get("editorial_label", "").strip().lower()
        if lab and m.get("label", "").strip().lower() == lab:
            return f"{m['label']} label"
        if opts.get("flag_formatted") and (looks_editorial(m["description"])
                                           or looks_editorial(m["title"])):
            return "caption already starts with a place and date"
        return ""

    def reapply_auto(self):
        opts, n = load_options(), 0
        with self.lock:
            for f in self.files:
                if self.auto_reason(f, opts) and not self.state[f]["editorial"]:
                    self.state[f]["editorial"] = True
                    if "dt" in self.state[f]["auto_cats"]:
                        self._autofill(f, self.state[f], overwrite=False)
                    n += 1
        self.save()
        return n

    def act(self, files, site, code, action):
        """Apply/undo a fix, or skip/include a site, for some photos (all if none given)."""
        files = [f for f in (files or self.files) if f in self.state]
        sites = SITE_ORDER if site == "*" else [site]
        n = 0
        with self.lock:
            for f in files:
                s = self.state[f]
                for st in sites:
                    if st not in SITES:
                        continue
                    issues = self.plan(f, st)["issues"]
                    key = f"{st}:{code}"
                    if action == "apply":
                        avail = [i["fix"] for i in issues if i.get("fix") and not i.get("applied")]
                        todo = avail if code == "*" else [c for c in avail if c == code]
                        for c in todo:
                            if c in COPY_FIXES and (not have_pillow()
                                                    or load_options()["photo_mode"] != "adjusted"):
                                continue
                            s["fixes"].append(f"{st}:{c}")
                            n += 1
                    elif action == "unapply" and key in s["fixes"]:
                        s["fixes"].remove(key)
                        n += 1
                    elif action == "skip":
                        if st not in s["skip"]:
                            s["skip"].append(st)
                            n += 1
                        if st in s["force"]:
                            s["force"].remove(st)
                    elif action == "include":
                        if st in s["skip"]:
                            s["skip"].remove(st)
                            n += 1
                        if any(i["level"] == "block" for i in issues) and st not in s["force"]:
                            s["force"].append(st)
                            n += 1
        self.save()
        return n

    # ---- per-site plan -----------------------------------------------------
    def plan(self, f, site, opts=None):
        """What this photo becomes on one site: text, file name, copies, and issues."""
        opts = opts or load_options()
        rules, m, s = SITES[site], self.meta[f], self.state[f]
        info = m["info"]
        keep = opts["caption_mode"] == "keep"
        applied = {c.split(":", 1)[1] for c in s["fixes"] if c.startswith(site + ":")}
        adjusted = opts["photo_mode"] == "adjusted"
        issues = []

        def issue(level, text, fix=None):
            locked = bool(fix in COPY_FIXES and not adjusted)
            issues.append({"level": level, "text": text, "fix": fix, "locked": locked,
                           "applied": bool(fix and fix in applied and not locked),
                           "needs_pillow": bool(fix in COPY_FIXES and not have_pillow())})

        title = m["title"] or m["description"]
        desc = m["description"] or m["title"]
        marker = opts["editorial_keyword"].strip().lower()
        kws = m["keywords"]
        if marker and opts["drop_marker_keyword"]:
            kws = [k for k in kws if k.lower() != marker]
        px = info["width"] * info["height"]
        mp = px / 1e6
        ops, name, scale = set(), f, 1.0
        sent = f in self.sent.get(site, set())

        # --- sent before?
        if not sent:
            how, e = self.sent_before(f, site)
            if how:
                where = os.path.basename(e["batch"].rstrip("/\\")) or e["batch"]
                what = "This exact photo" if how == "exact" else "A photo that looks the same"
                text = (f"{what} was sent to {rules['label']} on {e['date']} "
                        f"({e['file']} in {where}).")
                if opts["skip_sent"] and how == "exact":
                    issue("block", text + " Skipped so you don't send it twice.")
                else:
                    issue("warn", text + " Check it isn't a duplicate.")
        if self.twins.get(f):
            issue("warn", f"Same file as {', '.join(self.twins[f])} in this batch.")

        # --- the photo itself
        if rules.get("min_px") and px and px < rules["min_px"]:
            issue("block", f"Too small: {mp:.1f} MP. {rules['label']} needs at least "
                           f"{rules['min_label']}. Making it bigger usually gets rejected, so this "
                           f"site is skipped.")
        elif rules.get("min_side") and px and min(info["width"], info["height"]) < rules["min_side"]:
            issue("block", f"Too small: the short side is {min(info['width'], info['height'])} px; "
                           f"{rules['label']} needs {rules['min_side']} px.")
        if rules.get("max_px") and px > rules["max_px"]:
            issue("fix", f"{mp:.0f} MP is over {rules['label']}'s limit of "
                         f"{rules['max_px'] // 1_000_000} MP.", "resize")
        mb = info["bytes"] / 1048576
        if rules.get("max_mb") and mb > rules["max_mb"]:
            issue("fix", f"File is {mb:.0f} MB; {rules['label']} takes up to {rules['max_mb']} MB.",
                  "smaller")
        color = info.get("color", "sRGB")
        if color in ("CMYK", "Grayscale"):
            issue("fix", f"Saved as {color}. Stock sites need RGB.", "srgb")
        elif rules.get("srgb") is True and color not in ("sRGB", "untagged"):
            issue("fix", f"Color profile is {color}. {rules['label']} asks for sRGB, otherwise "
                         f"colors can shift.", "srgb")
        elif rules.get("srgb") == "pond5" and color not in ("sRGB", "untagged", "Adobe RGB"):
            issue("fix", f"Color profile is {color}. Pond5 takes sRGB or Adobe RGB.", "srgb")
        if rules.get("min_quality") and info.get("quality", 100) < rules["min_quality"]:
            issue("warn", f"Saved at about JPEG quality {info['quality']}. {rules['label']} asks "
                          f"for best-quality compression. Re-export at 100% (or 12 in Photoshop); "
                          f"saving this file again can't bring the detail back.")
        if rules.get("baseline") and info.get("progressive"):
            issue("fix", "Saved as a progressive JPEG. Alamy asks for standard (baseline) JPEGs "
                         "to avoid processing errors.", "baseline")

        # --- your resize-for-upload setting (copies only, never below the site's minimum)
        if adjusted and opts["resize_mode"] != "off" and px:
            try:
                val = float(opts["resize_value"])
            except ValueError:
                val = 0
            if val > 0:
                if opts["resize_mode"] == "long_side":
                    k = val / max(info["width"], info["height"])
                else:
                    k = math.sqrt(val * 1e6 / px)
                floor = rules.get("min_px", 0) * 1.02
                if rules.get("min_side"):
                    floor = max(floor, (rules["min_side"] / min(info["width"], info["height"])) ** 2 * px)
                note = ""
                if k < 1 and px * k * k < floor:
                    k = math.sqrt(floor / px)
                    note = f" (kept above {rules['label']}'s minimum)"
                if k * k < 0.9:  # not worth re-saving the photo to shave off under 10%
                    scale = k
                    ops.add("downscale")
                    issue("info", f"Resized for upload to {round(info['width'] * k)} \u00d7 "
                                  f"{round(info['height'] * k)} px{note}, from your Settings. "
                                  f"Your original stays full size.")
        if info.get("gps"):
            if opts["strip_gps"]:
                ops.add("nogps")
                issue("info", "Has a GPS location; it's left out of the uploaded copy. "
                              "Pixels are not changed.")
            else:
                issue("warn", "Has a GPS location (where it was taken), which some sites keep in "
                              "the file buyers download. To leave it out, turn on \u201cRemove GPS "
                              "location\u201d in Settings.")

        # --- file name
        if rules.get("safe_names") and safe_name(f) != f:
            issue("fix", f"File name has characters {rules['label']} can mangle "
                         f"(it would match as {safe_name(f)}).", "rename")
            if "rename" in applied:
                name = safe_name(f)
        if rules.get("name_max") and len(f) > rules["name_max"]:
            issue("fix", f"File name is {len(f)} characters; Adobe's spreadsheet only matches "
                         f"names up to {rules['name_max']}.", "rename")
            if "rename" in applied:
                name = safe_name(f, rules["name_max"])

        # --- words
        if not title:
            issue("warn", "No title or description is saved in this photo.")
        if rules.get("title_max"):
            t = title.replace(",", "") if site == "adobe" and opts["adobe_strip_commas"] \
                and not keep else title
            if len(t) > rules["title_max"]:
                issue("fix", f"Title is {len(t)} characters; {rules['label']} recommends up to "
                             f"{rules['title_max']}. Suggested: “{shorten(t, rules['title_max'])}”",
                      "shorten_title")
                if "shorten_title" in applied:
                    t = shorten(t, rules["title_max"])
            title = t

        if s["editorial"] and rules.get("caption"):
            if looks_editorial(desc):
                pass
            elif keep:
                issue("warn", "Editorial, but the caption doesn't start with a place and date, "
                              "and you chose to keep captions exactly as written.")
            else:
                if not (s["ed_city"] or s["ed_region"]) or not s["ed_date"]:
                    issue("warn", "Editorial captions need a place and date. Add them with "
                                  "“Editorial: where & when”.")
                desc = editorial_caption(desc, s["ed_city"], s["ed_region"], s["ed_date"],
                                         rules["caption"])
        if rules.get("desc_max") and len(desc) > rules["desc_max"]:
            issue("fix", f"Description is {len(desc)} characters; {rules['label']} takes up to "
                         f"{rules['desc_max']}. Suggested: “{shorten(desc, rules['desc_max'])}”",
                  "shorten_desc")
            if "shorten_desc" in applied:
                desc = shorten(desc, rules["desc_max"])
        if rules.get("desc_min_words") and desc and len(desc.split()) < rules["desc_min_words"]:
            issue("warn", f"Description has {len(desc.split())} words; {rules['label']} wants "
                          f"at least {rules['desc_min_words']}.")
        if rules.get("kw_min") and len(kws) < rules["kw_min"]:
            issue("warn", f"{len(kws)} keywords; {rules['label']} needs at least {rules['kw_min']}. "
                          f"Add {rules['kw_min'] - len(kws)} more before you submit there.")
        if rules.get("kw_max") and len(kws) > rules["kw_max"]:
            issue("info", f"{len(kws)} keywords; the first {rules['kw_max']} are sent.")
            kws = kws[:rules["kw_max"]]
        if rules.get("editorial_note") and s["editorial"]:
            issue("warn", "Adobe only accepts illustrative editorial (no news or event photos). "
                          "Skip Adobe for this one unless it fits.")

        if adjusted:
            for c in COPY_FIXES & applied:
                if any(i["fix"] == c for i in issues):
                    ops.add(c)
        if site == "dreamstime" and not s["dt_cats"]:
            issue("warn", "No Dreamstime category chosen yet (for the Dreamstime spreadsheet).")

        blocked = any(i["level"] == "block" for i in issues)
        if sent:
            status = "sent"
        elif site in s["skip"]:
            status = "skip"
        elif blocked and site not in s["force"]:
            status = "skip"
        elif any(i["fix"] and not i["applied"] for i in issues):
            status = "fix"
        elif any(i["level"] == "warn" for i in issues):
            status = "warn"
        else:
            status = "ok"
        return {"status": status, "include": status != "skip", "issues": issues,
                "name": name, "title": title, "desc": desc, "keywords": kws,
                "ops": sorted(ops), "scale": scale, "user_skipped": site in s["skip"],
                "forced": site in s["force"], "sent": sent}

    def items(self):
        opts = load_options()
        out = []
        for f in self.files:
            m = self.meta[f]
            sites = {}
            for st in opts["my_sites"]:
                p = self.plan(f, st, opts)
                sites[st] = {k: p[k] for k in ("status", "issues", "user_skipped", "forced",
                                               "ops")}
            out.append({"file": f, "title": m["title"], "description": m["description"],
                        "mtime": self.stat[f][1],
                        "keywords": m["keywords"], "info": m["info"], "label": m.get("label", ""),
                        "preformatted": looks_editorial(m["description"]) or looks_editorial(m["title"]),
                        "auto": self.auto_reason(f, opts), "sites": sites,
                        "twins": self.twins.get(f, []), **self.state[f]})
        return out

    def thumbnail(self, f):
        if f in self.thumbs and len(self.thumbs[f]) > 2000:
            return self.thumbs[f]
        try:
            from PIL import Image
            im = Image.open(os.path.join(self.folder, f))
            im.draft("RGB", (480, 480))
            im = im.convert("RGB")
            im.thumbnail((480, 480))
            buf = BytesIO()
            im.save(buf, "JPEG", quality=80)
            self.thumbs[f] = buf.getvalue()
            return self.thumbs[f]
        except Exception:
            pass
        if f in self.thumbs:
            return self.thumbs[f]
        with open(os.path.join(self.folder, f), "rb") as fh:
            return fh.read()

    # ---- the file each site actually gets ----------------------------------
    def site_file(self, f, site, plan=None):
        """Path of the file to send to a site: the original, or a fixed copy."""
        plan = plan or self.plan(f, site)
        src = os.path.join(self.folder, f)
        if not plan["ops"] and plan["name"] == f:
            return src
        d = os.path.join(self.out, site, "files")
        os.makedirs(d, exist_ok=True)
        dst = os.path.join(d, plan["name"])
        stamp = "|".join(plan["ops"]) + f"|{plan['scale']:.5f}|{os.stat(src).st_mtime_ns}"
        marker = dst + ".stockup"
        try:
            if os.path.exists(dst) and open(marker).read() == stamp:
                return dst
        except OSError:
            pass
        if plan["ops"]:
            make_copy(src, dst, set(plan["ops"]), SITES[site], plan["scale"])
        else:  # renamed only: an exact byte-for-byte copy
            copy_file(src, dst, replace=True)
        with open(marker, "w") as fh:
            fh.write(stamp)
        return dst


# --------------------------------------------------------------------------
# Making fixed copies (needs Pillow). Your original is never changed.
# --------------------------------------------------------------------------

def _meta_segments(src_bytes):
    """Raw EXIF/XMP/IPTC segments and comments of a JPEG, to carry over to a new copy."""
    segs, i = [], 2
    while i + 4 <= len(src_bytes) and src_bytes[i] == 0xFF:
        marker = src_bytes[i + 1]
        if marker in (0xDA, 0xD9):
            break
        ln = int.from_bytes(src_bytes[i + 2:i + 4], "big")
        if marker in (0xE1, 0xED, 0xFE):  # EXIF + XMP, IPTC, comments
            segs.append(src_bytes[i:i + 2 + ln])
        i += 2 + ln
    return b"".join(segs)


def _insert_segments(jpeg, segs):
    i = 2
    if jpeg[2:4] == b"\xff\xe0":  # keep JFIF first
        i = 4 + int.from_bytes(jpeg[4:6], "big")
    return jpeg[:i] + segs + jpeg[i:]


def strip_gps_bytes(jpeg):
    """Blank out GPS location in EXIF and XMP without touching the image data.
    The file keeps the same size; only the location bytes become zeros/spaces."""
    data = bytearray(jpeg)
    i = 2
    while i + 4 <= len(data) and data[i] == 0xFF:
        marker = data[i + 1]
        if marker in (0xDA, 0xD9):
            break
        ln = int.from_bytes(data[i + 2:i + 4], "big")
        seg_start, seg_end = i + 4, i + 2 + ln
        if marker == 0xE1 and data[seg_start:seg_start + 6] == b"Exif\x00\x00":
            t0 = seg_start + 6
            end = "<" if data[t0:t0 + 2] == b"II" else ">"

            def u16(o):
                return struct.unpack(end + "H", bytes(data[t0 + o:t0 + o + 2]))[0]

            def u32(o):
                return struct.unpack(end + "I", bytes(data[t0 + o:t0 + o + 4]))[0]
            try:
                ifd0 = u32(4)
                for e in range(u16(ifd0)):
                    p = ifd0 + 2 + e * 12
                    if u16(p) == 0x8825:
                        g = u32(p + 8)
                        sizes = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8, 11: 4, 12: 8}
                        n = u16(g)
                        for k in range(n):
                            q = g + 2 + k * 12
                            size = sizes.get(u16(q + 2), 1) * u32(q + 4)
                            if size > 4:
                                off = u32(q + 8)
                                data[t0 + off:t0 + off + size] = bytes(size)
                            data[t0 + q:t0 + q + 12] = bytes(12)
                        data[t0 + g:t0 + g + 2] = bytes(2)  # the GPS block now holds nothing
            except (struct.error, IndexError):
                pass
        elif marker == 0xE1 and bytes(data[seg_start:seg_start + 29]).startswith(b"http://ns.adobe.com/xap/1.0/"):
            seg = bytes(data[seg_start:seg_end])
            seg = re.sub(rb'exif:GPS\w+="[^"]*"', lambda m: b" " * len(m.group(0)), seg)
            seg = re.sub(rb"<exif:GPS(\w+)[^>]*>.*?</exif:GPS\1>", lambda m: b" " * len(m.group(0)),
                         seg, flags=re.S)
            data[seg_start:seg_end] = seg
        i = seg_end
    return bytes(data)


def make_copy(src, dst, ops, rules, scale=1.0):
    raw = open(src, "rb").read()
    if not (ops - {"nogps"}):  # only the GPS location changes: no re-saving, pixels untouched
        data = strip_gps_bytes(raw) if "nogps" in ops else raw
        tmp = dst + ".part"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, dst)
        return
    from PIL import Image, JpegImagePlugin
    im = Image.open(BytesIO(raw))
    icc = im.info.get("icc_profile")
    # Save the copy no worse than the original: the same colour detail (chroma subsampling),
    # and quality 95 or the original's own quality, whichever is higher.
    try:
        sampling = JpegImagePlugin.get_sampling(im)
    except Exception:
        sampling = -1
    sampling = sampling if sampling in (0, 1, 2) else 0
    q_tables = getattr(im, "quantization", None) or {}
    orig_q = quality_from_table(list(q_tables[0])) if 0 in q_tables and len(q_tables[0]) == 64 else 95
    best_q = min(100, max(95, orig_q))

    if "srgb" in ops or im.mode not in ("RGB", "L"):
        from PIL import ImageCms
        srgb = ImageCms.createProfile("sRGB")
        if icc and im.mode in ("RGB", "CMYK", "L"):
            # Relative colorimetric with black point compensation: Photoshop's default,
            # so colours move as little as possible
            intent = getattr(getattr(ImageCms, "Intent", None), "RELATIVE_COLORIMETRIC",
                             getattr(ImageCms, "INTENT_RELATIVE_COLORIMETRIC", 1))
            flags = getattr(getattr(ImageCms, "Flags", None), "BLACKPOINTCOMPENSATION",
                            getattr(ImageCms, "FLAGS", {}).get("BLACKPOINTCOMPENSATION", 0x2000))
            try:
                im = ImageCms.profileToProfile(im, ImageCms.ImageCmsProfile(BytesIO(icc)), srgb,
                                               renderingIntent=intent, outputMode="RGB", flags=flags)
            except Exception:
                im = im.convert("RGB")
        else:
            im = im.convert("RGB")
        out_icc = ImageCms.ImageCmsProfile(srgb).tobytes()
    else:
        out_icc = icc  # RGB stays RGB, black and white stays black and white
    if "downscale" in ops and scale < 1:
        im = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))),
                       Image.LANCZOS)
    if "resize" in ops and rules.get("max_px"):
        px = im.width * im.height
        if px > rules["max_px"]:
            k = math.sqrt(rules["max_px"] / px) * 0.995
            im = im.resize((int(im.width * k), int(im.height * k)), Image.LANCZOS)
    segs = _meta_segments(raw)

    def encode(img, quality, sub):
        buf = BytesIO()
        kw = {"quality": quality, "progressive": False, "optimize": True}
        if img.mode == "RGB":
            kw["subsampling"] = sub
        if out_icc:
            kw["icc_profile"] = out_icc
        img.save(buf, "JPEG", **kw)
        return _insert_segments(buf.getvalue(), segs)

    data = encode(im, best_q, sampling)
    if "smaller" in ops:  # only when a site's file-size limit needs it: step down gently
        limit = rules.get("max_mb", 10_000) * 1048576
        steps = [(q, sampling) for q in (93, 90, 87, 84) if q < best_q] + \
                [(q, 2) for q in (90, 85, 80) if sampling != 2]
        for q, sub in steps:
            if len(data) <= limit:
                break
            data = encode(im, q, sub)
        while len(data) > limit:  # still too big: make it a little smaller
            im = im.resize((int(im.width * 0.9), int(im.height * 0.9)), Image.LANCZOS)
            data = encode(im, 85, 2)
    if "nogps" in ops:
        data = strip_gps_bytes(data)
    tmp = dst + ".part"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, dst)


def copy_file(src, dst, replace=False):
    """A real copy (never a link), so nothing done to it can reach your original."""
    if os.path.exists(dst):
        if not replace:
            return
        os.remove(dst)
    tmp = dst + ".part"
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)


# --------------------------------------------------------------------------
# Making each site's files
# --------------------------------------------------------------------------

def write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, quoting=csv.QUOTE_MINIMAL)
        w.writerow(header)
        w.writerows(rows)


def build_outputs(batch, progress=None):
    opts = load_options()
    out = batch.out
    os.makedirs(out, exist_ok=True)
    summary, report = [], []
    total = len(batch.files) * len(opts["my_sites"])
    done = 0
    for site in opts["my_sites"]:
        rules = SITES[site]
        sd = os.path.join(out, site)
        os.makedirs(sd, exist_ok=True)
        if os.path.isdir(os.path.join(sd, "files")):  # start fresh: no leftover copies
            shutil.rmtree(os.path.join(sd, "files"))
        rows, editorial, included, skipped, problems = [], [], [], [], 0
        for f in batch.files:
            p = batch.plan(f, site, opts)
            done += 1
            if progress:
                progress(done, total, f"{rules['label']}: {f}")
            for i in p["issues"]:
                if i["level"] != "info" and not i["applied"]:
                    report.append(f"[{rules['label']}] {f}: {i['text']}")
                    problems += 1
            if not p["include"]:
                skipped.append(f)
                continue
            batch.site_file(f, site, p)
            included.append(p["name"])
            s = batch.state[f]
            if s["editorial"]:
                editorial.append(p["name"])
            rows.append((f, p, s))

        files = []
        if site == "shutterstock":
            write_csv(os.path.join(sd, "shutterstock.csv"),
                      ["Filename", "Description", "Keywords", "Categories", "Editorial"],
                      [[p["name"], p["desc"], ",".join(p["keywords"]), ",".join(s["ss_cats"]),
                        "Yes" if s["editorial"] else "No"] for f, p, s in rows])
            files.append("shutterstock.csv")
            for f, p, s in rows:
                if not s["ss_cats"]:
                    report.append(f"[Shutterstock] {f}: no category chosen (required).")
        elif site == "adobe":
            name = f"adobe_{datetime.now():%m_%d_%y}.csv"
            write_csv(os.path.join(sd, name), ["Filename", "Title", "Keywords", "Category", "Releases"],
                      [[p["name"], p["title"], ",".join(p["keywords"]), s["adobe_cat"] or "", ""]
                       for f, p, s in rows])
            files.append(name)
        elif site == "depositphotos":
            write_csv(os.path.join(sd, "depositphotos.csv"),
                      ["Filename", "Description", "Keywords", "Editorial", "Nudity", "Releases"],
                      [[p["name"], p["desc"], ",".join(p["keywords"]),
                        "yes" if s["editorial"] else "no", "no", ""] for f, p, s in rows])
            files.append("depositphotos.csv")
        elif site == "dreamstime":
            write_csv(os.path.join(sd, "dreamstime.csv"),
                      ["Filename", "Image Name", "Description", "Category 1", "Category 2",
                       "Category 3", "keywords", "Free", "W-EL", "P-EL", "SR-EL", "SR-Price",
                       "Editorial", "MR doc Ids", "Pr Docs"],
                      [[p["name"], p["title"], p["desc"],
                        *(list(s["dt_cats"]) + [0, 0, 0])[:3], ",".join(p["keywords"]),
                        0, 0, 0, 0, 0, 1 if s["editorial"] else 0, "", ""] for f, p, s in rows])
            files.append("dreamstime.csv")
        elif site == "123rf":
            write_csv(os.path.join(sd, "123rf.csv"),
                      ["oldfilename", "123rf_filename", "description", "keywords", "country"],
                      [[p["name"], "", p["desc"], ",".join(p["keywords"]), ""] for f, p, s in rows])
            files.append("123rf.csv")
            gb = sum(os.path.getsize(batch.site_file(f, site, p)) for f, p, s in rows) / 1024 ** 3
            if gb > rules["batch_gb"]:
                report.append(f"[123RF] This batch is {gb:.1f} GB; 123RF's FTP takes "
                              f"{rules['batch_gb']} GB at a time. Upload it in two parts.")
        elif site == "pond5":
            write_csv(os.path.join(sd, "pond5.csv"),
                      ["originalfilename", "title", "description", "keywords", "city", "region",
                       "country", "editorial"],
                      [[p["name"], p["title"], p["desc"], ",".join(p["keywords"]),
                        s["ed_city"] if s["editorial"] else "", s["ed_region"] if s["editorial"] else "",
                        "", "yes" if s["editorial"] else "no"] for f, p, s in rows])
            files.append("pond5.csv")
        elif site == "istock":
            for sub in ("creative", "editorial"):
                d = os.path.join(sd, sub)
                if os.path.isdir(d):
                    shutil.rmtree(d)
                os.makedirs(d)
            for f, p, s in rows:
                copy_file(batch.site_file(f, site, p),
                          os.path.join(sd, "editorial" if s["editorial"] else "creative", p["name"]))
            files += ["creative/", "editorial/"]
        if site in ("adobe", "dreamstime", "alamy", "123rf"):
            with open(os.path.join(sd, "editorial_list.txt"), "w", encoding="utf-8") as fh:
                fh.write("\n".join(editorial) + ("\n" if editorial else ""))
            files.append("editorial_list.txt")
        if os.path.isdir(os.path.join(sd, "files")):
            files.append("files/  (copies made for this site)")
        summary.append({"site": site, "label": rules["label"], "folder": sd, "files": files,
                        "included": len(included), "skipped": len(skipped),
                        "editorial": len(editorial), "problems": problems,
                        "next": rules["csv_note"]})

    with open(os.path.join(out, "checks.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(report) + "\n" if report else "No problems found.\n")
    return {"folder": out, "sites": summary, "warnings": report, "count": len(batch.files),
            "editorial": sum(1 for f in batch.files if batch.state[f]["editorial"]),
            "changed_originals": batch.changed_originals(),
            "mode": opts["photo_mode"]}


# --------------------------------------------------------------------------
# Logins: usernames in stockup_sites.ini, passwords in the system keychain
# --------------------------------------------------------------------------

def _keyring():
    try:
        import keyring
        kr = keyring.get_keyring()
        name = type(kr).__name__.lower()
        if "fail" in name or "null" in name or "plaintext" in name:
            return None
        return keyring
    except Exception:
        return None


def keychain_name():
    if not _keyring():
        return ""
    return {"darwin": "your Mac's Keychain", "win32": "Windows Credential Manager"}.get(
        sys.platform, "your system keychain")


def _render_ini(values):
    lines = ["# StockUp logins. Usernames are kept here.",
             "# Passwords are kept in " + (keychain_name() or "this file") + ".",
             "# Easiest: use Logins on the StockUp page instead of editing this file.", ""]
    for site in SITE_ORDER:
        rules = SITES[site]
        if not rules["ftp"]:
            continue
        v = values.get(site, {})
        lines += [f"[{site}]", f"# {rules['login_hint']}",
                  f"enabled = {'yes' if v.get('enabled') else 'no'}",
                  f"protocol = {v.get('protocol') or rules['ftp']['protocol']}",
                  f"host = {v.get('host') or rules['ftp']['host']}",
                  f"port = {v.get('port') or rules['ftp']['port']}",
                  f"username = {v.get('username', '')}",
                  f"password = {v.get('password', '')}",
                  f"remote_dir = {v.get('remote_dir') or '/'}", ""]
    tmp = CONFIG_PATH + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # readable by you only
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    os.replace(tmp, CONFIG_PATH)
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass


def _read_ini():
    cp = configparser.ConfigParser(interpolation=None)
    if os.path.exists(CONFIG_PATH):
        cp.read(CONFIG_PATH, encoding="utf-8")
    vals = {}
    for site in SITE_ORDER:
        if not SITES[site]["ftp"]:
            continue
        sec = cp[site] if cp.has_section(site) else {}
        g = (lambda k, d="": sec.get(k, d)) if sec else (lambda k, d="": d)
        vals[site] = {
            "enabled": str(g("enabled", "no")).lower() in ("yes", "true", "1", "on"),
            "protocol": g("protocol", ""), "host": g("host", ""), "port": g("port", ""),
            "username": g("username", ""), "password": g("password", ""),
            "remote_dir": g("remote_dir", "/"),
        }
    return vals


_ini_lock = threading.Lock()


def load_sites():
    """Site logins. Moves any passwords found in the .ini into the keychain."""
    with _ini_lock:
        vals = _read_ini()
        kr = _keyring()
        moved = False
        for site, v in vals.items():
            if kr and v["password"]:
                try:
                    kr.set_password(KEYRING_SERVICE, site, v["password"])
                    v["password"] = ""
                    moved = True
                except Exception:
                    pass
        if moved or not os.path.exists(CONFIG_PATH):
            _render_ini(vals)
    sites = []
    for site, v in vals.items():
        pw = v["password"]
        if not pw and kr:
            try:
                pw = kr.get_password(KEYRING_SERVICE, site) or ""
            except Exception:
                pw = ""
        ftp = SITES[site]["ftp"]
        proto = (v["protocol"] or ftp["protocol"]).lower()
        try:
            port = int(v["port"] or ftp["port"])
        except ValueError:
            port = ftp["port"]
        sites.append({"name": site, "enabled": v["enabled"], "protocol": proto,
                      "host": v["host"] or ftp["host"], "port": port,
                      "username": v["username"], "password": pw,
                      "remote_dir": v["remote_dir"] or "/"})
    return sites


def save_logins(changes):
    """changes: {site: {username, password (blank = keep), remote_dir}}"""
    with _ini_lock:
        vals = _read_ini()
        kr = _keyring()
        for site, c in changes.items():
            if site not in vals:
                continue
            v = vals[site]
            if "username" in c:
                v["username"] = str(c["username"]).strip()
            if "remote_dir" in c and str(c["remote_dir"]).strip():
                v["remote_dir"] = str(c["remote_dir"]).strip()
            if c.get("clear"):
                v["username"] = v["password"] = ""
                if kr:
                    try:
                        kr.delete_password(KEYRING_SERVICE, site)
                    except Exception:
                        pass
            elif c.get("password"):
                if kr:
                    kr.set_password(KEYRING_SERVICE, site, c["password"])
                    v["password"] = ""
                else:
                    v["password"] = c["password"]
            v["enabled"] = bool(v["username"])
        _render_ini(vals)
    return logins_summary()


def logins_summary():
    out = []
    for s in load_sites():
        out.append({"name": s["name"], "label": SITES[s["name"]]["label"],
                    "hint": SITES[s["name"]]["login_hint"], "username": s["username"],
                    "has_password": bool(s["password"]), "remote_dir": s["remote_dir"]})
    return {"sites": out, "keychain": keychain_name(), "config": CONFIG_PATH}


# --------------------------------------------------------------------------
# Uploading
# --------------------------------------------------------------------------

class _TLS(ftplib.FTP_TLS):
    """FTP over TLS that reuses the login session for file transfers. Many servers
    (including stock agencies') refuse transfers without this; plain ftplib doesn't do it."""

    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(conn, server_hostname=self.host,
                                            session=self.sock.session)
        return conn, size


class FTPConn:
    """protocol ftps: always encrypted. protocol ftp: tries encryption first, and falls
    back to plain FTP only if the site doesn't offer it (and you allow that)."""

    def __init__(self, site, timeout=60, encrypted_only=False):
        self.encrypted = False
        tls_error = None
        if site["protocol"] in ("ftps", "ftp"):
            try:
                self.ftp = _TLS(timeout=timeout)
                self.ftp.connect(site["host"], site["port"])
                self.ftp.auth()
                self.ftp.login(site["username"], site["password"])
                self.ftp.prot_p()
                self.ftp.set_pasv(True)
                self._cd(site)
                self.ftp.nlst()  # proves encrypted file transfers work, not just the login
                self.encrypted = True
            except ftplib.error_perm as e:
                if str(e).startswith("530"):  # wrong username or password
                    raise
                tls_error = e
            except (ftplib.Error, ssl.SSLError, OSError, EOFError) as e:
                tls_error = e
            if not self.encrypted:
                try:
                    self.ftp.close()
                except Exception:
                    pass
                if site["protocol"] == "ftps" or encrypted_only:
                    raise RuntimeError(f"{site['host']} wouldn't make an encrypted connection "
                                       f"({tls_error}).")
        if not self.encrypted:
            self.ftp = ftplib.FTP(timeout=timeout)
            self.ftp.connect(site["host"], site["port"])
            self.ftp.login(site["username"], site["password"])
            self.ftp.set_pasv(True)
            self._cd(site)

    def _cd(self, site):
        if site["remote_dir"] not in ("", "/"):
            self.ftp.cwd(site["remote_dir"])

    def listdir(self):
        try:
            return [n.rsplit("/", 1)[-1] for n in self.ftp.nlst()]
        except ftplib.error_perm:
            return []

    def delete(self, name):
        self.ftp.delete(name)

    def put(self, local, name, progress, handed_over=None):
        # Like ftplib's storbinary, plus a signal once every byte has been handed to the
        # network and StockUp is only waiting for the site to confirm it.
        self.ftp.voidcmd("TYPE I")
        with open(local, "rb") as fh, self.ftp.transfercmd("STOR " + name) as conn:
            while True:
                buf = fh.read(256 * 1024)
                if not buf:
                    break
                conn.sendall(buf)
                progress(len(buf))
            if handed_over:
                handed_over()
            if isinstance(conn, ssl.SSLSocket):
                conn.unwrap()
        self.ftp.voidresp()
        try:
            remote = self.ftp.size(name)
            if remote is not None and remote != os.path.getsize(local):
                raise IOError(f"size mismatch after upload ({remote} vs {os.path.getsize(local)})")
        except ftplib.error_perm:
            pass

    def close(self):
        try:
            self.ftp.quit()
        except Exception:
            try:
                self.ftp.close()
            except Exception:
                pass


class SFTPConn:
    encrypted = True

    def __init__(self, site, timeout=60, encrypted_only=False):
        try:
            import paramiko
        except ImportError:
            raise RuntimeError("SFTP needs paramiko: run  pip install paramiko")
        import socket as _socket
        sock = _socket.create_connection((site["host"], site["port"]), timeout=timeout)
        self.t = paramiko.Transport(sock)
        self.t.banner_timeout = timeout
        self.t.auth_timeout = timeout
        self.t.connect(username=site["username"], password=site["password"])
        self.sftp = paramiko.SFTPClient.from_transport(self.t)
        self.sftp.get_channel().settimeout(timeout)  # a stalled transfer gives up instead of hanging
        if site["remote_dir"] not in ("", "/"):
            self.sftp.chdir(site["remote_dir"])

    def listdir(self):
        return self.sftp.listdir()

    def delete(self, name):
        self.sftp.remove(name)

    def put(self, local, name, progress, handed_over=None):
        last = [0]

        def cb(done, total):
            progress(done - last[0])
            last[0] = done
        self.sftp.put(local, name, callback=cb)

    def close(self):
        try:
            self.sftp.close()
            self.t.close()
        except Exception:
            pass


def connect(site, timeout=60):
    if not site["username"] or not site["password"]:
        raise RuntimeError("login not set yet - add it under Logins")
    enc_only = load_options()["encrypted_only"]
    cls = SFTPConn if site["protocol"] == "sftp" else FTPConn
    return cls(site, timeout=timeout, encrypted_only=enc_only)


def connection_label(protocol):
    if protocol == "sftp":
        return "Encrypted (SFTP)"
    if protocol == "ftps":
        return "Encrypted (FTPS)"
    if load_options()["encrypted_only"]:
        return "Encrypted only (skipped if the site can't)"
    return "Tries encryption first; plain FTP if the site doesn't offer it"


def upload_preview(batch, only):
    """Exactly what Upload would send, per site, before anything leaves your computer."""
    opts = load_options()
    logins = {s["name"]: s for s in load_sites()}
    out = []
    for site in only:
        if site not in logins:
            continue
        send, ed, copies, renamed, sent, notsend, size = 0, 0, 0, 0, 0, 0, 0
        for f in batch.files:
            p = batch.plan(f, site, opts)
            if p["sent"]:
                sent += 1
                continue
            if not p["include"]:
                notsend += 1
                continue
            send += 1
            ed += bool(batch.state[f]["editorial"])
            copies += bool(p["ops"])
            renamed += p["name"] != f
            size += os.path.getsize(os.path.join(batch.folder, f))
        out.append({"site": site, "label": SITES[site]["label"], "send": send, "editorial": ed,
                    "copies": copies, "renamed": renamed, "already": sent, "not_sending": notsend,
                    "mb": round(size / 1048576), "host": logins[site]["host"],
                    "connection": connection_label(logins[site]["protocol"])})
    return {"sites": out, "mode": opts["photo_mode"], "strip_gps": opts["strip_gps"],
            "order": opts["upload_order"], "pacing": opts["upload_pacing"]}


def send_sheet(batch, site):
    """Put a site's spreadsheet on its FTP (Dreamstime reads dreamstime.csv from there)."""
    sheet = SITES[site].get("sheet")
    path = os.path.join(batch.out, site, sheet or "")
    if not sheet or not os.path.exists(path):
        raise RuntimeError("Click “Make site files” first.")
    login = next(s for s in load_sites() if s["name"] == site)
    c = connect(login)
    try:
        c.put(path, sheet, lambda n: None)
        enc = c.encrypted
    finally:
        c.close()
    return {"ok": True, "message": f"Sent {sheet} to {SITES[site]['label']}"
                                   f"{' over an encrypted connection' if enc else ''}."}


class Interrupted(Exception):
    """Raised inside a transfer when you press Pause or Stop."""


PARTIAL_NAME = ".stockup_partial.json"


class Uploader:
    def __init__(self, batch):
        self.batch = batch
        self.log_path = os.path.join(batch.folder, UPLOAD_LOG_NAME)
        self.partial_path = os.path.join(batch.folder, PARTIAL_NAME)
        self.receipt_path = os.path.join(batch.out, "upload_receipt.csv")
        self.lock = threading.Lock()
        self.progress = {}
        self.running = False
        self.stop_requested = False
        self.paused = False

    # ---- records ------------------------------------------------------------
    def _load_log(self):
        try:
            with open(self.log_path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def _partials(self):
        """Files StockUp started sending but didn't finish: ours to replace, never someone else's."""
        try:
            with open(self.partial_path, encoding="utf-8") as fh:
                return {k: set(v) for k, v in json.load(fh).items()}
        except (OSError, ValueError):
            return {}

    def _set_partial(self, site, name, unfinished):
        with self.lock:
            p = self._partials()
            names = p.setdefault(site, set())
            (names.add if unfinished else names.discard)(name)
            tmp = self.partial_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({k: sorted(v) for k, v in p.items() if v}, fh, indent=1)
            os.replace(tmp, self.partial_path)

    def _mark_done(self, site, f, sent_as, local, encrypted):
        with self.lock:
            log = self._load_log()
            log.setdefault(site, [])
            if f not in log[site]:
                log[site].append(f)
            tmp = self.log_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(log, fh, indent=1)
            os.replace(tmp, self.log_path)
            self.batch.sent.setdefault(site, set()).add(f)
            os.makedirs(self.batch.out, exist_ok=True)
            new = not os.path.exists(self.receipt_path)
            with open(self.receipt_path, "a", encoding="utf-8", newline="") as fh:
                w = csv.writer(fh)
                if new:
                    w.writerow(["time", "site", "photo", "sent as", "bytes", "exact original",
                                "encrypted", "sha256 of what was sent"])
                same = os.path.abspath(local) == os.path.join(self.batch.folder, f)
                w.writerow([datetime.now().isoformat(timespec="seconds"), SITES[site]["label"], f,
                            sent_as, os.path.getsize(local), "yes" if same else "no (copy)",
                            "yes" if encrypted else "no",
                            self.batch.sha[f] if same else file_sha256(local)])
        self._set_partial(site, sent_as, False)
        record_history(site, self.batch.folder, f, self.batch.sha[f], self.batch.look.get(f, ""))

    # ---- controls -----------------------------------------------------------
    def status(self):
        with self.lock:
            return {"running": self.running, "paused": self.paused and self.running,
                    "stopping": self.stop_requested and self.running,
                    "sites": json.loads(json.dumps(self.progress))}

    def stop(self):
        self.stop_requested = True
        self.paused = False

    def pause(self, on):
        if self.running:
            self.paused = bool(on)

    def _check(self):
        if self.stop_requested or self.paused:
            raise Interrupted()

    # ---- running ------------------------------------------------------------
    def start(self, only=None):
        if self.running:
            return False
        if only:
            sites = [s for s in load_sites() if s["name"] in only]
        else:
            sites = [s for s in load_sites() if s["enabled"]]
        if not sites:
            return False
        self.stop_requested = False
        self.paused = False
        with self.lock:
            self.running = True
            self.progress = {}
            for s in sites:
                plans = {f: self.batch.plan(f, s["name"]) for f in self.batch.files}
                todo = [f for f in self.batch.files if plans[f]["include"]]
                done = [f for f in todo if plans[f]["sent"]]
                size = sum(os.path.getsize(os.path.join(self.batch.folder, f)) for f in todo)
                self.progress[s["name"]] = {
                    "state": "waiting", "done": len(done), "total": len(todo),
                    "bytes": sum(os.path.getsize(os.path.join(self.batch.folder, f)) for f in done),
                    "total_bytes": size or 1, "current": "", "errors": [], "notes": [],
                    "skipped": len(done), "left_out": len(self.batch.files) - len(todo),
                    "encrypted": None}
        if load_options()["upload_pacing"] == "one_by_one":
            def in_turn():
                for s in sites:
                    if self.stop_requested:
                        self._set(s["name"], state="stopped")
                        continue
                    self._run_site(s)
            threads = [threading.Thread(target=in_turn, daemon=True)]
        else:
            threads = [threading.Thread(target=self._run_site, args=(s,), daemon=True)
                       for s in sites]
        for t in threads:
            t.start()

        def waiter():
            for t in threads:
                t.join()
            changed = self.batch.changed_originals()
            with self.lock:
                if changed:
                    for p in self.progress.values():
                        p["errors"].append("These originals changed while StockUp was open "
                                           "(StockUp never writes to them, so another app did): "
                                           + ", ".join(changed))
                self.running = False
                self.paused = False
        threading.Thread(target=waiter, daemon=True).start()
        return True

    def _set(self, site, **kw):
        with self.lock:
            self.progress[site].update(kw)

    def _err(self, site, msg):
        with self.lock:
            self.progress[site]["errors"].append(msg)

    def _note(self, site, msg):
        with self.lock:
            self.progress[site]["notes"].append(msg)

    def _remove_partial(self, site, remote_name):
        """After an interrupted transfer: delete the half-sent file so the site never gets it."""
        label = SITES[site["name"]]["label"]
        for attempt in range(2):
            try:
                c = connect(site)
                try:
                    if remote_name in c.listdir():
                        c.delete(remote_name)
                finally:
                    c.close()
                self._set_partial(site["name"], remote_name, False)
                return True
            except Exception as e:
                last = e
                time.sleep(1)
        self._err(site["name"], f"{remote_name}: StockUp couldn't remove the half-sent file from "
                                f"{label} ({last}). It will replace it next time you upload; or "
                                f"delete it on {label}'s site.")
        return False

    def _wait_while_paused(self, name):
        if not self.paused:
            return
        self._set(name, state="paused", current="")
        while self.paused and not self.stop_requested:
            time.sleep(0.3)

    def _run_site(self, site):
        name = site["name"]
        opts = load_options()
        todo = [f for f in self.batch.files if self.batch.plan(f, name)["include"]
                and f not in self.batch.sent.get(name, set())]
        if opts["upload_order"] == "spread":
            todo = spread_order(todo, self.batch.look,
                                f"{self.batch.folder}|{name}|{datetime.now():%Y-%m-%d}")
        conn, failures_in_row, last, on_server = None, 0, "", set()
        ours = self._partials().get(name, set())  # half-sent earlier by StockUp: safe to replace
        sent_now = {self.batch.sha.get(f) for f in self.batch.sent.get(name, set())}

        def hang_up():
            nonlocal conn
            if conn:
                conn.close()
            conn = None

        for f in todo:
            self._wait_while_paused(name)
            if self.stop_requested:
                hang_up()
                self._set(name, state="stopped", current="")
                return
            if opts["skip_sent"] and self.batch.sha.get(f) in sent_now:
                self._note(name, f"{f} is the same file as one already sent to "
                                 f"{SITES[name]['label']}, so it was skipped.")
                continue
            try:
                plan = self.batch.plan(f, name)
                local = self.batch.site_file(f, name, plan)
            except Exception as e:
                self._err(name, f"{f}: couldn't prepare the file ({e})")
                continue
            remote = plan["name"]
            ok, skipped, attempt = False, False, 0
            while attempt < 3:
                start_bytes = self.progress[name]["bytes"]
                started = False
                try:
                    self._check()
                    if conn is None:
                        self._set(name, state="connecting")
                        conn = connect(site)
                        on_server = set(conn.listdir())
                        self._set(name, encrypted=conn.encrypted)
                    if remote in on_server and remote not in ours:
                        self._note(name, f"{remote} is already on the {SITES[name]['label']} "
                                         f"server, so it wasn't sent again or overwritten.")
                        skipped = True
                        break
                    self._set(name, state="uploading", current=f)

                    def prog(n, _name=name):
                        with self.lock:
                            self.progress[_name]["bytes"] += n
                        self._check()  # Pause / Stop take effect mid-photo
                    self._set_partial(name, remote, True)
                    ours.add(remote)
                    started = True
                    conn.put(local, remote, prog,
                             lambda _name=name: self._set(_name, state="finishing"))
                    on_server.add(remote)
                    sent_now.add(self.batch.sha.get(f))
                    ok = True
                    break
                except Interrupted:
                    self._set(name, bytes=start_bytes)
                    hang_up()
                    if started:
                        self._set(name, state="tidying up", current=f)
                        if self._remove_partial(site, remote):
                            ours.discard(remote)
                            self._note(name, f"{remote}: interrupted part-way; the half-sent file "
                                             f"was removed from the server.")
                    if self.stop_requested:
                        self._set(name, state="stopped", current="")
                        return
                    self._wait_while_paused(name)  # then send this photo again from the start
                    if self.stop_requested:
                        self._set(name, state="stopped", current="")
                        return
                    continue
                except Exception as e:
                    self._set(name, bytes=start_bytes)
                    hang_up()
                    last = str(e) or e.__class__.__name__
                    low = last.lower()
                    if "530" in last or "login" in low or "auth" in low or "paramiko" in low \
                            or "encrypted connection" in low:
                        self._set(name, state="error", current="")
                        self._err(name, f"Couldn't connect: {last}")
                        return
                    attempt += 1
                    time.sleep(2 * attempt)
            if ok:
                failures_in_row = 0
                self._mark_done(name, f, remote, local, conn.encrypted if conn else False)
                ours.discard(remote)
                with self.lock:
                    self.progress[name]["done"] += 1
            elif not skipped:
                failures_in_row += 1
                self._err(name, f"{f}: {last}")
                if remote in self._partials().get(name, set()):  # never leave half a file behind
                    self._remove_partial(site, remote)
                if failures_in_row >= 5:
                    self._set(name, state="error", current="")
                    self._err(name, "Stopped after 5 failures in a row.")
                    hang_up()
                    return
        hang_up()
        errs = self.progress[name]["errors"]
        self._set(name, state="finished" if not errs else "finished with errors", current="")


def sites_summary():
    opts = load_options()
    chosen = opts["upload_sites"]
    out = []
    for s in load_sites():
        if s["name"] not in opts["my_sites"]:
            continue
        ready = bool(s["username"] and s["password"])
        out.append({"name": s["name"], "label": SITES[s["name"]]["label"], "ready": ready,
                    "checked": ready and ((s["name"] in chosen) if chosen else True)})
    return out


def test_sites():
    """Check every login at the same time, giving each site up to 15 seconds."""
    my = load_options()["my_sites"]
    sites = [s for s in load_sites() if s["name"] in my]
    results = [{"name": s["name"], "label": SITES[s["name"]]["label"]} for s in sites]

    def check(s, r):
        if not s["username"]:
            r["result"] = "No login yet"
            return
        try:
            c = connect(s, timeout=15)
            r["listing"] = c.listdir()[:30]
            r["encrypted"] = c.encrypted
            c.close()
            r["result"] = "OK"
        except Exception as e:
            r["result"] = f"Couldn't connect: {e}"
    threads = [threading.Thread(target=check, args=(s, r), daemon=True) for s, r in zip(sites, results)]
    for t in threads:
        t.start()
    for t, r in zip(threads, results):
        t.join(40)
        r.setdefault("result", "Couldn't connect: no answer from the site")
    return results


# --------------------------------------------------------------------------
# Updates from GitHub releases
# --------------------------------------------------------------------------

def _vtuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)


def _http_get(url, timeout=15):
    import urllib.request
    req = urllib.request.Request(url, headers={
        "User-Agent": f"StockUp/{VERSION}", "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception as e:
        msg = str(e)
        if "CERTIFICATE_VERIFY_FAILED" in msg:
            raise RuntimeError("Your Python can't verify secure connections. On a Mac, open "
                               "Applications > Python 3.x and double-click "
                               "'Install Certificates.command', then try again.")
        raise RuntimeError(f"Couldn't reach GitHub ({msg}).")


APP_ASSET = {"darwin": "StockUp-Mac.zip", "win32": "StockUp-Windows.zip"}.get(sys.platform, "")
_update_cache = {"t": 0, "data": None}


def check_update(force=False):
    if not force and _update_cache["data"] and time.time() - _update_cache["t"] < 3600:
        return _update_cache["data"]
    rel = json.loads(_http_get(f"https://api.github.com/repos/{REPO}/releases/latest"))
    latest = rel.get("tag_name", "").lstrip("v")
    assets = {a.get("name"): a.get("browser_download_url") for a in rel.get("assets", [])}
    data = {"current": VERSION, "latest": latest, "frozen": FROZEN,
            "available": bool(latest) and _vtuple(latest) > _vtuple(VERSION),
            "notes": (rel.get("body") or "").strip()[:1500],
            "url": rel.get("html_url", ""), "asset": assets.get("stockup.py"),
            "download": assets.get(APP_ASSET) or rel.get("html_url", ""),
            "tag": rel.get("tag_name", "")}
    _update_cache.update(t=time.time(), data=data)
    return data


def install_update():
    """Download the latest stockup.py, check it, keep a backup, swap it in."""
    if FROZEN:
        raise RuntimeError("The app updates by downloading the new version from GitHub.")
    info = check_update(force=True)
    if not info["available"]:
        return {"updated": False, "message": f"You already have the latest version ({VERSION})."}
    url = info["asset"] or f"https://raw.githubusercontent.com/{REPO}/{info['tag']}/stockup.py"
    src = _http_get(url, timeout=60)
    text = src.decode("utf-8")
    try:
        compile(text, "stockup.py", "exec")
    except SyntaxError:
        raise RuntimeError("The download looks damaged. Nothing was changed; try again later.")
    m = re.search(r'^VERSION = "([^"]+)"', text, re.M)
    if not m or _vtuple(m.group(1)) != _vtuple(info["latest"]):
        raise RuntimeError("The downloaded file doesn't match the release. Nothing was changed.")
    me = os.path.abspath(__file__)
    shutil.copy2(me, me + ".bak")
    tmp = me + ".new"
    with open(tmp, "wb") as fh:
        fh.write(src)
    os.replace(tmp, me)
    return {"updated": True, "version": info["latest"],
            "message": f"Updated to {info['latest']}. Your previous version is saved as stockup.py.bak."}


def restart_program(server, folder):
    """Restart in place. With a folder: same page reconnects. Without: pick a new folder."""
    def go():
        time.sleep(0.8)
        server.shutdown()
        server.server_close()
        env = dict(os.environ)
        args = [sys.executable] + ([] if FROZEN else [os.path.abspath(__file__)])
        if folder:
            env["STOCKUP_PORT"] = str(server.server_address[1])
            args.append(folder)
        if os.name == "nt":
            import subprocess
            subprocess.Popen(args, env=env)
            os._exit(0)
        os.environ.clear()
        os.environ.update(env)
        os.execv(sys.executable, args)
    threading.Thread(target=go, daemon=True).start()


# --------------------------------------------------------------------------
# The page
# --------------------------------------------------------------------------

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>StockUp</title>
<script>try{var t=localStorage.getItem('stockup-theme');if(t==='light'||t==='dark')document.documentElement.dataset.theme=t}catch(e){}</script>
<style>
:root{--bg:#f4f3ef;--panel:#ffffff;--ink:#1d1d1b;--mute:#6b6a65;--line:#dedcd5;
--accent:#1f5f8b;--on-accent:#ffffff;--ed:#b4441c;--ed-bg:#fbeee8;--ok:#2e7d4f;--warn:#9a6a00;--warn-bg:#fff4d6;--thumb:#e6e4dd;--shadow:#0000002a;color-scheme:light}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#121211;--panel:#1d1d1b;--ink:#ecebe6;
--mute:#9a9891;--line:#34332e;--accent:#6aa9d8;--on-accent:#0d1b26;--ed:#f08a5d;--ed-bg:#33211a;--ok:#6cc58f;--warn:#e8b84a;--warn-bg:#352a10;--thumb:#2a2a27;--shadow:#00000080;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#121211;--panel:#1d1d1b;--ink:#ecebe6;--mute:#9a9891;--line:#34332e;
--accent:#6aa9d8;--on-accent:#0d1b26;--ed:#f08a5d;--ed-bg:#33211a;--ok:#6cc58f;--warn:#e8b84a;--warn-bg:#352a10;--thumb:#2a2a27;--shadow:#00000080;color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;background:var(--bg);color:var(--ink)}
header{position:sticky;top:0;z-index:5;background:var(--panel);border-bottom:1px solid var(--line);padding:10px 16px}
.top{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
h1{font-size:18px;margin:0}
.sub{color:var(--mute);font-size:13px;flex:1;min-width:120px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:8px}
.group{display:flex;flex-wrap:wrap;gap:6px;align-items:center;padding:4px 8px;border:1px solid var(--line);border-radius:8px}
.group b{font-size:12px;color:var(--mute);font-weight:600}
button,select,input{font:inherit;color:var(--ink);background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:5px 9px}
button{cursor:pointer}button:hover{border-color:var(--accent)}button:disabled{opacity:.5;cursor:default}
button.primary{background:var(--accent);color:var(--on-accent);border-color:var(--accent);font-weight:600}
button.edbtn{border-color:var(--ed);color:var(--ed)}
button.small{padding:2px 8px;font-size:12px}
button.link{border:none;background:none;color:var(--accent);padding:2px 4px}
input{width:120px}input[type=date]{width:150px}
#dtGroup select{max-width:200px}
input[type=checkbox],input[type=radio]{width:auto;padding:0;margin:0}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:6px;overflow:hidden}
.seg button{border:none;border-radius:0;padding:4px 9px;font-size:12px}
.seg button.on{background:var(--accent);color:var(--on-accent)}
.count{display:inline-block;min-width:18px;padding:0 5px;border-radius:9px;background:var(--warn);color:var(--panel);font-size:11px;font-weight:700;margin-left:4px}
#welcome{display:none;max-width:860px;margin:28px auto 60px;padding:0 16px}
body.nobatch #welcome{display:block}
body.nobatch header .bar,body.nobatch main,body.nobatch footer,body.nobatch #checksBtn,
body.nobatch #anotherBtn,body.nobatch #mode,body.nobatch #scanbar{display:none!important}
#dropzone{border:2px dashed var(--line);border-radius:16px;padding:44px 20px;text-align:center;background:var(--panel)}
#dropzone h2{margin:0 0 6px;font-size:22px}
#dropzone .hint{max-width:560px;margin:0 auto 18px}
.choices{display:flex;gap:10px;justify-content:center;flex-wrap:wrap}
.pathrow{display:flex;gap:6px;justify-content:center;margin-top:14px;flex-wrap:wrap}
.pathrow input{width:min(420px,100%)}
#recent h3{font-size:15px;margin:26px 0 8px}
.rec{display:flex;justify-content:space-between;gap:12px;align-items:center;padding:10px 12px;border:1px solid var(--line);border-radius:10px;background:var(--panel);margin-bottom:8px;cursor:pointer}
.rec:hover{border-color:var(--accent)}
.rec .path{font-size:12px;color:var(--mute);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:520px}
#droplay{position:fixed;inset:0;z-index:20;display:none;align-items:center;justify-content:center;background:color-mix(in srgb,var(--bg) 82%,transparent)}
#droplay div{border:3px dashed var(--accent);border-radius:18px;padding:40px 60px;background:var(--panel);font-size:20px;font-weight:600;text-align:center}
#droplay small{display:block;font-size:13px;font-weight:400;color:var(--mute);margin-top:6px}
#busy{position:fixed;inset:0;z-index:21;display:none;align-items:center;justify-content:center;background:color-mix(in srgb,var(--bg) 70%,transparent)}
#busy .card2{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:18px 22px;width:min(440px,calc(100% - 32px));box-shadow:0 8px 30px var(--shadow)}
#scanbar{display:none;margin-top:8px;font-size:12px;color:var(--mute)}
#scanbar .prog{height:4px;margin-top:4px}
#banner{display:none;margin-top:8px;padding:7px 10px;border-radius:8px;background:var(--warn-bg);font-size:13px}
main{padding:14px 16px 130px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px}
.card{background:var(--panel);border:2px solid var(--line);border-radius:10px;overflow:hidden;cursor:pointer;user-select:none;position:relative}
.card.sel{border-color:var(--accent);box-shadow:0 0 0 2px var(--accent)}
.card.ed{background:var(--ed-bg)}
.thumb{aspect-ratio:3/2;background:var(--thumb);display:block;width:100%;object-fit:cover}
.meta{padding:7px 9px}
.fn{font-size:11px;color:var(--mute);display:flex;justify-content:space-between;gap:6px}
.fn span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.fn span+span{flex-shrink:0}
.tt{font-weight:600;font-size:13px;margin:2px 0 5px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.tags{display:flex;flex-wrap:wrap;gap:4px;font-size:11px}
.tag{padding:1px 7px;border-radius:9px;border:1px solid var(--line);color:var(--mute)}
.tag.ed{background:var(--ed);border-color:var(--ed);color:var(--panel);font-weight:700}
.tag.ok{border-color:var(--ok);color:var(--ok)}
.tag.warn{border-color:var(--ed);color:var(--ed)}
.chips{display:flex;flex-wrap:wrap;gap:3px;margin-top:6px}
.chip{font-size:10.5px;font-weight:700;padding:1px 5px;border-radius:5px;border:1px solid var(--line);letter-spacing:.02em}
.chip.ok{color:var(--ok);border-color:var(--ok)}
.chip.warn{color:var(--warn);border-color:var(--warn)}
.chip.fix{background:var(--warn);border-color:var(--warn);color:var(--panel)}
.chip.skip{color:var(--mute);text-decoration:line-through;opacity:.75}
.auto{font-style:normal;font-size:9.5px;font-weight:700;letter-spacing:.03em;text-transform:uppercase;color:var(--accent);margin-left:3px}
.chip.sent{background:var(--ok);border-color:var(--ok);color:var(--panel)}
.mode{font-size:11px;font-weight:600;padding:2px 8px;border-radius:9px;border:1px solid var(--ok);color:var(--ok);white-space:nowrap;cursor:pointer}
.mode.adj{border-color:var(--warn);color:var(--warn)}
#dock{position:fixed;left:16px;bottom:64px;width:min(380px,calc(100% - 32px));display:none;flex-direction:column;gap:8px;z-index:8}
.pcard{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:9px 12px;box-shadow:0 6px 20px var(--shadow);font-size:13px}
.pcard .row1{display:flex;justify-content:space-between;gap:8px;align-items:baseline}
.pcard .big{height:10px}
.pcard.done .prog i{background:var(--ok)}.pcard.bad .prog i{background:var(--ed)}
.lock{font-size:11px;color:var(--ok)}.lock.no{color:var(--warn)}
.dockbar{display:flex;gap:6px;justify-content:flex-end}
table.pv{border-collapse:collapse;width:100%;font-size:13px;margin:6px 0}
table.pv td,table.pv th{padding:4px 6px;border-top:1px solid var(--line);text-align:left;vertical-align:top}
table.pv th{font-size:12px;color:var(--mute);font-weight:600}
.ok{color:var(--ok)}
.check{position:absolute;top:6px;left:6px;width:22px;height:22px;border-radius:50%;background:#ffffffd9;border:2px solid #00000059;display:flex;align-items:center;justify-content:center;font-size:13px;color:#fff}
.card.sel .check{background:var(--accent);border-color:var(--accent);color:var(--on-accent)}
footer{position:fixed;bottom:0;left:0;right:0;background:var(--panel);border-top:1px solid var(--line);padding:9px 16px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
#msg{color:var(--mute);flex:1;min-width:200px;font-size:13px}
.sites{display:flex;gap:10px;flex-wrap:wrap;align-items:center;font-size:13px}
.sites label{display:flex;gap:4px;align-items:center;cursor:pointer}
.sites label.off{color:var(--mute);cursor:default}
#panel{position:fixed;right:16px;bottom:72px;width:min(560px,calc(100% - 32px));max-height:72vh;overflow:auto;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;display:none;box-shadow:0 8px 30px var(--shadow);z-index:9}
#panel h3{margin:0 0 8px;font-size:15px}
#panel h4{margin:14px 0 4px;font-size:14px;display:flex;align-items:center;gap:8px}
#panel label.row{display:flex;gap:8px;align-items:flex-start;margin:8px 0}
#panel label.row input[type=checkbox],#panel label.row input[type=radio]{margin-top:3px}
.issue{display:flex;gap:8px;align-items:flex-start;justify-content:space-between;padding:6px 0;border-top:1px solid var(--line);font-size:13px}
.issue .acts{display:flex;gap:4px;flex-shrink:0}
.lvl{font-size:11px;font-weight:700;margin-right:4px}
.lvl.block{color:var(--ed)}.lvl.fix{color:var(--warn)}.lvl.warn{color:var(--warn)}.lvl.info,.lvl.done{color:var(--ok)}
.site{margin:8px 0}.prog{height:8px;background:var(--line);border-radius:4px;overflow:hidden;margin:4px 0}
.prog i{display:block;height:100%;background:var(--accent);width:0}
.err{color:var(--ed);font-size:12px}
pre{white-space:pre-wrap;font-size:12px;margin:4px 0}
.hint{font-size:12px;color:var(--mute)}
.login{border-top:1px solid var(--line);padding:8px 0}
.login .f{display:flex;gap:6px;flex-wrap:wrap;margin-top:4px}
.login input{width:200px}
.empty{color:var(--mute);padding:40px;text-align:center}
@media (max-width:600px){input:not([type=checkbox]):not([type=radio]){width:100px}.group{width:100%}.login input{width:100%}}
</style></head><body>
<header>
  <div class="top">
    <h1>StockUp</h1><span class="sub" id="folder"></span><span class="mode" id="mode" onclick="openSettings()"></span>
    <button onclick="openChecks()" id="checksBtn">Check photos</button>
    <div class="seg" id="theme" title="Light or dark look">
      <button data-t="auto">Auto</button><button data-t="light">Light</button><button data-t="dark">Dark</button>
    </div>
    <button onclick="openLogins()">Logins</button>
    <button onclick="openSettings()">Settings</button>
    <button onclick="another()" id="anotherBtn">Another batch</button>
    <button onclick="quitApp()">Quit</button>
  </div>
  <div id="banner"></div>
  <div id="scanbar"><span id="scantext"></span><div class="prog"><i id="scanfill"></i></div></div>
  <div class="bar">
    <div class="group"><b>Pick</b>
      <button onclick="selAll(true)">All</button><button onclick="selAll(false)">None</button>
      <select id="filter" onchange="render()">
        <option value="all">Show every photo</option><option value="ed">Show editorial</option>
        <option value="com">Show creative</option><option value="nocat">Show ones missing a category</option>
        <option value="issues">Show ones a site would reject</option>
      </select>
      <span class="hint" id="selcount">0 picked</span>
    </div>
    <div class="group"><b>Type</b>
      <button class="edbtn" onclick="setEd(true)">Editorial</button>
      <button onclick="setEd(false)">Creative</button>
    </div>
  </div>
  <div class="bar">
    <div class="group"><b>Categories</b>
      <button onclick="autoCats()" title="Pick categories for the picked photos from their titles and keywords">Auto-pick</button>
    </div>
    <div class="group" id="ssGroup"><b>Shutterstock category</b>
      <select id="ss1"></select><select id="ss2"></select>
      <button onclick="applySS()">Set</button>
    </div>
    <div class="group" id="adGroup"><b>Adobe category</b><select id="ad"></select><button onclick="applyAd()">Set</button></div>
    <div class="group" id="dtGroup"><b>Dreamstime category</b>
      <select id="dt1"></select><select id="dt2"></select><select id="dt3"></select>
      <button onclick="applyDT()">Set</button>
    </div>
    <div class="group"><b>Editorial: where &amp; when</b>
      <input id="ecity" placeholder="City"><input id="eregion" placeholder="State or country">
      <input id="edate" type="date"><button onclick="applyLoc()">Set</button>
    </div>
  </div>
</header>
<section id="welcome">
  <div id="dropzone">
    <h2>Drop photos or a folder here</h2>
    <p class="hint">StockUp opens them right where they are when it can find them on your computer. If it can’t, it copies them into a new batch folder, and your originals stay where they were.</p>
    <div class="choices">
      <button class="primary" onclick="pickFolder()">Choose a folder…</button>
      <button onclick="$('pickFiles').click()">Choose photos…</button>
      <input type="file" id="pickFiles" multiple accept=".jpg,.jpeg,image/jpeg" hidden onchange="filesChosen(this)">
    </div>
    <div class="pathrow"><input id="pathIn" placeholder="…or paste a folder’s location" onkeydown="if(event.key=='Enter')openPath()"><button onclick="openPath()">Open</button></div>
  </div>
  <div id="recent"></div>
</section>
<main><div class="grid" id="grid"></div></main>
<div id="panel"></div>
<div id="dock"></div>
<div id="droplay"><div>Drop to open these photos<small id="droplayHint"></small></div></div>
<div id="busy"><div class="card2"><b id="busyTitle"></b><div class="prog"><i id="busyFill"></i></div><div class="hint" id="busyText"></div></div></div>
<footer>
  <span id="msg">Click a photo to pick it. Shift-click to pick a range.</span>
  <div class="sites" id="sites"></div>
  <button onclick="testSites()">Check logins</button>
  <button onclick="build()" id="buildBtn">Make site files</button>
  <button class="primary" onclick="upload()" id="uploadBtn">Upload</button>
</footer>
<script>
const SS=__SS__, AD=__AD__, SITES=__SITES__, FIXES=__FIXES__, DT=__DT__;
const DTNAME=Object.fromEntries(DT.map(([n,c])=>[c,n.replace(' -> ',' › ')]));
let items=[], sel=new Set(), last=null, dirty=false, saveT=null, opts={}, sites=[], VERSION='', FROZEN=false, PILLOW=true, openDetail=null;
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]);
const lab=s=>SITES[s]?.label||s;
const jattr=v=>esc(JSON.stringify(v));
async function api(path,body){
  const r=await fetch(path,body===undefined?{}:{method:'POST',body:JSON.stringify(body)});
  const j=await r.json().catch(()=>({error:'StockUp stopped responding. Is it still running?'}));
  if(!r.ok||j.error)throw new Error(j.error||'Something went wrong.');
  return j;
}
/* theme */
function setTheme(t){
  if(t==='auto')delete document.documentElement.dataset.theme;else document.documentElement.dataset.theme=t;
  try{localStorage.setItem('stockup-theme',t)}catch(e){}
  document.querySelectorAll('#theme button').forEach(b=>b.classList.toggle('on',b.dataset.t===t));
}
$('theme').onclick=e=>{const t=e.target.dataset.t;if(t)setTheme(t)};
setTheme(document.documentElement.dataset.theme||'auto');

function fill(){
  for(const id of ['ss1','ss2'])$(id).innerHTML=`<option value="">${id=='ss1'?'Choose…':'Second (optional)'}</option>`+SS.map(c=>`<option>${c}</option>`).join('');
  $('ad').innerHTML='<option value="">Choose…</option>'+Object.entries(AD).map(([n,c])=>`<option value="${n}">${c}</option>`).join('');
  const groups={};DT.forEach(([n,c])=>{const [g,sub]=n.split(' -> ');(groups[g]=groups[g]||[]).push([sub,c])});
  const opts_=Object.entries(groups).map(([g,l])=>`<optgroup label="${esc(g)}">${l.map(([sub,c])=>`<option value="${c}">${esc(sub)}</option>`).join('')}</optgroup>`).join('');
  ['dt1','dt2','dt3'].forEach((id,i)=>$(id).innerHTML=`<option value="">${['Choose…','Second (optional)','Third (optional)'][i]}</option>`+opts_);
}
const my=()=>opts.my_sites||[];
const hasIssue=it=>Object.values(it.sites).some(s=>s.status=='fix'||s.status=='skip'&&!s.user_skipped);
function visible(){
  const f=$('filter').value;
  return items.filter(it=>f=='all'||(f=='ed'&&it.editorial)||(f=='com'&&!it.editorial)||
    (f=='nocat'&&((my().includes('shutterstock')&&!it.ss_cats.length)||(my().includes('adobe')&&!it.adobe_cat)||(my().includes('dreamstime')&&!it.dt_cats.length)))||(f=='issues'&&hasIssue(it)));
}
function chips(it){
  const word={ok:'ready',warn:'ready, with a note',fix:'needs a fix',skip:'not sending',sent:'sent'};
  return '<div class="chips">'+my().map(s=>{const st=it.sites[s];if(!st)return'';
    return `<span class="chip ${st.status}" title="${esc(lab(s))}: ${word[st.status]}">${esc(SITES[s].short)}</span>`}).join('')+'</div>';
}
function render(){
  const v=visible();
  $('grid').innerHTML=v.length?v.map(it=>{
    const t=[];
    if(it.editorial){
      t.push('<span class="tag ed">EDITORIAL</span>');
      if(it.preformatted)t.push('<span class="tag ok">caption ready</span>');
      else if(opts.caption_mode=='keep')t.push('<span class="tag warn">kept as written</span>');
      else{const loc=[it.ed_city,it.ed_region].filter(Boolean).join(', ');
        t.push(`<span class="tag ${loc&&it.ed_date?'':'warn'}">${esc(loc||'add place')} · ${esc(it.ed_date||'add date')}</span>`);}
    }
    t.push(`<span class="tag">${it.keywords.length} keywords</span>`);
    const au=k=>it.auto_cats.includes(k)?` <i class="auto" title="Picked automatically from: ${esc(it.cat_why.join(', '))}">auto</i>`:'';
    if(my().includes('shutterstock'))t.push(it.ss_cats.length?`<span class="tag">SS: ${esc(it.ss_cats.join(' + '))}${au('ss')}</span>`:'<span class="tag warn">no Shutterstock category</span>');
    if(my().includes('adobe')&&it.adobe_cat)t.push(`<span class="tag">Adobe: ${esc(AD[it.adobe_cat])}${au('adobe')}</span>`);
    if(my().includes('dreamstime')&&it.dt_cats.length)t.push(`<span class="tag">DT: ${esc(it.dt_cats.map(c=>DTNAME[c]?.split(' › ').pop()).join(', '))}${au('dt')}</span>`);
    if(it.info.gps)t.push(`<span class="tag ${opts.strip_gps?'':'warn'}" title="Where the photo was taken is saved in the file">${opts.strip_gps?'GPS removed on upload':'has GPS location'}</span>`);
    const mp=(it.info.width*it.info.height/1e6).toFixed(1);
    return `<div class="card ${sel.has(it.file)?'sel':''} ${it.editorial?'ed':''}" data-f="${esc(it.file)}" title="${esc(it.auto?'Marked editorial automatically: '+it.auto:'')}">
      <div class="check">${sel.has(it.file)?'✓':''}</div>
      <img class="thumb" loading="lazy" src="/thumb/${encodeURIComponent(it.file)}?v=${it.info.bytes}-${it.mtime||0}" alt="">
      <div class="meta"><div class="fn"><span>${esc(it.file)}</span><span>${mp} MP</span></div><div class="tt">${esc(it.description||it.title||'(no title)')}</div>
      <div class="tags">${t.join('')}</div>${chips(it)}
      <button class="link small" data-detail="${esc(it.file)}">Site details</button></div></div>`}).join(''):'<div class="empty">No photos to show here.</div>';
  $('selcount').textContent=sel.size+' picked';
  $('ssGroup').style.display=my().includes('shutterstock')?'':'none';
  $('adGroup').style.display=my().includes('adobe')?'':'none';
  $('dtGroup').style.display=my().includes('dreamstime')?'':'none';
  const adj=opts.photo_mode=='adjusted';
  $('mode').className='mode'+(adj?' adj':'');
  $('mode').textContent=adj?'Adjusted copies allowed':'Exact files only';
  $('mode').title=adj?'StockUp may make adjusted copies (fixes, resizing) for upload. Your originals are never changed. Click to change.'
    :'Every site gets your exact original files, byte for byte. Click to change.';
  const n=items.filter(hasIssue).length;
  $('checksBtn').innerHTML='Check photos'+(n?`<span class="count">${n}</span>`:'');
}
$('grid').addEventListener('click',e=>{
  const d=e.target.closest('[data-detail]');if(d){showDetail(d.dataset.detail);return}
  const c=e.target.closest('.card');if(!c)return;
  const f=c.dataset.f,vis=visible().map(i=>i.file);
  if(e.shiftKey&&last!==null){const a=vis.indexOf(last),b=vis.indexOf(f);
    if(a>=0&&b>=0)for(let i=Math.min(a,b);i<=Math.max(a,b);i++)sel.add(vis[i]);}
  else{sel.has(f)?sel.delete(f):sel.add(f)}
  last=f;render();
});
function selAll(on){sel=on?new Set(visible().map(i=>i.file)):new Set();render()}
function need(){if(!sel.size){note('Pick some photos first.');return false}return true}
function each(fn){items.forEach(it=>{if(sel.has(it.file))fn(it)});changed()}
function setEd(v){if(need())each(it=>it.editorial=v)}
function applySS(){if(!need())return;const c=[$('ss1').value,$('ss2').value].filter(Boolean);
  if(!c.length)return note('Choose a Shutterstock category first.');each(it=>it.ss_cats=[...new Set(c)])}
function applyAd(){if(!need())return;const v=$('ad').value;if(!v)return note('Choose an Adobe category first.');each(it=>it.adobe_cat=+v)}
async function autoCats(){
  if(!sel.size)return note('Pick the photos to auto-pick categories for (click “All” for the whole batch).');
  if(!confirm(`Pick categories for ${sel.size} photo${sel.size>1?'s':''} from their titles and keywords? This replaces the categories they have now.`))return;
  try{await save();const r=await api('/api/autocats',{files:[...sel]});await load();note(`Categories picked for ${r.changed} photo${r.changed==1?'':'s'}. Check them, then change any with the menus.`)}
  catch(e){note(e.message)}
}
function applyDT(){if(!need())return;const c=[$('dt1').value,$('dt2').value,$('dt3').value].filter(Boolean).map(Number);
  if(!c.length)return note('Choose a Dreamstime category first.');each(it=>it.dt_cats=[...new Set(c)])}
function applyLoc(){if(!need())return;const c=$('ecity').value.trim(),r=$('eregion').value.trim(),d=$('edate').value;
  if(!c&&!r&&!d)return note('Type a place or pick a date first.');each(it=>{if(c)it.ed_city=c;if(r)it.ed_region=r;if(d)it.ed_date=d})}
let keepNoteUntil=0;
function note(t,keep){if(!keep&&Date.now()<keepNoteUntil)return;$('msg').textContent=t;if(keep)keepNoteUntil=Date.now()+12000}
function counts(){const e=items.filter(i=>i.editorial).length;return `${e} editorial, ${items.length-e} creative`}
function changed(){dirty=true;render();clearTimeout(saveT);saveT=setTimeout(async()=>{await save();await load()},500)}
async function save(){
  if(!dirty)return;
  const body={};items.forEach(it=>body[it.file]={editorial:it.editorial,ss_cats:it.ss_cats,adobe_cat:it.adobe_cat,dt_cats:it.dt_cats,ed_city:it.ed_city,ed_region:it.ed_region,ed_date:it.ed_date});
  try{await api('/api/save',body);dirty=false;note('Saved · '+counts())}catch(e){note(e.message)}
}
function panel(html,keepScroll){openDetail=null;const p=$('panel');const y=p.scrollTop;p.innerHTML=html+'<p><button onclick="closePanel()">Close</button></p>';p.style.display='block';p.scrollTop=keepScroll?y:0}
function closePanel(){openDetail=null;$('panel').style.display='none'}

/* per-site checks */
function issueRow(i,acts){
  const tag=i.applied?'<span class="lvl done">FIXED</span>':`<span class="lvl ${i.level}">${{block:'WON’T ACCEPT',fix:'FIX',warn:'NOTE',info:'OK'}[i.level]}</span>`;
  const lock=i.locked&&!i.applied?'<div class="hint">StockUp can fix this with an adjusted copy, but you’ve chosen to upload exact files only. To allow it, choose “Let StockUp make adjusted copies” in <button class="link small" onclick="openSettings()">Settings</button>. Your original is never changed either way.</div>'
    :(i.needs_pillow&&!i.applied?'<div class="hint">This fix needs Pillow: run  pip install pillow  (the StockUp app already has it).</div>':'');
  return `<div class="issue"><div>${tag}${esc(i.text)}${lock}</div><div class="acts">${acts}</div></div>`;
}
async function act(files,site,code,action){
  try{await save();const r=await api('/api/fix',{files,site,code,action});await load();
    note(r.changed?'Updated.':'Nothing to change.');if(openDetail)showDetail(openDetail,true);else openChecks(true,true)}
  catch(e){note(e.message)}
}
function showDetail(f,keepScroll){
  const it=items.find(i=>i.file==f);if(!it)return;
  let h=`<h3>${esc(f)}</h3><div class="hint">${it.info.width} × ${it.info.height} px · ${(it.info.width*it.info.height/1e6).toFixed(1)} MP · ${(it.info.bytes/1048576).toFixed(1)} MB · ${esc(it.info.color)}${it.info.quality?' · JPEG quality ~'+it.info.quality:''}${it.info.progressive?' · progressive':''}${it.info.gps?' · has GPS location':''}</div>`;
  for(const s of my()){
    const st=it.sites[s];if(!st)continue;
    const word={ok:'Ready',warn:'Ready, with a note',fix:'Needs a fix',skip:'Not sending',sent:'Sent'}[st.status];
    const toggle=st.status=='sent'?'':st.status=='skip'?`<button class="small" onclick="act(${jattr([f])},'${s}','*','include')">Send anyway</button>`
      :`<button class="small" onclick="act(${jattr([f])},'${s}','*','skip')">Don’t send</button>`;
    h+=`<h4><span class="chip ${st.status}">${esc(SITES[s].short)}</span>${esc(lab(s))} — ${word} ${toggle}</h4>`;
    if(!st.issues.length)h+='<div class="hint">Nothing to change.</div>';
    for(const i of st.issues){
      let a='';
      if(i.fix&&!i.applied&&!i.needs_pillow&&!i.locked)a=`<button class="small primary" onclick="act(${jattr([f])},'${s}','${i.fix}','apply')">${esc(FIXES[i.fix])}</button>`;
      if(i.applied)a=`<button class="small" onclick="act(${jattr([f])},'${s}','${i.fix}','unapply')">Undo</button>`;
      h+=issueRow(i,a);
    }
  }
  panel(h,keepScroll);openDetail=f;
}
function generic(t){  // one line that is true for every photo in a group
  return t.replace(/\s*Suggested:.*$/,'').replace(/ is [\d.,]+ (characters|MB)/,' is too long').replace(/^Too small: [\d.]+ MP\./,'Too small.')
    .replace(/^[\d.]+ MP is over/,'Over').replace(/^File is [\d.]+ MB;/,'File is too big;').replace(/^\d+ keywords;/,'Too few keywords;')
    .replace(/ Add \d+ more before you submit there\./,'').replace(/^Description has \d+ words;/,'Description is too short;')
    .replace(/the short side is \d+ px;/,'the short side is too short;').replace(/File name is \d+ characters;/,'File name is too long;')
    .replace(/Skip Adobe for this one unless it fits\./,'');
}
function openChecks(keep,keepScroll){
  if(keep&&$('panel').style.display=='none')return;
  let h='<h3>Check photos</h3><div class="hint">What each site would reject or change, and what StockUp can fix. Your original photos are never changed. '+(opts.photo_mode=='adjusted'?'Fixes that change the picture make copies just for that site.':'You’ve chosen to upload exact files only, so fixes that would change the picture are switched off.')+'</div>';
  let anyFix=false;
  for(const s of my()){
    const st={ok:0,warn:0,fix:0,skip:0,sent:0},groups={};
    for(const it of items){const x=it.sites[s];if(!x)continue;st[x.status]++;
      if(x.user_skipped||x.status=='sent')continue;
      for(const i of x.issues){if(i.level=='info')continue;
        const k=(i.applied?'done:':'')+(i.fix||i.level)+'|'+i.text.replace(/[\d.]+|“.*”/g,'#').slice(0,60);
        (groups[k]=groups[k]||{i,files:[]}).files.push(it.file);}}
    const tot=st.ok+st.warn+st.fix+st.skip;
    h+=`<h4><span class="chip ${st.fix?'fix':st.skip?'skip':st.warn?'warn':'ok'}">${esc(SITES[s].short)}</span>${esc(lab(s))} — ${st.ok+st.warn} ready${st.fix?`, ${st.fix} need a fix`:''}${st.skip?`, ${st.skip} not sending`:''}${st.sent?`, ${st.sent} sent`:''}</h4>`;
    const gs=Object.values(groups);
    if(!gs.length){h+='<div class="hint">All good.</div>';continue}
    for(const {i,files} of gs){
      const n=files.length,list=jattr(files);
      const text=n>1?`${n} photos: ${generic(i.text)}`:`${files[0]}: ${i.text}`;
      let a='';
      if(i.fix&&!i.applied&&!i.needs_pillow&&!i.locked){anyFix=true;a=`<button class="small primary" onclick="act(${list},'${s}','${i.fix}','apply')">${esc(FIXES[i.fix])}${n>1?' ('+n+')':''}</button>`}
      else if(i.applied)a=`<button class="small" onclick="act(${list},'${s}','${i.fix}','unapply')">Undo</button>`;
      if(i.level=='block')a+=`<button class="small" onclick="act(${list},'${s}','*','include')">Send anyway</button>`;
      if(i.level=='warn'&&/Adobe only accepts/.test(i.text))a+=`<button class="small" onclick="act(${list},'${s}','*','skip')">Don’t send to Adobe</button>`;
      h+=issueRow({...i,text},a+(n==1?`<button class="small" onclick="showDetail(${jattr(files[0])})">Details</button>`:''));
    }
  }
  if(anyFix)h+=`<p><button class="primary" onclick="act([],'*','*','apply')">Apply every fix</button> <span class="hint">You can undo any of them from a photo’s Site details.</span></p>`;
  if(!PILLOW)h+='<p class="hint">Some fixes need Pillow. Run  pip install pillow  and reopen StockUp, or use the StockUp app.</p>';
  panel(h,keepScroll);
}

/* settings */
function openSettings(){
  const o=opts;
  const adj=o.photo_mode=='adjusted';
  panel(`<h3>Settings</h3>
  <div class="hint">Your photo files</div>
  <label class="row"><input type="radio" name="pm" value="untouched" ${!adj?'checked':''} onchange="pmChanged()">
    <span><b>Upload my exact files</b> (recommended). Every site gets your originals byte for byte. Fixes that would change the picture are switched off.</span></label>
  <label class="row"><input type="radio" name="pm" value="adjusted" ${adj?'checked':''} onchange="pmChanged()">
    <span><b>Let StockUp make adjusted copies</b> when you ask: sRGB, smaller files, baseline JPEGs, and resizing below. Copies go in stockup_output; your originals are never changed.</span></label>
  <div id="rs" style="margin:4px 0 0 24px;${adj?'':'opacity:.5'}">
    <label class="row"><span style="min-width:150px">Resize for upload</span>
      <select id="o_rmode" ${adj?'':'disabled'}>
        <option value="off" ${o.resize_mode=='off'?'selected':''}>Don’t resize</option>
        <option value="long_side" ${o.resize_mode=='long_side'?'selected':''}>Longest side, in pixels</option>
        <option value="megapixels" ${o.resize_mode=='megapixels'?'selected':''}>Megapixels</option>
      </select>
      <input id="o_rval" style="width:90px" inputmode="decimal" value="${esc(o.resize_value)}" placeholder="e.g. 6000" ${adj?'':'disabled'}></label>
    <div class="hint">Only makes photos smaller, never bigger, and never below a site’s minimum. Your originals stay full size.</div>
  </div>
  <label class="row"><input type="checkbox" id="o_gps" ${o.strip_gps?'checked':''}>
    <span><b>Remove GPS location</b> from what’s uploaded. Only the location is blanked out; the picture itself isn’t changed or re-saved.</span></label>
  <div class="hint" style="margin-top:12px">Uploading</div>
  <label class="row"><input type="checkbox" id="o_skip" ${o.skip_sent?'checked':''}>
    <span>Skip photos I’ve already sent to a site (recognised even if renamed)</span></label>
  <label class="row"><input type="checkbox" id="o_enc" ${o.encrypted_only?'checked':''}>
    <span>Only upload over encrypted connections. Sites that can’t do this are skipped. (StockUp always tries encryption first.)</span></label>
  <label class="row"><span style="min-width:150px">Upload order</span>
    <select id="o_order"><option value="spread" ${o.upload_order=='spread'?'selected':''}>Shuffled, look-alikes kept apart</option>
    <option value="folder" ${o.upload_order=='folder'?'selected':''}>Same order as the folder</option></select></label>
  <label class="row"><span style="min-width:150px">Sites</span>
    <select id="o_pace"><option value="together" ${o.upload_pacing=='together'?'selected':''}>All at once</option>
    <option value="one_by_one" ${o.upload_pacing=='one_by_one'?'selected':''}>One site at a time</option></select></label>
  <label class="row"><input type="checkbox" id="o_cats" ${o.auto_categories?'checked':''}>
    <span>Pick categories automatically from titles and keywords for photos that don’t have any. Categories you choose yourself are never changed.</span></label>
  <div class="hint" style="margin-top:12px">Sites I sell on</div>
  <div style="display:flex;flex-wrap:wrap;gap:10px;margin:6px 0">${Object.keys(SITES).map(s=>`<label class="row" style="margin:0"><input type="checkbox" class="mysite" value="${s}" ${my().includes(s)?'checked':''}><span>${esc(lab(s))}</span></label>`).join('')}</div>
  <div class="hint" style="margin-top:12px">Titles and captions</div>
  <label class="row"><input type="radio" name="cm" value="auto" ${o.caption_mode=='auto'?'checked':''}>
    <span>Keep captions that already start with a place and date, and add them to the rest</span></label>
  <label class="row"><input type="radio" name="cm" value="keep" ${o.caption_mode=='keep'?'checked':''}>
    <span>Always use my titles and captions exactly as written</span></label>
  <label class="row"><input type="checkbox" id="o_commas" ${o.adobe_strip_commas?'checked':''}>
    <span>Take commas out of Adobe titles (Adobe asks for this)</span></label>
  <div class="hint" style="margin-top:12px">Mark photos as editorial automatically when…</div>
  <label class="row"><input type="checkbox" id="o_fmt" ${o.flag_formatted?'checked':''}>
    <span>the caption already starts with a place and date</span></label>
  <label class="row"><span style="min-width:140px">it has the keyword</span><input id="o_kw" value="${esc(o.editorial_keyword)}" placeholder="e.g. editorial"></label>
  <label class="row"><input type="checkbox" id="o_drop" ${o.drop_marker_keyword?'checked':''}>
    <span>…and leave that keyword out when uploading</span></label>
  <label class="row"><span style="min-width:140px">it has the color label</span>
    <select id="o_lab">${['','Red','Yellow','Green','Blue','Purple'].map(l=>`<option ${o.editorial_label==l?'selected':''} value="${l}">${l||'(off)'}</option>`).join('')}</select></label>
  <p><button class="primary" onclick="saveSettings()">Save settings</button></p>
  <div class="hint">Version ${esc(VERSION)} · <button class="link" onclick="checkUpdate(true)">Check for updates</button></div>`);
}
function pmChanged(){const adj=document.querySelector('input[name=pm]:checked').value=='adjusted';
  $('rs').style.opacity=adj?1:.5;$('o_rmode').disabled=!adj;$('o_rval').disabled=!adj}
async function saveSettings(){
  const rv=$('o_rval').value.trim();
  if($('o_rmode').value!='off'&&!(parseFloat(rv)>0))return note('Type a size for “Resize for upload”, or choose “Don’t resize”.');
  const o={photo_mode:document.querySelector('input[name=pm]:checked').value,resize_mode:$('o_rmode').value,resize_value:rv,
    strip_gps:$('o_gps').checked,skip_sent:$('o_skip').checked,encrypted_only:$('o_enc').checked,
    upload_order:$('o_order').value,upload_pacing:$('o_pace').value,auto_categories:$('o_cats').checked,caption_mode:document.querySelector('input[name=cm]:checked').value,adobe_strip_commas:$('o_commas').checked,
    flag_formatted:$('o_fmt').checked,editorial_keyword:$('o_kw').value,drop_marker_keyword:$('o_drop').checked,editorial_label:$('o_lab').value,
    my_sites:[...document.querySelectorAll('.mysite:checked')].map(i=>i.value)};
  try{const r=await api('/api/options',o);await load();await loadSites();
    note(r.newly_flagged?`Settings saved. ${r.newly_flagged} more photos marked editorial.`:'Settings saved.');closePanel();}
  catch(e){note(e.message)}
}

/* logins */
async function openLogins(){
  let L;try{L=await api('/api/logins')}catch(e){return note(e.message)}
  const rows=L.sites.filter(s=>my().includes(s.name)).map(s=>`<div class="login" data-s="${s.name}">
    <b>${esc(s.label)}</b> ${s.username&&s.has_password?'<span class="tag ok">saved</span>':''}
    <div class="hint">${esc(s.hint)}</div>
    <div class="f"><input class="u" placeholder="Username" value="${esc(s.username)}" autocomplete="off">
    <input class="p" type="password" placeholder="${s.has_password?'Password saved · type to change':'Password'}" autocomplete="new-password">
    ${s.username?`<button class="small" onclick="clearLogin('${s.name}')">Remove</button>`:''}</div></div>`).join('');
  panel(`<h3>Logins</h3><div class="hint">${L.keychain?`Passwords are stored in ${esc(L.keychain)}, not in a file.`:'Passwords are stored in stockup_sites.ini on this computer. Install keyring (pip install keyring) to keep them in your system keychain instead.'}</div>
    ${rows||'<p class="hint">Pick your sites in Settings first.</p>'}
    <p><button class="primary" onclick="saveLogins()">Save logins</button> <button onclick="testSites()">Check logins</button></p>`);
}
async function saveLogins(){
  const ch={};document.querySelectorAll('.login').forEach(d=>{ch[d.dataset.s]={username:d.querySelector('.u').value,password:d.querySelector('.p').value}});
  try{await api('/api/logins',ch);await loadSites();note('Logins saved.');openLogins()}catch(e){note(e.message)}
}
async function clearLogin(s){try{await api('/api/logins',{[s]:{clear:true}});await loadSites();openLogins()}catch(e){note(e.message)}}

/* sites to send to */
async function loadSites(){sites=await api('/api/sites');renderSites()}
function renderSites(){
  $('sites').innerHTML='<span class="hint">Send to:</span>'+(sites.length?sites.map(s=>`<label class="${s.ready?'':'off'}" title="${s.ready?'':'Add your login under Logins to use this site'}">
    <input type="checkbox" data-s="${esc(s.name)}" ${s.checked?'checked':''} ${s.ready?'':'disabled'}>${esc(s.label)}</label>`).join(''):'<span class="hint">no upload sites</span>');
  $('sites').querySelectorAll('input').forEach(i=>i.onchange=()=>{const s=sites.find(x=>x.name==i.dataset.s);s.checked=i.checked;
    api('/api/options',{upload_sites:sites.filter(x=>x.checked).map(x=>x.name)}).catch(()=>{})});
}

/* make files */
let bpoll=null;
async function build(){
  await save();
  try{await api('/api/build',{});clearInterval(bpoll);bpoll=setInterval(buildStatus,700);buildStatus()}catch(e){note(e.message)}
}
async function buildStatus(){
  let b;try{b=await api('/api/build_status')}catch(e){return}
  if(b.running){const pct=b.total?100*b.done/b.total:0;
    panel(`<h3>Making site files…</h3><div class="prog"><i style="width:${pct}%"></i></div><div class="hint">${esc(b.current)}</div>`);return}
  clearInterval(bpoll);
  if(b.error)return panel('<h3>Couldn’t make the files</h3><p class="err">'+esc(b.error)+'</p>');
  const r=b.result;if(!r)return;
  note(`Files ready · ${counts()}`);
  const orig=r.changed_originals.length?`<p class="err">These originals changed while StockUp was open (StockUp never writes to them, so another app did): ${esc(r.changed_originals.join(', '))}</p>`
    :`<p class="ok">✓ Your originals are unchanged.${r.mode=='adjusted'?'':' Every site gets your exact files.'}</p>`;
  panel(`<h3>Files ready</h3><div class="hint">Saved in ${esc(r.folder)}</div>${orig}`+r.sites.map(s=>`<h4>${esc(s.label)}</h4>
    <div>${s.included} photos${s.skipped?`, ${s.skipped} not sent`:''}${s.problems?` · <span class="err">${s.problems} note${s.problems>1?'s':''} in checks.txt</span>`:''}</div>
    <div class="hint">${esc(s.files.join(' · '))}</div><div class="hint">Next: ${esc(s.next)}</div>
    ${s.site=='dreamstime'?'<p><button class="small primary" onclick="sendSheet(\'dreamstime\')">Send spreadsheet to Dreamstime</button></p>':''}`).join(''));
}
async function sendSheet(site){
  if(!confirm('Send the spreadsheet now? Dreamstime only applies it to photos that already show up in its Unfinished section.'))return;
  try{const r=await api('/api/send_sheet',{site});note(r.message)}catch(e){note(e.message)}
}
async function testSites(){
  panel('<h3>Checking logins…</h3>');
  try{const r=await api('/api/test',{});
    panel('<h3>Logins</h3>'+r.map(s=>`<div class="site"><b>${esc(s.label)}</b>
      <div class="${s.result=='OK'?'':'err'}">${s.result=='OK'?`Connected · <span class="lock ${s.encrypted?'':'no'}">${s.encrypted?'encrypted':'not encrypted (the site doesn’t offer it)'}</span>`:esc(s.result)}</div>${s.listing?`<div class="hint">On the server: ${esc(s.listing.join(', ')||'(empty)')}</div>`:''}</div>`).join(''));}
  catch(e){panel('<h3>Logins</h3><p class="err">'+esc(e.message)+'</p>')}
}
let poll=null,dockHidden=false;
async function upload(){
  const pick=sites.filter(s=>s.checked).map(s=>s.name);
  if(!pick.length)return panel('<h3>Pick a site</h3><p>Tick at least one site next to “Send to”. A site is greyed out until you add its login under Logins.</p>');
  await save();
  let pv;try{pv=await api('/api/preview',{sites:pick})}catch(e){return note(e.message)}
  const total=pv.sites.reduce((a,s)=>a+s.send,0);
  panel(`<h3>Ready to upload</h3>
    <div class="hint">Nothing has been sent yet. Check this, then press Start.</div>
    <table class="pv"><tr><th>Site</th><th>Sending</th><th>Not sending</th><th>Connection</th></tr>`+
    pv.sites.map(s=>`<tr><td><b>${esc(s.label)}</b><div class="hint">${esc(s.host)}</div></td>
      <td>${s.send} photos · ${s.mb} MB${s.editorial?`<div class="hint">${s.editorial} editorial</div>`:''}${s.copies?`<div class="hint">${s.copies} as adjusted copies</div>`:''}${s.renamed?`<div class="hint">${s.renamed} with a safer name</div>`:''}</td>
      <td>${s.not_sending||'–'}${s.already?`<div class="hint">${s.already} already sent</div>`:''}</td>
      <td class="hint">${esc(s.connection)}</td></tr>`).join('')+`</table>
    <div class="hint">${pv.mode=='adjusted'?'Adjusted copies are allowed (only where you applied a fix or set a resize).':'Exact files: every photo goes up exactly as it is on your computer.'}
      ${pv.strip_gps?' GPS locations are left out.':''} Order: ${pv.order=='spread'?'shuffled, look-alikes kept apart':'same as the folder'}. ${pv.pacing=='one_by_one'?'One site at a time.':'All sites at once.'}
      Files already on a site’s server are never overwritten.</div>
    <p><button class="primary" ${total?'':'disabled'} onclick="startUpload(${jattr(pick)})">Start upload${total?` (${total})`:''}</button></p>`);
}
async function startUpload(pick){
  try{const j=await api('/api/upload',{sites:pick});
    if(!j.started)return panel('<h3>Nothing to upload</h3><p>'+esc(j.reason)+'</p>');
    closePanel();dockHidden=false;clearInterval(poll);poll=setInterval(status,1000);status();}
  catch(e){note(e.message)}
}
const mb=b=>(b/1048576).toFixed(0)+' MB';
let lastStatus=null;
async function status(){
  let s;try{s=await api('/api/status')}catch(e){return}
  lastStatus=s;
  const d=$('dock');
  if(!Object.keys(s.sites).length||dockHidden){d.style.display='none'}
  else{
    d.style.display='flex';
    d.innerHTML=Object.entries(s.sites).map(([n,p])=>{
      const pct=p.total_bytes?Math.min(100,100*p.bytes/p.total_bytes):0;
      const fin=/finished|stopped|error/.test(p.state);
      const cls=p.state=='error'||p.errors.length?'bad':(fin?'done':'');
      let word={waiting:'Waiting its turn',connecting:'Connecting…',uploading:'Uploading',paused:'Paused',finishing:'Finishing this photo…',
        'tidying up':'Removing the half-sent file…',finished:'Done','finished with errors':'Done, with problems',stopped:'Stopped',error:'Stopped by a problem'}[p.state]||p.state;
      if(s.paused&&p.state=='finishing')word='Finishing this photo, then pausing';
      else if(s.paused&&/uploading|connecting/.test(p.state))word='Pausing…';
      if(s.stopping&&p.state=='finishing')word='Finishing this photo, then stopping';
      return `<div class="pcard ${cls}"><div class="row1"><b>${esc(lab(n))}</b><span>${Math.round(pct)}%</span></div>
        <div class="prog big"><i style="width:${pct}%"></i></div>
        <div class="row1 hint"><span>${esc(word)} · ${p.done} of ${p.total}</span>${p.encrypted===null?'':`<span class="lock ${p.encrypted?'':'no'}">${p.encrypted?'encrypted':'not encrypted'}</span>`}</div>
        ${p.current&&!fin?`<div class="hint">${esc(p.current)}</div>`:''}${p.errors.length?`<div class="err">${p.errors.length} problem${p.errors.length>1?'s':''}</div>`:''}</div>`}).join('')+
      `<div class="dockbar">${s.running&&!s.stopping?`<button class="small ${s.paused?'primary':''}" onclick="pauseUpload(${!s.paused})">${s.paused?'Resume':'Pause'}</button>`:''}
        ${s.running?`<button class="small" ${s.stopping?'disabled':''} onclick="stopUpload()">${s.stopping?'Stopping…':'Stop'}</button>`:''}
        <button class="small" onclick="uploadDetails()">Details</button>${s.running?'':'<button class="small" onclick="hideDock()">Close</button>'}</div>`;
  }
  if(!s.running){clearInterval(poll);if(Object.keys(s.sites).length){note('Upload finished.');load()}}
}
function hideDock(){dockHidden=true;$('dock').style.display='none'}
async function stopUpload(){
  if(!confirm('Stop uploading now? Any photo part-way through is removed from the site, and Upload picks up from there next time.'))return;
  try{await api('/api/stop',{});status()}catch(e){note(e.message)}}
async function pauseUpload(on){try{await api('/api/pause',{paused:on});note(on?'Paused. Press Resume to carry on; a photo that was part-way through is sent again from the start.':'Resumed.');status()}catch(e){note(e.message)}}
function uploadDetails(){
  const s=lastStatus;if(!s)return;
  panel('<h3>'+(s.running?'Uploading…':'Upload finished')+'</h3>'+Object.entries(s.sites).map(([n,p])=>`<div class="site"><b>${esc(lab(n))}</b> — ${p.done} of ${p.total} photos · ${mb(p.bytes)} of ${mb(p.total_bytes)}
      ${p.skipped?`<span class="hint">(${p.skipped} sent earlier)</span>`:''}${p.left_out?`<span class="hint"> · ${p.left_out} not for this site</span>`:''}
      ${p.notes.map(e=>`<div class="hint">${esc(e)}</div>`).join('')}${p.errors.map(e=>`<div class="err">${esc(e)}</div>`).join('')}</div>`).join('')+
    '<p class="hint">Every upload is listed in stockup_output/upload_receipt.csv. Something failed? Press Upload again — photos already sent are skipped.</p>');
}

/* updates */
async function checkUpdate(manual){
  try{const u=await api('/api/version'+(manual?'?force=1':''));
    if(u.available){$('banner').style.display='block';
      $('banner').innerHTML=u.frozen
        ?`StockUp ${esc(u.latest)} is available (you have ${esc(u.current)}). <a href="${esc(u.download)}" class="primary" target="_blank" rel="noopener"><button class="primary">Download</button></a> Then quit this one and open the new app. <a href="${esc(u.url)}" target="_blank" rel="noopener" class="hint">What’s new</a>`
        :`StockUp ${esc(u.latest)} is available (you have ${esc(u.current)}). <button class="primary" onclick="doUpdate()">Update now</button> <a href="${esc(u.url)}" target="_blank" rel="noopener" class="hint">What’s new</a>`;
      if(manual)closePanel();}
    else if(manual)note(`You have the latest version (${u.current}).`);}
  catch(e){if(manual)note(e.message)}
}
async function doUpdate(){
  await save();$('banner').textContent='Updating…';
  try{const r=await api('/api/update',{});
    if(!r.updated){$('banner').textContent=r.message;return}
    $('banner').textContent=r.message+' Restarting…';
    for(let i=0;i<30;i++){await new Promise(z=>setTimeout(z,1000));try{const v=await api('/api/version');if(v.current==r.version){location.reload();return}}catch(e){}}
    $('banner').textContent='Updated. Close this tab and start StockUp again.';}
  catch(e){$('banner').textContent=e.message}
}
async function another(){
  await save();
  try{const r=await api('/api/another',{});if(!r.ok)return note(r.message);closePanel();await load()}
  catch(e){note(e.message)}
}
async function quitApp(force){
  await save();
  try{const r=await api('/api/quit',{force:!!force});
    if(!r.ok){if(confirm(r.message))return quitApp(true);return}
    document.body.innerHTML='<div class="empty">StockUp has quit. You can close this tab.</div>';}
  catch(e){document.body.innerHTML='<div class="empty">StockUp has quit. You can close this tab.</div>'}
}

let scanT=null;
function showScan(sc){
  const busy=!sc.ready;
  $('scanbar').style.display=busy?'block':'none';
  $('buildBtn').disabled=busy;$('uploadBtn').disabled=busy;
  $('buildBtn').title=$('uploadBtn').title=busy?'Available once StockUp has finished checking your photos':'';
  if(busy){$('scantext').textContent=`Checking your photos for duplicates and photos you’ve sent before… ${sc.done} of ${sc.total}. You can start picking and tagging now.`;
    $('scanfill').style.width=(sc.total?100*sc.done/sc.total:0)+'%';}
}
async function watchScan(){
  clearTimeout(scanT);
  let sc;try{sc=await api('/api/scan')}catch(e){scanT=setTimeout(watchScan,1500);return}
  showScan(sc);
  if(sc.ready){await save();await load();note('Photos checked · '+counts())}else scanT=setTimeout(watchScan,700);
}
/* opening batches */
function showBusy(title,text,pct){$('busy').style.display=title?'flex':'none';if(!title)return;
  $('busyTitle').textContent=title;$('busyText').textContent=text||'';$('busyFill').style.width=(pct||0)+'%'}
function showWelcome(r){
  document.body.classList.add('nobatch');
  $('folder').textContent='';
  $('recent').innerHTML=r.recent.length?'<h3>Recent batches</h3>'+r.recent.map(b=>`<div class="rec" onclick="openFolder(${jattr(b.folder)})">
      <div><b>${esc(b.name)}</b><div class="path">${esc(b.folder)}</div></div><div class="hint">${b.photos} photos · ${esc(b.when)}</div></div>`).join(''):'';
}
async function openFolder(folder,only){
  showBusy('Opening…',folder);
  try{await save();await api('/api/open',{folder,only});sel=new Set();last=null;await load();
    note(only||window._fromDrop?`Opened ${items.length} photo${items.length==1?'':'s'} right where they are — nothing was copied.`:'Opened · '+counts(),!!(only||window._fromDrop))}
  catch(e){note(e.message);alert(e.message)}
  finally{showBusy()}
}
function openPath(){const v=$('pathIn').value.trim();if(v)openFolder(v)}
async function pickFolder(){
  showBusy('Choose a folder','A folder window has opened. If you don’t see it, it may be behind this browser window.',0);
  try{const r=await api('/api/pick_folder',{});if(r.ok){sel=new Set();last=null;await load();note('Opened · '+counts())}}
  catch(e){note(e.message);alert(e.message)}
  finally{showBusy()}
}
const isJpeg=n=>/\.jpe?g$/i.test(n)&&!n.startsWith('.');
function readAll(reader){return new Promise((ok,bad)=>{const out=[];const next=()=>reader.readEntries(es=>{if(!es.length)return ok(out);out.push(...es);next()},bad);next()})}
async function walk(entry,path,out){
  if(entry.isFile){const f=await new Promise((ok,bad)=>entry.file(ok,bad));out.push({file:f,path:path+f.name})}
  else if(entry.isDirectory){for(const e of await readAll(entry.createReader()))await walk(e,path+entry.name+'/',out)}
}
async function collect(dt){
  const entries=[...dt.items].filter(i=>i.kind=='file').map(i=>i.webkitGetAsEntry&&i.webkitGetAsEntry()).filter(Boolean);
  if(!entries.length)return {list:[...dt.files].map(f=>({file:f,path:f.name})),folder:''};
  const out=[];for(const e of entries)await walk(e,'',out);
  const folder=entries.length==1&&entries[0].isDirectory?entries[0].name:'';
  return {list:out,folder};
}
async function chooseAddOrNew(){
  return new Promise(res=>{window._addNew=res;
    panel(`<h3>Add these photos?</h3><p>This batch was made from photos you dropped in, so StockUp can add these to it.</p>
      <p><button class="primary" onclick="closePanel();_addNew(true)">Add to this batch</button> <button onclick="closePanel();_addNew(false)">Open as a new batch</button></p>`)});
}
async function openDropped(list,folderName){
  const photos=list.filter(x=>isJpeg(x.file.name)),others=list.length-photos.length;
  if(!photos.length)return alert('None of those are JPEG photos. StockUp works with JPEGs.');
  if(dirty)await save();
  // 1. are they already on this computer? then open them right there, nothing copied
  const nested=photos.some(x=>x.path.split('/').length>(folderName?2:1));
  if(!nested){
    showBusy('Looking for these photos on your computer…','So they can be opened where they are, without copying.',0);
    try{const r=await api('/api/drop/locate',{folder_name:folderName,files:photos.map(x=>({name:x.file.name,size:x.file.size,mtime:x.file.lastModified}))});
      showBusy();
      if(r.folder){window._fromDrop=true;await openFolder(r.folder,r.only);window._fromDrop=false;
        if(others)note(note_txt()+` (${others} non-JPEG file${others>1?'s were':' was'} left out)`,true);return}}
    catch(e){showBusy()}
  }
  // 2. not found: copy them into a batch folder
  let add=false;
  if(!document.body.classList.contains('nobatch')&&window._dropped)add=await chooseAddOrNew();
  let start;try{start=await api('/api/drop/start',{add})}catch(e){return alert(e.message)}
  let done=0,failed=0;const q=photos.slice();
  const work=async()=>{while(q.length){const x=q.shift();
    try{const r=await fetch(`/api/drop/file?token=${start.token}&name=${encodeURIComponent(x.file.name)}`,{method:'POST',body:x.file});if(!r.ok)failed++}catch(e){failed++}
    done++;showBusy(start.adding?'Adding photos to this batch…':'Copying photos into a new batch…',`${done} of ${photos.length} · ${start.folder}`,100*done/photos.length)}};
  showBusy('Copying photos…','',0);
  await Promise.all([work(),work(),work()]);
  try{await api('/api/drop/finish',{token:start.token});sel=new Set();last=null;await load();
    note(`${start.adding?'Added':'Copied'} ${done-failed} photo${done-failed==1?'':'s'} ${start.adding?'to this batch':'into '+start.folder} (StockUp couldn’t find where they live on your computer). Your originals are where they were.`+
      (failed?` ${failed} couldn’t be copied.`:'')+(others?` ${others} non-JPEG file${others>1?'s were':' was'} left out.`:''),true)}
  catch(e){alert(e.message)}
  finally{showBusy()}
}
const note_txt=()=>$('msg').textContent;
function filesChosen(inp){const list=[...inp.files].map(f=>({file:f,path:f.name}));inp.value='';openDropped(list,'')}
let dragDepth=0;
const hasFiles=e=>e.dataTransfer&&[...(e.dataTransfer.types||[])].includes('Files');
window.addEventListener('dragenter',e=>{if(!hasFiles(e))return;e.preventDefault();dragDepth++;
  $('droplayHint').textContent=document.body.classList.contains('nobatch')?'Photos or folders':'They open as a new batch (or can be added to a batch made from dropped photos)';
  $('droplay').style.display='flex'});
window.addEventListener('dragover',e=>{if(hasFiles(e))e.preventDefault()});
window.addEventListener('dragleave',e=>{if(!hasFiles(e))return;dragDepth=Math.max(0,dragDepth-1);if(!dragDepth)$('droplay').style.display='none'});
window.addEventListener('drop',async e=>{if(!hasFiles(e))return;e.preventDefault();dragDepth=0;$('droplay').style.display='none';
  if(lastStatus&&lastStatus.running)return alert('Wait for the upload to finish before opening other photos.');
  const {list,folder}=await collect(e.dataTransfer);openDropped(list,folder)});

async function load(){
  const r=await api('/api/items');opts=r.options;VERSION=r.version;FROZEN=r.frozen;PILLOW=r.pillow;
  if(r.no_batch){items=[];clearTimeout(scanT);scanT=null;showWelcome(r);return}
  document.body.classList.remove('nobatch');items=r.items;window._dropped=r.dropped;
  $('folder').textContent=r.folder.split(/[\\/]/).filter(Boolean).pop()+' · '+items.length+' photos'+(r.only?' picked from this folder':'');$('folder').title=r.folder;
  render();showScan(r.scan);
  if(!r.scan.ready&&!scanT)watchScan();
}
window.addEventListener('beforeunload',()=>{if(dirty)save()});
window.addEventListener('pagehide',()=>{try{navigator.sendBeacon('/api/bye','')}catch(e){}});
document.addEventListener('visibilitychange',()=>{if(!document.hidden)fetch('/api/ping').catch(()=>{})});
setInterval(()=>fetch('/api/ping').catch(()=>{}),20000);
(async()=>{fill();try{await load();if(items.length)note('Ready · '+counts());await loadSites();checkUpdate(false);
  const st=await api('/api/status');if(st.running){poll=setInterval(status,1000);status()}}catch(e){note(e.message)}})();
</script></body></html>"""


# --------------------------------------------------------------------------
# Opening batches: recent folders, choosing a folder, and photos dropped in
# --------------------------------------------------------------------------

def drop_root():
    """Where photos dragged onto the page are copied: Pictures/StockUp Batches."""
    pics = os.path.join(os.path.expanduser("~"), "Pictures")
    base = pics if os.path.isdir(pics) else os.path.expanduser("~")
    return os.path.join(base, "StockUp Batches")


def in_drop_root(folder):
    root = os.path.abspath(drop_root())
    return os.path.abspath(folder).startswith(root + os.sep)


def new_drop_folder():
    root = drop_root()
    os.makedirs(root, exist_ok=True)
    name = f"Batch {datetime.now():%Y-%m-%d %H.%M}"
    path, n = os.path.join(root, name), 2
    while os.path.exists(path):
        path, n = os.path.join(root, f"{name} ({n})"), n + 1
    os.makedirs(path)
    return path


def clean_photo_name(name):
    """A dropped file's own name (never a path), and only JPEGs."""
    name = os.path.basename(str(name).replace("\\", "/")).strip()
    name = "".join(ch for ch in name if ch >= " " and ch not in '<>:"|?*')
    if not name or name.startswith(".") or not name.lower().endswith(IMAGE_EXT):
        return ""
    return name


def save_dropped(folder, name, read_chunk, size):
    """Write one dropped photo into a batch folder. Never overwrites: an identical file is
    left as it is, a different file with the same name gets ' (2)' added."""
    name = clean_photo_name(name)
    if not name:
        return {"skipped": "not a JPEG"}
    if size > 1024 ** 3:
        return {"skipped": "bigger than 1 GB"}
    tmp = os.path.join(folder, f".incoming-{os.getpid()}-{threading.get_ident()}.part")
    h, left = hashlib.sha256(), size
    try:
        with open(tmp, "wb") as fh:
            while left > 0:
                chunk = read_chunk(min(left, 1 << 20))
                if not chunk:
                    raise IOError("the file didn't arrive completely")
                fh.write(chunk)
                h.update(chunk)
                left -= len(chunk)
        digest = h.hexdigest()
        stem, ext = os.path.splitext(name)
        target, n = os.path.join(folder, name), 2
        while os.path.exists(target):
            if file_sha256(target) == digest:
                os.remove(tmp)
                return {"saved": os.path.basename(target), "same": True}
            target, n = os.path.join(folder, f"{stem} ({n}){ext}"), n + 1
        os.replace(tmp, target)
        return {"saved": os.path.basename(target)}
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _search_roots():
    """Places a dropped folder or photo is likely to be, most likely first."""
    home = os.path.expanduser("~")
    roots = []
    for f in load_options()["recent"]:
        roots += [f, os.path.dirname(f)]
    for name in ("Pictures", "Desktop", "Downloads", "Documents", "Dropbox", "Google Drive",
                 "OneDrive", "iCloud Drive"):
        roots.append(os.path.join(home, name))
    roots += [os.path.join(home, n) for n in sorted(os.listdir(home)) if n.startswith("OneDrive")] \
        if os.path.isdir(home) else []
    roots.append(os.path.join(home, "Library", "Mobile Documents", "com~apple~CloudDocs"))
    if sys.platform == "darwin" and os.path.isdir("/Volumes"):
        roots += [os.path.join("/Volumes", v) for v in sorted(os.listdir("/Volumes"))]
    if os.name == "nt":
        roots += [f"{d}:\\" for d in "DEFGHIJKLMNOPQRSTUVWXYZ" if os.path.exists(f"{d}:\\")]
    roots.append(home)
    seen, out = set(), []
    for r in roots:
        r = os.path.abspath(r)
        if r not in seen and os.path.isdir(r):
            seen.add(r)
            out.append(r)
    return out


_SKIP_DIRS = {"library", "appdata", "node_modules", "$recycle.bin", "system volume information",
              "windows", "program files", "program files (x86)", "applications", OUT_NAME}
_SKIP_SUFFIX = (".photoslibrary", ".lrdata", ".app", ".photolibrary", ".aplibrary")


def locate_dropped(folder_name, files, budget_seconds=4.0, max_dirs=40000):
    """Find where dropped photos already live on this computer, so they can be opened in
    place instead of copied. files: [{"name", "size", "mtime" (ms)}] - the browser tells us
    these, but not the folder. Returns (folder, only) or (None, None). Every photo must
    match by name and size (and date, when the browser gave one)."""
    want = {}
    for f in files:
        n = clean_photo_name(f.get("name", ""))
        if n:
            want[n] = (int(f.get("size", -1)), float(f.get("mtime", 0)) / 1000.0)
    if not want:
        return None, None

    def matches(d):
        for n, (size, mtime) in want.items():
            try:
                st = os.stat(os.path.join(d, n))
            except OSError:
                return False
            if st.st_size != size or (mtime and abs(st.st_mtime - mtime) > 2.5):
                return False
        return True

    end, visited = time.time() + budget_seconds, 0
    for root in _search_roots():
        for d, subdirs, _ in os.walk(root):
            visited += 1
            if visited > max_dirs or time.time() > end:
                return None, None
            subdirs[:] = [s for s in subdirs if not s.startswith(".")
                          and s.lower() not in _SKIP_DIRS and not s.lower().endswith(_SKIP_SUFFIX)]
            if folder_name and os.path.basename(d) != folder_name:
                continue
            if matches(d):
                jpgs = {x for x in os.listdir(d) if x.lower().endswith(IMAGE_EXT) and not x.startswith(".")}
                return d, (None if jpgs == set(want) else sorted(want))
    return None, None


def import_paths(paths):
    """Folders or photos dropped on the app's icon. A folder, or photos from one folder, open
    right where they are. Only photos gathered from several places are copied into a new
    batch folder (your originals stay where they are). Returns (folder, only)."""
    dirs = [p for p in paths if os.path.isdir(p)]
    if len(dirs) == 1 and len(paths) == 1:
        return dirs[0], None
    photos = [p for p in paths if os.path.isfile(p) and clean_photo_name(p)]
    if photos and not dirs and len({os.path.dirname(p) for p in photos}) == 1:
        return os.path.dirname(photos[0]), sorted(os.path.basename(p) for p in photos)
    files = []
    for p in paths:
        if os.path.isdir(p):
            for root, _, names in os.walk(p):
                files += [os.path.join(root, n) for n in sorted(names)]
        elif os.path.isfile(p):
            files.append(p)
    files = [f for f in files if clean_photo_name(f)]
    if not files:
        return "", None
    folder = new_drop_folder()  # photos from several places: gathered into one batch
    for f in files:
        with open(f, "rb") as fh:
            save_dropped(folder, os.path.basename(f), fh.read, os.path.getsize(f))
    return folder, None


def remember_recent(folder):
    recent = [f for f in load_options()["recent"] if f != folder]
    save_options({"recent": [folder] + recent[:9]})


def recent_batches():
    out = []
    for f in load_options()["recent"]:
        if not os.path.isdir(f):
            continue
        try:
            n = sum(1 for x in os.listdir(f) if x.lower().endswith(IMAGE_EXT) and not x.startswith("."))
            when = os.path.getmtime(os.path.join(f, STATE_NAME)) if os.path.exists(
                os.path.join(f, STATE_NAME)) else os.path.getmtime(f)
        except OSError:
            continue
        out.append({"folder": f, "name": os.path.basename(f.rstrip("/\\")) or f, "photos": n,
                    "when": datetime.fromtimestamp(when).strftime("%b %d, %Y"),
                    "dropped": in_drop_root(f)})
    return out


def ask_for_folder():
    """Your computer's own 'choose a folder' window. Runs as a separate process, because
    macOS only allows those windows from a program's main thread."""
    import subprocess
    try:
        if sys.platform == "darwin":
            r = subprocess.run(["osascript", "-e", "activate", "-e",
                                'POSIX path of (choose folder with prompt '
                                '"Choose the folder with this batch of photos")'],
                               capture_output=True, text=True, timeout=3600)
            return r.stdout.strip() if r.returncode == 0 else ""
        out = os.path.join(CONF_DIR, f"_picked_{os.getpid()}.txt")
        args = [sys.executable] + ([] if FROZEN else [os.path.abspath(__file__)]) + ["--pick-folder", out]
        subprocess.run(args, timeout=3600)
        try:
            with open(out, encoding="utf-8") as fh:
                return fh.read().strip()
        finally:
            if os.path.exists(out):
                os.remove(out)
    except Exception:
        return ""


class App:
    """What the web page talks to. A batch may or may not be open."""

    def __init__(self, batch=None):
        self.batch, self.uploader = None, None
        self.build_state = {"running": False, "done": 0, "total": 0, "current": "",
                            "result": None, "error": None}
        self.drops = {}  # upload tokens -> the batch folder they write into
        self.last_ping = time.time()
        self.closed_at = None  # when the page said it was closing
        if batch:
            self.set_batch(batch)

    def busy(self):
        return bool((self.uploader and self.uploader.running) or self.build_state["running"])

    def set_batch(self, batch):
        self.batch, self.uploader = batch, Uploader(batch)
        self.build_state.update(running=False, done=0, total=0, current="", result=None, error=None)
        remember_recent(batch.folder)

    def open(self, folder, only=None):
        if self.busy():
            raise RuntimeError("Wait for the current upload or file-making to finish first.")
        folder = os.path.abspath(os.path.expanduser(str(folder).strip().strip('"').strip("'")))
        if not os.path.isdir(folder):
            raise RuntimeError(f"That folder doesn't exist: {folder}")
        only = [clean_photo_name(n) for n in only] if only else None
        self.set_batch(Batch(folder, background=True, only=[n for n in only if n] if only else None))

    def close(self):
        if self.busy():
            raise RuntimeError("Wait for the current upload or file-making to finish first.")
        self.batch, self.uploader = None, None

    def status(self):
        return self.uploader.status() if self.uploader else {"running": False, "sites": {}}

    def start_build(self):
        if self.build_state["running"]:
            return
        self.build_state.update(running=True, done=0, total=0, current="", result=None, error=None)
        batch = self.batch

        def prog(d, t, cur):
            self.build_state.update(done=d, total=t, current=cur)

        def run():
            try:
                self.build_state["result"] = build_outputs(batch, prog)
            except Exception as e:
                self.build_state["error"] = str(e)
            self.build_state["running"] = False
        threading.Thread(target=run, daemon=True).start()


NEEDS_BATCH = {"/api/scan", "/api/save", "/api/autocats", "/api/fix", "/api/build", "/api/preview",
               "/api/upload", "/api/send_sheet"}


def make_handler(app):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json", cache=False):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            # thumbnails are kept by the browser (their address changes if the photo does);
            # everything else is always fresh
            self.send_header("Cache-Control", "private, max-age=86400" if cache else "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self):
            n = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(n) if n else b""
            return json.loads(raw) if raw.strip() else {}

        def _host_ok(self):
            # Only StockUp's own page may talk to StockUp: blocks other websites, including
            # tricks that point a web address at your computer.
            port = self.server.server_address[1]
            if self.headers.get("Host", "") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
                self._send(403, {"error": "forbidden"})
                return False
            return True

        def _same_origin(self):
            if not self._host_ok():
                return False
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).netloc != self.headers.get("Host", ""):
                self._send(403, {"error": "forbidden"})
                return False
            return True

        def do_GET(self):
            if not self._host_ok():
                return
            u = urlparse(self.path)
            p = u.path
            app.last_ping = time.time()
            batch = app.batch
            try:
                if p in NEEDS_BATCH and not batch:
                    return self._send(200, {"error": "Open a batch first."})
                if p == "/":
                    page = PAGE.replace("__SS__", json.dumps(SHUTTERSTOCK_CATEGORIES)) \
                        .replace("__AD__", json.dumps({str(k): v for k, v in ADOBE_CATEGORIES.items()})) \
                        .replace("__SITES__", json.dumps({k: {"label": v["label"], "short": v["short"],
                                                              "ftp": bool(v["ftp"])}
                                                          for k, v in SITES.items()})) \
                        .replace("__FIXES__", json.dumps(FIXES)) \
                        .replace("__DT__", json.dumps(DREAMSTIME_CATEGORIES))
                    return self._send(200, page, "text/html; charset=utf-8")
                if p == "/api/scan":
                    return self._send(200, batch.scan)
                if p == "/api/items" and not batch:
                    return self._send(200, {"no_batch": True, "recent": recent_batches(),
                                            "drop_root": drop_root(), "options": load_options(),
                                            "version": VERSION, "frozen": FROZEN,
                                            "pillow": have_pillow()})
                if p == "/api/items":
                    return self._send(200, {"folder": batch.folder, "items": batch.items(),
                                            "dropped": in_drop_root(batch.folder),
                                            "only": bool(batch.only),
                                            "scan": batch.scan,
                                            "options": load_options(), "version": VERSION,
                                            "frozen": FROZEN, "pillow": have_pillow()})
                if p == "/api/sites":
                    return self._send(200, sites_summary())
                if p == "/api/logins":
                    return self._send(200, logins_summary())
                if p == "/api/status":
                    return self._send(200, app.status())
                if p == "/api/build_status":
                    return self._send(200, app.build_state)
                if p == "/api/ping":
                    app.closed_at = None
                    return self._send(200, {"ok": True})
                if p == "/api/version":
                    force = "force=1" in u.query
                    try:
                        return self._send(200, check_update(force=force))
                    except Exception as e:
                        if force:
                            return self._send(200, {"error": str(e)})
                        return self._send(200, {"current": VERSION, "available": False})
                if p.startswith("/thumb/"):
                    f = unquote(p[len("/thumb/"):])
                    if not batch or f not in batch.meta:
                        return self._send(404, "not found", "text/plain")
                    return self._send(200, batch.thumbnail(f), "image/jpeg", cache=True)
            except Exception as e:
                return self._send(500, {"error": str(e)})
            self._send(404, "not found", "text/plain")

        def do_POST(self):
            if not self._same_origin():
                return
            u = urlparse(self.path)
            p = u.path
            batch = app.batch
            try:
                if p == "/api/drop/file":  # a photo's bytes, sent straight from the page
                    q = dict(x.split("=", 1) for x in u.query.split("&") if "=" in x)
                    folder = app.drops.get(q.get("token", ""))
                    if not folder:
                        return self._send(400, {"error": "This drop has expired. Drop the photos again."})
                    size = int(self.headers.get("Content-Length", 0) or 0)
                    return self._send(200, save_dropped(folder, unquote(q.get("name", "")),
                                                        self.rfile.read, size))
                body = self._json()
                if p in NEEDS_BATCH and not batch:
                    return self._send(200, {"error": "Open a batch first."})
                if p == "/api/open":
                    app.open(body.get("folder", ""), body.get("only"))
                    return self._send(200, {"ok": True})
                if p == "/api/drop/locate":
                    folder, only = locate_dropped(str(body.get("folder_name") or ""),
                                                  body.get("files") or [])
                    return self._send(200, {"folder": folder, "only": only})
                if p == "/api/pick_folder":
                    if app.busy():
                        return self._send(200, {"error": "Wait for the current upload to finish first."})
                    folder = ask_for_folder()
                    if not folder:
                        return self._send(200, {"cancelled": True})
                    app.open(folder)
                    return self._send(200, {"ok": True})
                if p == "/api/drop/start":
                    if app.busy():
                        return self._send(200, {"error": "Wait for the current upload to finish first."})
                    adding = bool(body.get("add")) and batch and in_drop_root(batch.folder)
                    folder = batch.folder if adding else new_drop_folder()
                    token = secrets.token_hex(16)
                    app.drops[token] = folder
                    return self._send(200, {"token": token, "folder": folder, "adding": bool(adding)})
                if p == "/api/drop/finish":
                    folder = app.drops.pop(body.get("token", ""), None)
                    if not folder:
                        return self._send(200, {"error": "This drop has expired."})
                    if not any(f.lower().endswith(IMAGE_EXT) for f in os.listdir(folder)):
                        if not os.listdir(folder):
                            os.rmdir(folder)  # nothing came in: don't leave an empty folder
                        return self._send(200, {"error": "None of those were JPEG photos."})
                    app.open(folder)
                    return self._send(200, {"ok": True, "folder": folder})
                if p in ("/api/build", "/api/preview", "/api/upload") and not batch.scan["ready"]:
                    return self._send(200, {"error": f"StockUp is still checking your photos "
                                                     f"({batch.scan['done']} of {batch.scan['total']}). "
                                                     f"Try again in a moment."})
                if p == "/api/pause":
                    if app.uploader:
                        app.uploader.pause(body.get("paused"))
                    return self._send(200, {"ok": True})
                if p == "/api/save":
                    batch.save(body)
                    return self._send(200, {"ok": True})
                if p == "/api/autocats":
                    return self._send(200, {"changed": batch.autopick(body.get("files") or [])})
                if p == "/api/fix":
                    n = batch.act(body.get("files") or [], body.get("site", "*"),
                                  body.get("code", "*"), body.get("action", "apply"))
                    return self._send(200, {"changed": n})
                if p == "/api/options":
                    before = load_options()
                    opts = save_options(body)
                    rules = ("editorial_keyword", "editorial_label", "flag_formatted")
                    n = batch.reapply_auto() if batch and any(before[k] != opts[k] for k in rules) else 0
                    if batch and opts["auto_categories"] and not before["auto_categories"]:
                        with batch.lock:
                            for f in batch.files:
                                batch._autofill(f, batch.state[f], overwrite=False)
                        batch.save()
                    return self._send(200, {"options": opts, "newly_flagged": n})
                if p == "/api/logins":
                    return self._send(200, save_logins(body))
                if p == "/api/build":
                    app.start_build()
                    return self._send(200, {"started": True})
                if p == "/api/test":
                    return self._send(200, test_sites())
                if p == "/api/preview":
                    ready = [s["name"] for s in sites_summary() if s["ready"]]
                    return self._send(200, upload_preview(
                        batch, [n for n in body.get("sites", []) if n in ready]))
                if p == "/api/stop":
                    if app.uploader:
                        app.uploader.stop()
                    return self._send(200, {"ok": True})
                if p == "/api/send_sheet":
                    return self._send(200, send_sheet(batch, body.get("site", "")))
                if p == "/api/upload":
                    if app.uploader.running:
                        return self._send(200, {"started": True})
                    ready = [s["name"] for s in sites_summary() if s["ready"]]
                    pick = [n for n in body.get("sites", []) if n in ready]
                    if not pick:
                        return self._send(200, {"started": False, "reason":
                                                "None of the ticked sites has a login yet. "
                                                "Add it under Logins."})
                    save_options({"upload_sites": pick})
                    return self._send(200, {"started": app.uploader.start(only=pick)})
                if p == "/api/update":
                    if app.busy():
                        return self._send(200, {"updated": False,
                                                "message": "Wait for the upload to finish, then update."})
                    r = install_update()
                    self._send(200, r)
                    if r.get("updated"):
                        restart_program(self.server, batch.folder if batch else None)
                    return
                if p == "/api/another":
                    if app.busy():
                        return self._send(200, {"ok": False,
                                                "message": "Wait for the upload to finish first."})
                    app.close()
                    return self._send(200, {"ok": True})
                if p == "/api/bye":  # the page is closing (or reloading)
                    app.closed_at = time.time()
                    return self._send(200, {"ok": True})
                if p == "/api/quit":
                    if app.busy() and not body.get("force"):
                        return self._send(200, {"ok": False, "message":
                                                "An upload is still running. Quit anyway?"})
                    self._send(200, {"ok": True})
                    threading.Timer(0.3, lambda: os._exit(0)).start()
                    return
            except Exception as e:
                return self._send(500, {"error": str(e)})
            self._send(404, {"error": "not found"})
    return H


# --------------------------------------------------------------------------
# Starting up
# --------------------------------------------------------------------------

def pick_folder():
    try:
        import tkinter
        from tkinter import filedialog
        root = tkinter.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass
        folder = filedialog.askdirectory(title="StockUp: choose the folder with this batch of photos")
        root.destroy()
        return folder
    except Exception:
        if sys.stdin and sys.stdin.isatty():
            return input("Drag the batch folder here and press Enter: ").strip().strip('"').strip("'")
        return ""


def self_test(out_path):
    """Used by the app build to prove the bundle works: python stockup.py --selftest out.txt"""
    lines = [f"StockUp {VERSION} frozen={FROZEN}"]
    for mod in ("PIL", "PIL.ImageCms", "keyring", "paramiko", "tkinter"):
        try:
            __import__(mod)
            lines.append(f"{mod}: ok")
        except Exception as e:
            lines.append(f"{mod}: FAILED {e}")
    try:
        from PIL import Image
        tmp = os.path.join(CONF_DIR, "_selftest_gps.jpg")
        ex = Image.Exif()
        ex[0x8825] = {1: "N", 2: (40.0, 44.0, 54.0), 3: "W", 4: (73.0, 59.0, 10.0)}
        Image.new("RGB", (64, 64), (10, 20, 30)).save(tmp, exif=ex.tobytes())
        raw = open(tmp, "rb").read()
        clean = strip_gps_bytes(raw)
        with open(tmp, "wb") as fh:
            fh.write(clean)
        ok = len(clean) == len(raw) and not read_photo(tmp)[0]["info"]["gps"]
        lines.append(f"gps: {'ok' if ok else 'FAILED still there'}")
        os.remove(tmp)
    except Exception as e:
        lines.append(f"gps: FAILED {e}")
    try:
        from PIL import Image, ImageCms
        tmp = os.path.join(CONF_DIR, "_selftest.jpg")
        Image.new("RGB", (64, 64), (200, 100, 50)).save(tmp)
        make_copy(tmp, tmp + ".out.jpg", {"srgb"}, {})
        lines.append(f"copy: ok {read_photo(tmp + '.out.jpg')[0]['info']['color']}")
        os.remove(tmp)
        os.remove(tmp + ".out.jpg")
    except Exception as e:
        lines.append(f"copy: FAILED {e}")
    lines.append(f"keychain: {keychain_name() or 'none'}")
    with open(out_path, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    return 1 if any("FAILED" in l for l in lines) else 0


def main(argv):
    if len(argv) > 1 and argv[1] == "--pick-folder":  # the folder window, run by ask_for_folder
        folder = pick_folder()
        if len(argv) > 2:
            with open(argv[2], "w", encoding="utf-8") as fh:
                fh.write(folder or "")
        else:
            print(folder or "")
        return 0
    if len(argv) > 1 and argv[1] == "--selftest":
        return self_test(argv[2] if len(argv) > 2 else "selftest.txt")
    if len(argv) > 1 and argv[1] in ("-h", "--help"):
        print(__doc__)
        print("Other commands:\n"
              "  python stockup.py build  FOLDER                 make the site files only\n"
              "  python stockup.py upload FOLDER [--sites a,b]   upload without the page\n"
              "  python stockup.py test                          check the logins\n"
              "  python stockup.py update                        install the latest version")
        return 0
    args = [a for a in argv[1:] if not a.startswith("-psn_")]  # macOS adds this to apps
    cmd = args[0] if args else ""
    if cmd == "test":
        for r in test_sites():
            print(f"{r['label']:14} {r['result']}")
            if r.get("listing"):
                print("               on server:", ", ".join(r["listing"]))
        return 0
    if cmd == "update":
        try:
            print(install_update()["message"])
        except Exception as e:
            print("Update failed:", e)
            return 1
        return 0
    load_sites()
    if cmd not in ("build", "upload"):
        return serve(args)
    folder = args[1] if len(args) > 1 else ""
    if not os.path.isdir(folder):
        print(f"Not a folder: {folder or '(none given)'}")
        return 1
    print("Reading your photos…", end=" ", flush=True)
    batch = Batch(folder)
    print(f"{len(batch.files)} JPEGs found.")
    if cmd == "build":
        r = build_outputs(batch)
        for s in r["sites"]:
            print(f"{s['label']:14} {s['included']} photos, {s['skipped']} skipped -> {s['folder']}")
        for w in r["warnings"]:
            print("  !", w)
        return 0
    if cmd == "upload":
        only = None
        if "--sites" in args:
            i = args.index("--sites")
            only = [s.strip() for s in (args[i + 1] if i + 1 < len(args) else "").split(",") if s.strip()]
        up = Uploader(batch)
        if not up.start(only=only):
            print("No sites to upload to. Add logins first:", CONFIG_PATH)
            return 1
        while up.status()["running"]:
            time.sleep(2)
            line = "  ".join(f"{n}: {p['done']}/{p['total']} {p['state']}"
                             for n, p in up.status()["sites"].items())
            print("\r" + line + " " * 10, end="", flush=True)
        print()
        for n, p in up.status()["sites"].items():
            for e in p["errors"]:
                print(f"  {n}: {e}")
        return 0


def serve(paths):
    """Open the page. With no folder, it starts on the welcome screen. Folders or photos
    dropped on the app's icon (or given on the command line) open straight away."""
    app = App()
    if paths:
        folder, only = import_paths([os.path.abspath(p) for p in paths])
        if folder:
            print("Reading your photos…", end=" ", flush=True)
            app.open(folder, only)
            print(f"{len(app.batch.files)} JPEGs found.")
        else:
            print("No JPEG photos found in what was given; opening the welcome screen.")
    restarting = os.environ.pop("STOCKUP_PORT", "")
    port = int(restarting) if restarting.isdigit() else 0
    server = None
    for _ in range(20):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
            break
        except OSError:
            time.sleep(0.25)
    if server is None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
        restarting = ""
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"StockUp {VERSION} is open in your browser: {url}\n"
          "Keep this window open while you work. Close it (or press Ctrl+C) to quit.")
    if not restarting:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    if FROZEN:  # the app has no window of its own: quit a while after the page is closed
        def watchdog():
            while True:
                time.sleep(30)
                # Quit a minute after the page is closed. A browser may freeze a background
                # tab for a long time, so silence alone only counts after 6 hours.
                closed = app.closed_at and app.closed_at >= app.last_ping - 1 \
                    and time.time() - app.closed_at > 60
                if (closed or time.time() - app.last_ping > 6 * 3600) and not app.busy():
                    os._exit(0)
        threading.Thread(target=watchdog, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBye.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
