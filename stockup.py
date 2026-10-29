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
import csv
import ftplib
import json
import math
import os
import re
import shutil
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

VERSION = "1.2.1"
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
        "csv_note": "Dreamstime reads your titles and keywords from the photos. "
                    "Tick editorial for the files in editorial_list.txt.",
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
    """(DateTimeOriginal as YYYY-MM-DD or '', embedded thumbnail bytes or None)"""
    if not payload.startswith(b"Exif\x00\x00"):
        return "", None
    t = payload[6:]
    if len(t) < 8:
        return "", None
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
        return date, thumb
    except Exception:
        return "", None


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
            scale = sum(vals) * 100 / _STD_LUMA_SUM
            q = (200 - scale) / 2 if scale <= 100 else 5000 / scale
            return max(1, min(100, round(q)))
        i += 1 + size
    return None


def read_photo(path):
    xmp, iptc, exif_date, thumb = {}, {}, "", None
    info = {"width": 0, "height": 0, "components": 3, "progressive": False}
    icc = b""
    try:
        for marker, payload in jpeg_segments(path):
            if marker == 0xE1 and payload.startswith(b"http://ns.adobe.com/xap/1.0/"):
                xmp = parse_xmp(payload)
            elif marker == 0xE1 and payload.startswith(b"Exif"):
                exif_date, thumb = parse_exif(payload)
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

class Batch:
    def __init__(self, folder):
        self.folder = os.path.abspath(folder)
        self.out = os.path.join(self.folder, OUT_NAME)
        self.state_path = os.path.join(self.folder, STATE_NAME)
        self.thumbs, self.meta = {}, {}
        self.lock = threading.RLock()
        self.files = sorted(
            (f for f in os.listdir(self.folder)
             if f.lower().endswith(IMAGE_EXT) and not f.startswith(".")),
            key=str.lower)
        for f in self.files:
            m, thumb = read_photo(os.path.join(self.folder, f))
            self.meta[f] = m
            if thumb:
                self.thumbs[f] = thumb
        self.state = self._load_state()

    # ---- state -------------------------------------------------------------
    def _load_state(self):
        st = {}
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, encoding="utf-8") as fh:
                    st = json.load(fh).get("files", {})
            except (OSError, ValueError):
                st = {}
        opts = load_options()
        for f in self.files:
            m, s = self.meta[f], st.setdefault(f, {})
            if "editorial" not in s:
                s["editorial"] = bool(self.auto_reason(f, opts))
            s.setdefault("ss_cats", [])
            s.setdefault("adobe_cat", None)
            s.setdefault("ed_city", m["city"])
            s.setdefault("ed_region", m["country"] or m["state"])
            s.setdefault("ed_date", m["date"])
            s.setdefault("fixes", [])   # ["adobe:shorten_title", ...]
            s.setdefault("skip", [])    # sites you chose not to send this photo to
            s.setdefault("force", [])   # sites to send to even though a check says no
        return {f: st[f] for f in self.files}

    def save(self, incoming=None):
        with self.lock:
            for f, s in (incoming or {}).items():
                if f not in self.state:
                    continue
                cat = s.get("adobe_cat")
                self.state[f].update({
                    "editorial": bool(s.get("editorial")),
                    "ss_cats": [c for c in s.get("ss_cats", []) if c in SHUTTERSTOCK_CATEGORIES][:2],
                    "adobe_cat": int(cat) if cat and int(cat) in ADOBE_CATEGORIES else None,
                    "ed_city": str(s.get("ed_city", "")).strip(),
                    "ed_region": str(s.get("ed_region", "")).strip(),
                    "ed_date": str(s.get("ed_date", "")).strip(),
                })
            tmp = self.state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"version": VERSION, "files": self.state}, fh, indent=1)
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
                            if c in COPY_FIXES and not have_pillow():
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
        issues = []

        def issue(level, text, fix=None):
            issues.append({"level": level, "text": text, "fix": fix,
                           "applied": bool(fix and fix in applied),
                           "needs_pillow": bool(fix in COPY_FIXES and not have_pillow())})

        title = m["title"] or m["description"]
        desc = m["description"] or m["title"]
        marker = opts["editorial_keyword"].strip().lower()
        kws = m["keywords"]
        if marker and opts["drop_marker_keyword"]:
            kws = [k for k in kws if k.lower() != marker]
        px = info["width"] * info["height"]
        mp = px / 1e6
        ops, name = set(), f

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

        for c in COPY_FIXES & applied:
            if any(i["fix"] == c for i in issues):
                ops.add(c)

        blocked = any(i["level"] == "block" for i in issues)
        if site in s["skip"]:
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
                "ops": sorted(ops), "user_skipped": site in s["skip"],
                "forced": site in s["force"]}

    def items(self):
        opts = load_options()
        out = []
        for f in self.files:
            m = self.meta[f]
            sites = {}
            for st in opts["my_sites"]:
                p = self.plan(f, st, opts)
                sites[st] = {k: p[k] for k in ("status", "issues", "user_skipped", "forced")}
            out.append({"file": f, "title": m["title"], "description": m["description"],
                        "keywords": m["keywords"], "info": m["info"], "label": m.get("label", ""),
                        "preformatted": looks_editorial(m["description"]) or looks_editorial(m["title"]),
                        "auto": self.auto_reason(f, opts), "sites": sites, **self.state[f]})
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
        stamp = "|".join(plan["ops"]) + f"|{os.path.getmtime(src)}"
        marker = dst + ".stockup"
        try:
            if os.path.exists(dst) and open(marker).read() == stamp:
                return dst
        except OSError:
            pass
        if plan["ops"]:
            make_copy(src, dst, set(plan["ops"]), SITES[site])
        else:
            if os.path.exists(dst):
                os.remove(dst)
            link_or_copy(src, dst)
        with open(marker, "w") as fh:
            fh.write(stamp)
        return dst


# --------------------------------------------------------------------------
# Making fixed copies (needs Pillow). Your original is never changed.
# --------------------------------------------------------------------------

def _meta_segments(src_bytes):
    """Raw EXIF/XMP/IPTC segments of a JPEG, to carry over to a new copy."""
    segs, i = [], 2
    while i + 4 <= len(src_bytes) and src_bytes[i] == 0xFF:
        marker = src_bytes[i + 1]
        if marker in (0xDA, 0xD9):
            break
        ln = int.from_bytes(src_bytes[i + 2:i + 4], "big")
        if marker in (0xE1, 0xED):
            segs.append(src_bytes[i:i + 2 + ln])
        i += 2 + ln
    return b"".join(segs)


def _insert_segments(jpeg, segs):
    i = 2
    if jpeg[2:4] == b"\xff\xe0":  # keep JFIF first
        i = 4 + int.from_bytes(jpeg[4:6], "big")
    return jpeg[:i] + segs + jpeg[i:]


def make_copy(src, dst, ops, rules):
    from PIL import Image
    raw = open(src, "rb").read()
    im = Image.open(BytesIO(raw))
    icc = im.info.get("icc_profile")
    out_icc = None
    if "srgb" in ops or im.mode not in ("RGB",):
        from PIL import ImageCms
        srgb = ImageCms.createProfile("sRGB")
        if icc and im.mode in ("RGB", "CMYK", "L"):
            try:
                im = ImageCms.profileToProfile(im, ImageCms.ImageCmsProfile(BytesIO(icc)),
                                               srgb, outputMode="RGB")
            except Exception:
                im = im.convert("RGB")
        else:
            im = im.convert("RGB")
        out_icc = ImageCms.ImageCmsProfile(srgb).tobytes()
    else:
        out_icc = icc
    if "resize" in ops and rules.get("max_px"):
        px = im.width * im.height
        if px > rules["max_px"]:
            k = math.sqrt(rules["max_px"] / px) * 0.995
            im = im.resize((int(im.width * k), int(im.height * k)), Image.LANCZOS)
    segs = _meta_segments(raw)
    limit = rules.get("max_mb", 10_000) * 1048576 if "smaller" in ops else None
    data = None
    for q in (95, 92, 89, 86, 83, 80):
        buf = BytesIO()
        kw = {"quality": q, "progressive": False, "optimize": True}
        if out_icc:
            kw["icc_profile"] = out_icc
        im.save(buf, "JPEG", **kw)
        data = _insert_segments(buf.getvalue(), segs)
        if limit is None or len(data) <= limit:
            break
    if limit and len(data) > limit:  # still too big at quality 80: scale down a little
        while len(data) > limit:
            im = im.resize((int(im.width * 0.9), int(im.height * 0.9)), Image.LANCZOS)
            buf = BytesIO()
            im.save(buf, "JPEG", quality=85, progressive=False, optimize=True,
                    **({"icc_profile": out_icc} if out_icc else {}))
            data = _insert_segments(buf.getvalue(), segs)
    tmp = dst + ".part"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, dst)


def link_or_copy(src, dst):
    if os.path.exists(dst):
        return
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


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
                link_or_copy(batch.site_file(f, site, p),
                             os.path.join(sd, "editorial" if s["editorial"] else "creative", p["name"]))
            files += ["creative/", "editorial/"]
        if site in ("adobe", "dreamstime", "alamy", "123rf"):
            with open(os.path.join(sd, "editorial_list.txt"), "w", encoding="utf-8") as fh:
                fh.write("\n".join(editorial) + ("\n" if editorial else ""))
            files.append("editorial_list.txt")
        if os.path.isdir(os.path.join(sd, "files")):
            files.append("files/  (fixed copies)")
        summary.append({"site": site, "label": rules["label"], "folder": sd, "files": files,
                        "included": len(included), "skipped": len(skipped),
                        "editorial": len(editorial), "problems": problems,
                        "next": rules["csv_note"]})

    with open(os.path.join(out, "checks.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(report) + "\n" if report else "No problems found.\n")
    return {"folder": out, "sites": summary, "warnings": report, "count": len(batch.files),
            "editorial": sum(1 for f in batch.files if batch.state[f]["editorial"])}


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
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    os.replace(tmp, CONFIG_PATH)


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

class FTPConn:
    def __init__(self, site, timeout=60):
        self.ftp = ftplib.FTP_TLS(timeout=timeout) if site["protocol"] == "ftps" \
            else ftplib.FTP(timeout=timeout)
        self.ftp.connect(site["host"], site["port"])
        self.ftp.login(site["username"], site["password"])
        if site["protocol"] == "ftps":
            self.ftp.prot_p()
        self.ftp.set_pasv(True)
        if site["remote_dir"] not in ("", "/"):
            self.ftp.cwd(site["remote_dir"])

    def listdir(self):
        try:
            return self.ftp.nlst()
        except ftplib.error_perm:
            return []

    def put(self, local, name, progress):
        with open(local, "rb") as fh:
            self.ftp.storbinary("STOR " + name, fh, blocksize=256 * 1024,
                                callback=lambda b: progress(len(b)))
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
    def __init__(self, site, timeout=60):
        try:
            import paramiko
        except ImportError:
            raise RuntimeError("SFTP needs paramiko: run  pip install paramiko")
        self.t = paramiko.Transport((site["host"], site["port"]))
        self.t.banner_timeout = timeout
        self.t.connect(username=site["username"], password=site["password"])
        self.sftp = paramiko.SFTPClient.from_transport(self.t)
        if site["remote_dir"] not in ("", "/"):
            self.sftp.chdir(site["remote_dir"])

    def listdir(self):
        return self.sftp.listdir()

    def put(self, local, name, progress):
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


def connect(site):
    if not site["username"] or not site["password"]:
        raise RuntimeError("login not set yet - add it under Logins")
    return SFTPConn(site) if site["protocol"] == "sftp" else FTPConn(site)


class Uploader:
    def __init__(self, batch):
        self.batch = batch
        self.log_path = os.path.join(batch.folder, UPLOAD_LOG_NAME)
        self.lock = threading.Lock()
        self.progress = {}
        self.running = False

    def _load_log(self):
        try:
            with open(self.log_path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def _mark_done(self, site, f):
        with self.lock:
            log = self._load_log()
            log.setdefault(site, [])
            if f not in log[site]:
                log[site].append(f)
            tmp = self.log_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(log, fh, indent=1)
            os.replace(tmp, self.log_path)

    def status(self):
        with self.lock:
            return {"running": self.running, "sites": json.loads(json.dumps(self.progress))}

    def start(self, only=None):
        if self.running:
            return False
        if only:
            sites = [s for s in load_sites() if s["name"] in only]
        else:
            sites = [s for s in load_sites() if s["enabled"]]
        if not sites:
            return False
        log = self._load_log()
        with self.lock:
            self.running = True
            self.progress = {}
            for s in sites:
                todo = [f for f in self.batch.files if self.batch.plan(f, s["name"])["include"]]
                done = [f for f in todo if f in log.get(s["name"], [])]
                size = sum(os.path.getsize(os.path.join(self.batch.folder, f)) for f in todo)
                self.progress[s["name"]] = {
                    "state": "waiting", "done": len(done), "total": len(todo),
                    "bytes": sum(os.path.getsize(os.path.join(self.batch.folder, f)) for f in done),
                    "total_bytes": size or 1, "current": "", "errors": [], "skipped": len(done),
                    "left_out": len(self.batch.files) - len(todo)}
        threads = [threading.Thread(target=self._run_site, args=(s,), daemon=True) for s in sites]
        for t in threads:
            t.start()

        def waiter():
            for t in threads:
                t.join()
            with self.lock:
                self.running = False
        threading.Thread(target=waiter, daemon=True).start()
        return True

    def _set(self, site, **kw):
        with self.lock:
            self.progress[site].update(kw)

    def _err(self, site, msg):
        with self.lock:
            self.progress[site]["errors"].append(msg)

    def _run_site(self, site):
        name = site["name"]
        done_log = set(self._load_log().get(name, []))
        todo = [f for f in self.batch.files
                if self.batch.plan(f, name)["include"] and f not in done_log]
        conn, failures_in_row, last = None, 0, ""
        for f in todo:
            try:
                plan = self.batch.plan(f, name)
                local = self.batch.site_file(f, name, plan)
            except Exception as e:
                self._err(name, f"{f}: couldn't prepare the file ({e})")
                continue
            ok = False
            for attempt in range(3):
                start_bytes = self.progress[name]["bytes"]
                try:
                    if conn is None:
                        self._set(name, state="connecting")
                        conn = connect(site)
                    self._set(name, state="uploading", current=f)

                    def prog(n, _name=name):
                        with self.lock:
                            self.progress[_name]["bytes"] += n
                    conn.put(local, plan["name"], prog)
                    ok = True
                    break
                except Exception as e:
                    self._set(name, bytes=start_bytes)
                    if conn:
                        conn.close()
                    conn = None
                    last = str(e) or e.__class__.__name__
                    low = last.lower()
                    if "530" in last or "login" in low or "auth" in low or "paramiko" in low:
                        self._set(name, state="error", current="")
                        self._err(name, f"Couldn't sign in: {last}")
                        return
                    time.sleep(2 * (attempt + 1))
            if ok:
                failures_in_row = 0
                self._mark_done(name, f)
                with self.lock:
                    self.progress[name]["done"] += 1
            else:
                failures_in_row += 1
                self._err(name, f"{f}: {last}")
                if failures_in_row >= 5:
                    self._set(name, state="error", current="")
                    self._err(name, "Stopped after 5 failures in a row.")
                    if conn:
                        conn.close()
                    return
        if conn:
            conn.close()
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
    results = []
    my = load_options()["my_sites"]
    for s in load_sites():
        if s["name"] not in my:
            continue
        r = {"name": s["name"], "label": SITES[s["name"]]["label"]}
        if not s["username"]:
            r["result"] = "No login yet"
            results.append(r)
            continue
        try:
            c = connect(s)
            r["listing"] = c.listdir()[:30]
            c.close()
            r["result"] = "OK"
        except Exception as e:
            r["result"] = f"Couldn't connect: {e}"
        results.append(r)
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
input[type=checkbox],input[type=radio]{width:auto;padding:0;margin:0}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:6px;overflow:hidden}
.seg button{border:none;border-radius:0;padding:4px 9px;font-size:12px}
.seg button.on{background:var(--accent);color:var(--on-accent)}
.count{display:inline-block;min-width:18px;padding:0 5px;border-radius:9px;background:var(--warn);color:var(--panel);font-size:11px;font-weight:700;margin-left:4px}
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
    <h1>StockUp</h1><span class="sub" id="folder"></span>
    <button onclick="openChecks()" id="checksBtn">Check photos</button>
    <div class="seg" id="theme" title="Light or dark look">
      <button data-t="auto">Auto</button><button data-t="light">Light</button><button data-t="dark">Dark</button>
    </div>
    <button onclick="openLogins()">Logins</button>
    <button onclick="openSettings()">Settings</button>
    <button onclick="another()">Another batch</button>
    <button onclick="quitApp()">Quit</button>
  </div>
  <div id="banner"></div>
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
    <div class="group" id="ssGroup"><b>Shutterstock category</b>
      <select id="ss1"></select><select id="ss2"></select>
      <button onclick="applySS()">Set</button>
    </div>
    <div class="group" id="adGroup"><b>Adobe category</b><select id="ad"></select><button onclick="applyAd()">Set</button></div>
    <div class="group"><b>Editorial: where &amp; when</b>
      <input id="ecity" placeholder="City"><input id="eregion" placeholder="State or country">
      <input id="edate" type="date"><button onclick="applyLoc()">Set</button>
    </div>
  </div>
</header>
<main><div class="grid" id="grid"></div></main>
<div id="panel"></div>
<footer>
  <span id="msg">Click a photo to pick it. Shift-click to pick a range.</span>
  <div class="sites" id="sites"></div>
  <button onclick="testSites()">Check logins</button>
  <button onclick="build()">Make site files</button>
  <button class="primary" onclick="upload()">Upload</button>
</footer>
<script>
const SS=__SS__, AD=__AD__, SITES=__SITES__, FIXES=__FIXES__;
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
}
const my=()=>opts.my_sites||[];
const hasIssue=it=>Object.values(it.sites).some(s=>s.status=='fix'||s.status=='skip'&&!s.user_skipped);
function visible(){
  const f=$('filter').value;
  return items.filter(it=>f=='all'||(f=='ed'&&it.editorial)||(f=='com'&&!it.editorial)||
    (f=='nocat'&&((my().includes('shutterstock')&&!it.ss_cats.length)||(my().includes('adobe')&&!it.adobe_cat)))||(f=='issues'&&hasIssue(it)));
}
function chips(it){
  const word={ok:'ready',warn:'ready, with a note',fix:'needs a fix',skip:'not sending'};
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
    if(my().includes('shutterstock'))t.push(it.ss_cats.length?`<span class="tag">SS: ${esc(it.ss_cats.join(' + '))}</span>`:'<span class="tag warn">no Shutterstock category</span>');
    if(my().includes('adobe')&&it.adobe_cat)t.push(`<span class="tag">Adobe: ${esc(AD[it.adobe_cat])}</span>`);
    const mp=(it.info.width*it.info.height/1e6).toFixed(1);
    return `<div class="card ${sel.has(it.file)?'sel':''} ${it.editorial?'ed':''}" data-f="${esc(it.file)}" title="${esc(it.auto?'Marked editorial automatically: '+it.auto:'')}">
      <div class="check">${sel.has(it.file)?'✓':''}</div>
      <img class="thumb" loading="lazy" src="/thumb/${encodeURIComponent(it.file)}" alt="">
      <div class="meta"><div class="fn"><span>${esc(it.file)}</span><span>${mp} MP</span></div><div class="tt">${esc(it.description||it.title||'(no title)')}</div>
      <div class="tags">${t.join('')}</div>${chips(it)}
      <button class="link small" data-detail="${esc(it.file)}">Site details</button></div></div>`}).join(''):'<div class="empty">No photos to show here.</div>';
  $('selcount').textContent=sel.size+' picked';
  $('ssGroup').style.display=my().includes('shutterstock')?'':'none';
  $('adGroup').style.display=my().includes('adobe')?'':'none';
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
function applyLoc(){if(!need())return;const c=$('ecity').value.trim(),r=$('eregion').value.trim(),d=$('edate').value;
  if(!c&&!r&&!d)return note('Type a place or pick a date first.');each(it=>{if(c)it.ed_city=c;if(r)it.ed_region=r;if(d)it.ed_date=d})}
function note(t){$('msg').textContent=t}
function counts(){const e=items.filter(i=>i.editorial).length;return `${e} editorial, ${items.length-e} creative`}
function changed(){dirty=true;render();clearTimeout(saveT);saveT=setTimeout(async()=>{await save();await load()},500)}
async function save(){
  if(!dirty)return;
  const body={};items.forEach(it=>body[it.file]={editorial:it.editorial,ss_cats:it.ss_cats,adobe_cat:it.adobe_cat,ed_city:it.ed_city,ed_region:it.ed_region,ed_date:it.ed_date});
  try{await api('/api/save',body);dirty=false;note('Saved · '+counts())}catch(e){note(e.message)}
}
function panel(html,keepScroll){openDetail=null;const p=$('panel');const y=p.scrollTop;p.innerHTML=html+'<p><button onclick="closePanel()">Close</button></p>';p.style.display='block';p.scrollTop=keepScroll?y:0}
function closePanel(){openDetail=null;$('panel').style.display='none'}

/* per-site checks */
function issueRow(i,acts){
  const tag=i.applied?'<span class="lvl done">FIXED</span>':`<span class="lvl ${i.level}">${{block:'WON’T ACCEPT',fix:'FIX',warn:'NOTE',info:'OK'}[i.level]}</span>`;
  return `<div class="issue"><div>${tag}${esc(i.text)}${i.needs_pillow&&!i.applied?'<div class="hint">This fix needs Pillow: run  pip install pillow  (the StockUp app already has it).</div>':''}</div><div class="acts">${acts}</div></div>`;
}
async function act(files,site,code,action){
  try{await save();const r=await api('/api/fix',{files,site,code,action});await load();
    note(r.changed?'Updated.':'Nothing to change.');if(openDetail)showDetail(openDetail,true);else openChecks(true,true)}
  catch(e){note(e.message)}
}
function showDetail(f,keepScroll){
  const it=items.find(i=>i.file==f);if(!it)return;
  let h=`<h3>${esc(f)}</h3><div class="hint">${it.info.width} × ${it.info.height} px · ${(it.info.width*it.info.height/1e6).toFixed(1)} MP · ${(it.info.bytes/1048576).toFixed(1)} MB · ${esc(it.info.color)}${it.info.progressive?' · progressive':''}</div>`;
  for(const s of my()){
    const st=it.sites[s];if(!st)continue;
    const word={ok:'Ready',warn:'Ready, with a note',fix:'Needs a fix',skip:'Not sending'}[st.status];
    const toggle=st.status=='skip'?`<button class="small" onclick="act(${jattr([f])},'${s}','*','include')">Send anyway</button>`
      :`<button class="small" onclick="act(${jattr([f])},'${s}','*','skip')">Don’t send</button>`;
    h+=`<h4><span class="chip ${st.status}">${esc(SITES[s].short)}</span>${esc(lab(s))} — ${word} ${toggle}</h4>`;
    if(!st.issues.length)h+='<div class="hint">Nothing to change.</div>';
    for(const i of st.issues){
      let a='';
      if(i.fix&&!i.applied&&!i.needs_pillow)a=`<button class="small primary" onclick="act(${jattr([f])},'${s}','${i.fix}','apply')">${esc(FIXES[i.fix])}</button>`;
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
  let h='<h3>Check photos</h3><div class="hint">What each site would reject or change, and what StockUp can fix. Your original photos are never changed; fixes make copies just for that site.</div>';
  let anyFix=false;
  for(const s of my()){
    const st={ok:0,warn:0,fix:0,skip:0},groups={};
    for(const it of items){const x=it.sites[s];if(!x)continue;st[x.status]++;
      if(x.user_skipped)continue;
      for(const i of x.issues){if(i.level=='info')continue;
        const k=(i.applied?'done:':'')+(i.fix||i.level)+'|'+i.text.replace(/[\d.]+|“.*”/g,'#').slice(0,60);
        (groups[k]=groups[k]||{i,files:[]}).files.push(it.file);}}
    const tot=st.ok+st.warn+st.fix+st.skip;
    h+=`<h4><span class="chip ${st.fix?'fix':st.skip?'skip':st.warn?'warn':'ok'}">${esc(SITES[s].short)}</span>${esc(lab(s))} — ${st.ok+st.warn} ready${st.fix?`, ${st.fix} need a fix`:''}${st.skip?`, ${st.skip} not sending`:''}</h4>`;
    const gs=Object.values(groups);
    if(!gs.length){h+='<div class="hint">All good.</div>';continue}
    for(const {i,files} of gs){
      const n=files.length,list=jattr(files);
      const text=n>1?`${n} photos: ${generic(i.text)}`:`${files[0]}: ${i.text}`;
      let a='';
      if(i.fix&&!i.applied&&!i.needs_pillow){anyFix=true;a=`<button class="small primary" onclick="act(${list},'${s}','${i.fix}','apply')">${esc(FIXES[i.fix])}${n>1?' ('+n+')':''}</button>`}
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
  panel(`<h3>Settings</h3>
  <div class="hint">Sites I sell on</div>
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
async function saveSettings(){
  const o={caption_mode:document.querySelector('input[name=cm]:checked').value,adobe_strip_commas:$('o_commas').checked,
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
  panel(`<h3>Files ready</h3><div class="hint">Saved in ${esc(r.folder)}</div>`+r.sites.map(s=>`<h4>${esc(s.label)}</h4>
    <div>${s.included} photos${s.skipped?`, ${s.skipped} not sent`:''}${s.problems?` · <span class="err">${s.problems} note${s.problems>1?'s':''} in checks.txt</span>`:''}</div>
    <div class="hint">${esc(s.files.join(' · '))}</div><div class="hint">Next: ${esc(s.next)}</div>`).join(''));
}
async function testSites(){
  panel('<h3>Checking logins…</h3>');
  try{const r=await api('/api/test',{});
    panel('<h3>Logins</h3>'+r.map(s=>`<div class="site"><b>${esc(s.label)}</b>
      <div class="${s.result=='OK'?'':'err'}">${s.result=='OK'?'Connected':esc(s.result)}</div>${s.listing?`<div class="hint">On the server: ${esc(s.listing.join(', ')||'(empty)')}</div>`:''}</div>`).join(''));}
  catch(e){panel('<h3>Logins</h3><p class="err">'+esc(e.message)+'</p>')}
}
let poll=null;
async function upload(){
  const pick=sites.filter(s=>s.checked).map(s=>s.name);
  if(!pick.length)return panel('<h3>Pick a site</h3><p>Tick at least one site next to “Send to”. A site is greyed out until you add its login under Logins.</p>');
  await save();
  try{const j=await api('/api/upload',{sites:pick});
    if(!j.started)return panel('<h3>Nothing to upload</h3><p>'+esc(j.reason)+'</p>');
    clearInterval(poll);poll=setInterval(status,1000);status();}
  catch(e){note(e.message)}
}
const mb=b=>(b/1048576).toFixed(0)+' MB';
async function status(){
  let s;try{s=await api('/api/status')}catch(e){return}
  panel('<h3>'+(s.running?'Uploading…':'Upload finished')+'</h3>'+Object.entries(s.sites).map(([n,p])=>{
    const pct=p.total_bytes?Math.min(100,100*p.bytes/p.total_bytes):0;
    return `<div class="site"><b>${esc(lab(n))}</b> — ${esc(p.state)} · ${p.done} of ${p.total} photos · ${mb(p.bytes)} of ${mb(p.total_bytes)}
      ${p.skipped?`<span class="hint">(${p.skipped} sent earlier)</span>`:''}${p.left_out?`<span class="hint"> · ${p.left_out} not for this site</span>`:''}
      <div class="prog"><i style="width:${pct}%"></i></div>${p.current?`<div class="hint">${esc(p.current)}</div>`:''}
      ${p.errors.map(e=>`<div class="err">${esc(e)}</div>`).join('')}</div>`}).join('')+
    (s.running?'':'<p class="hint">Next: click “Make site files” for each site’s spreadsheet and what’s left to do on its website. Something failed? Press Upload again — photos already sent are skipped.</p>'));
  if(!s.running)clearInterval(poll);
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
  try{const r=await api('/api/another',{});if(!r.ok)return note(r.message);
    document.body.innerHTML='<div class="empty">Choose your next batch folder in the window that just opened. This page will reload by itself… If nothing happens, open StockUp again.</div>';}
  catch(e){note(e.message)}
}
async function quitApp(force){
  await save();
  try{const r=await api('/api/quit',{force:!!force});
    if(!r.ok){if(confirm(r.message))return quitApp(true);return}
    document.body.innerHTML='<div class="empty">StockUp has quit. You can close this tab.</div>';}
  catch(e){document.body.innerHTML='<div class="empty">StockUp has quit. You can close this tab.</div>'}
}

async function load(){
  const r=await api('/api/items');items=r.items;opts=r.options;VERSION=r.version;FROZEN=r.frozen;PILLOW=r.pillow;
  $('folder').textContent=r.folder.split(/[\\/]/).filter(Boolean).pop()+' · '+items.length+' photos';$('folder').title=r.folder;
  render();
}
window.addEventListener('beforeunload',()=>{if(dirty)save()});
setInterval(()=>fetch('/api/ping').catch(()=>{}),20000);
(async()=>{fill();try{await load();note('Ready · '+counts());await loadSites();checkUpdate(false)}catch(e){note(e.message)}})();
</script></body></html>"""


class App:
    """What the web page talks to."""

    def __init__(self, batch):
        self.batch = batch
        self.uploader = Uploader(batch)
        self.build_state = {"running": False, "done": 0, "total": 0, "current": "",
                            "result": None, "error": None}
        self.last_ping = time.time()

    def start_build(self):
        if self.build_state["running"]:
            return
        self.build_state.update(running=True, done=0, total=0, current="", result=None, error=None)

        def prog(d, t, cur):
            self.build_state.update(done=d, total=t, current=cur)

        def run():
            try:
                self.build_state["result"] = build_outputs(self.batch, prog)
            except Exception as e:
                self.build_state["error"] = str(e)
            self.build_state["running"] = False
        threading.Thread(target=run, daemon=True).start()


def make_handler(app):
    batch = app.batch

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self):
            n = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(n) if n else b""
            return json.loads(raw) if raw.strip() else {}

        def _same_origin(self):
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).netloc != self.headers.get("Host", ""):
                self._send(403, {"error": "forbidden"})
                return False
            return True

        def do_GET(self):
            u = urlparse(self.path)
            p = u.path
            app.last_ping = time.time()
            try:
                if p == "/":
                    page = PAGE.replace("__SS__", json.dumps(SHUTTERSTOCK_CATEGORIES)) \
                        .replace("__AD__", json.dumps({str(k): v for k, v in ADOBE_CATEGORIES.items()})) \
                        .replace("__SITES__", json.dumps({k: {"label": v["label"], "short": v["short"],
                                                              "ftp": bool(v["ftp"])}
                                                          for k, v in SITES.items()})) \
                        .replace("__FIXES__", json.dumps(FIXES))
                    return self._send(200, page, "text/html; charset=utf-8")
                if p == "/api/items":
                    return self._send(200, {"folder": batch.folder, "items": batch.items(),
                                            "options": load_options(), "version": VERSION,
                                            "frozen": FROZEN, "pillow": have_pillow()})
                if p == "/api/sites":
                    return self._send(200, sites_summary())
                if p == "/api/logins":
                    return self._send(200, logins_summary())
                if p == "/api/status":
                    return self._send(200, app.uploader.status())
                if p == "/api/build_status":
                    return self._send(200, app.build_state)
                if p == "/api/ping":
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
                    if f not in batch.meta:
                        return self._send(404, "not found", "text/plain")
                    return self._send(200, batch.thumbnail(f), "image/jpeg")
            except Exception as e:
                return self._send(500, {"error": str(e)})
            self._send(404, "not found", "text/plain")

        def do_POST(self):
            if not self._same_origin():
                return
            p = urlparse(self.path).path
            try:
                body = self._json()
                if p == "/api/save":
                    batch.save(body)
                    return self._send(200, {"ok": True})
                if p == "/api/fix":
                    n = batch.act(body.get("files") or [], body.get("site", "*"),
                                  body.get("code", "*"), body.get("action", "apply"))
                    return self._send(200, {"changed": n})
                if p == "/api/options":
                    before = load_options()
                    opts = save_options(body)
                    rules = ("editorial_keyword", "editorial_label", "flag_formatted")
                    n = batch.reapply_auto() if any(before[k] != opts[k] for k in rules) else 0
                    return self._send(200, {"options": opts, "newly_flagged": n})
                if p == "/api/logins":
                    return self._send(200, save_logins(body))
                if p == "/api/build":
                    app.start_build()
                    return self._send(200, {"started": True})
                if p == "/api/test":
                    return self._send(200, test_sites())
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
                    if app.uploader.running:
                        return self._send(200, {"updated": False,
                                                "message": "Wait for the upload to finish, then update."})
                    r = install_update()
                    self._send(200, r)
                    if r.get("updated"):
                        restart_program(self.server, batch.folder)
                    return
                if p == "/api/another":
                    if app.uploader.running:
                        return self._send(200, {"ok": False,
                                                "message": "Wait for the upload to finish first."})
                    self._send(200, {"ok": True})
                    restart_program(self.server, None)
                    return
                if p == "/api/quit":
                    if app.uploader.running and not body.get("force"):
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
    folder = (args[1] if len(args) > 1 else "") if cmd in ("build", "upload") \
        else (cmd or pick_folder())
    if not folder or not os.path.isdir(folder):
        print(f"Not a folder: {folder or '(none chosen)'}")
        return 1
    load_sites()
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

    app = App(batch)
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
                if time.time() - app.last_ping > 180 and not app.uploader.running \
                        and not app.build_state["running"]:
                    os._exit(0)
        threading.Thread(target=watchdog, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBye.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
