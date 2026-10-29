#!/usr/bin/env python3
"""
StockUp - send one batch of keyworded photos to several stock agencies.

  python stockup.py "/path/to/batch folder"

Opens a review page in your browser where you can:
  * see every photo with the title and keywords already embedded in it
  * shift-click to select a range and mark it Editorial in one go
  * set Shutterstock / Adobe categories and editorial location/date in bulk
  * build each site's CSV, and upload to every FTP site at once

Only the Python standard library is needed. Pillow (pip install pillow) makes
thumbnails faster; paramiko (pip install paramiko) is only needed if your
Adobe Stock account has SFTP.
"""

import configparser
import csv
import ftplib
import json
import mimetypes
import os
import re
import shutil
import socket
import sys
import threading
import time
import webbrowser
import xml.etree.ElementTree as ET
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from urllib.parse import unquote, urlparse

VERSION = "1.1.0"
REPO = "kefirman9000/stockup"
HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "stockup_sites.ini")
OPTIONS_PATH = os.path.join(HERE, "stockup_options.json")

DEFAULT_OPTIONS = {
    # "auto": keep captions already written as "PLACE - DATE: text", add the
    #         prefix to the rest.  "keep": never change titles or captions.
    "caption_mode": "auto",
    # Pre-check Editorial for photos that carry this keyword (blank = off)
    "editorial_keyword": "editorial",
    # Pre-check Editorial for photos with this Lightroom color label (e.g. Red)
    "editorial_label": "",
    # Pre-check Editorial for photos whose caption is already editorial-formatted
    "flag_formatted": True,
    # Leave the marker keyword out of the keywords sent to the sites
    "drop_marker_keyword": True,
    # Adobe asks for titles without commas
    "adobe_strip_commas": True,
    # Sites ticked for upload last time (empty = the ones enabled in the .ini)
    "upload_sites": [],
}


def load_options():
    opts = dict(DEFAULT_OPTIONS)
    try:
        with open(OPTIONS_PATH, encoding="utf-8") as fh:
            saved = json.load(fh)
        opts.update({k: v for k, v in saved.items() if k in DEFAULT_OPTIONS})
    except (OSError, ValueError):
        pass
    return opts


def save_options(new):
    opts = load_options()
    for k, v in new.items():
        if k in DEFAULT_OPTIONS:
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
    with open(OPTIONS_PATH, "w", encoding="utf-8") as fh:
        json.dump(opts, fh, indent=1)
    return opts
STATE_NAME = "stockup.json"
OUT_NAME = "stockup_output"
UPLOAD_LOG_NAME = ".stockup_uploaded.json"
IMAGE_EXT = (".jpg", ".jpeg")

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

DEFAULT_CONFIG = """\
# StockUp site settings. Keep this file private: it holds your passwords.
# Set enabled = yes for each site you want the Upload button to send to.
# remote_dir is the folder on the agency's server ("/" = the top folder).
# Use the "Test connections" button first to see what folders each server has.

[shutterstock]
enabled = no
protocol = ftps
host = ftps.shutterstock.com
port = 21
username =
password =
remote_dir = /

[dreamstime]
# username is your numeric Dreamstime account ID
enabled = no
protocol = ftp
host = upload.dreamstime.com
port = 21
username =
password =
remote_dir = /

[alamy]
# username is the email you log in to Alamy with
enabled = no
protocol = ftp
host = upload.alamy.com
port = 21
username =
password =
remote_dir = /

[adobe]
# Only if your Adobe Stock Upload page shows SFTP login details.
# Needs: pip install paramiko
enabled = no
protocol = sftp
host = sftp.contributor.adobestock.com
port = 22
username =
password =
remote_dir = /
"""


# --------------------------------------------------------------------------
# Reading embedded metadata (XMP, IPTC, EXIF) from JPEGs - no dependencies
# --------------------------------------------------------------------------

NS = {
    "x": "adobe:ns:meta/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "photoshop": "http://ns.adobe.com/photoshop/1.0/",
    "Iptc4xmpCore": "http://iptc.org/std/Iptc4xmpCore/1.0/xmlns/",
    "xmp": "http://ns.adobe.com/xap/1.0/",
    "exif": "http://ns.adobe.com/exif/1.0/",
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
            if marker in (0xD9, 0xDA):  # end of image / start of scan
                return
            if 0xD0 <= marker <= 0xD7 or marker == 0x01:
                continue
            ln = f.read(2)
            if len(ln) < 2:
                return
            size = int.from_bytes(ln, "big") - 2
            yield marker, f.read(size)


def _xmp_text(root, path):
    el = root.find(path, NS)
    if el is None:
        return ""
    # Alt / Seq / Bag containers
    for li in el.iter("{%s}li" % NS["rdf"]):
        if li.text and li.text.strip():
            return li.text.strip()
    return (el.text or "").strip()


def _xmp_attr_or_text(root, tag):
    """photoshop:City etc. can be an element or an attribute on rdf:Description."""
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
        e2 = data.rfind(b"</rdf:RDF>")
        data = data[start:e2 + len(b"</rdf:RDF>")]
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return {}
    out = {
        "title": _xmp_attr_or_text(root, "dc:title"),
        "description": _xmp_attr_or_text(root, "dc:description"),
        "city": _xmp_attr_or_text(root, "photoshop:City"),
        "state": _xmp_attr_or_text(root, "photoshop:State"),
        "country": _xmp_attr_or_text(root, "photoshop:Country"),
        "date": _xmp_attr_or_text(root, "photoshop:DateCreated"),
        "label": _xmp_attr_or_text(root, "xmp:Label"),
    }
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
    data = payload[14:]
    iim = None
    i = 0
    while i + 12 <= len(data):
        if data[i:i + 4] != b"8BIM":
            break
        rid = int.from_bytes(data[i + 4:i + 6], "big")
        nlen = data[i + 6]
        j = i + 7 + nlen
        if (1 + nlen) % 2:
            j += 1
        size = int.from_bytes(data[j:j + 4], "big")
        block = data[j + 4:j + 4 + size]
        if rid == 0x0404:
            iim = block
        i = j + 4 + size + (size % 2)
    if not iim:
        return {}
    fields = {}
    utf8 = False
    k = 0
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
    return {
        "title": first(5),
        "description": first(120),
        "keywords": [dec(v).strip() for v in fields.get(25, []) if v.strip()],
        "city": first(90),
        "state": first(95),
        "country": first(101),
        "date": d,
    }


def parse_exif(payload):
    """Returns (DateTimeOriginal 'YYYY-MM-DD' or '', embedded thumbnail bytes or None)."""
    if not payload.startswith(b"Exif\x00\x00"):
        return "", None
    t = payload[6:]
    if len(t) < 8:
        return "", None
    end = "<" if t[:2] == b"II" else ">"
    import struct

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
                tag, typ, cnt = u16(p), u16(p + 2), u32(p + 4)
                tags[tag] = (typ, cnt, p + 8)
            nxt = u32(off + 2 + n * 12)
        except Exception:
            return {}, 0
        return tags, nxt

    try:
        ifd0, ifd1_off = ifd(u32(4))
        date = ""
        if 0x8769 in ifd0:
            sub, _ = ifd(u32(ifd0[0x8769][2]))
            if 0x9003 in sub:
                typ, cnt, p = sub[0x9003]
                raw = t[u32(p):u32(p) + cnt].split(b"\x00")[0].decode("ascii", "ignore")
                m = re.match(r"(\d{4}):(\d{2}):(\d{2})", raw)
                if m:
                    date = "-".join(m.groups())
        thumb = None
        if ifd1_off:
            ifd1, _ = ifd(ifd1_off)
            if 0x0201 in ifd1 and 0x0202 in ifd1:
                o = u32(ifd1[0x0201][2])
                ln = u32(ifd1[0x0202][2])
                cand = t[o:o + ln]
                if cand[:2] == b"\xff\xd8":
                    thumb = cand
        return date, thumb
    except Exception:
        return "", None


def read_metadata(path):
    xmp, iptc, exif_date, thumb = {}, {}, "", None
    try:
        for marker, payload in jpeg_segments(path):
            if marker == 0xE1 and payload.startswith(b"http://ns.adobe.com/xap/1.0/"):
                xmp = parse_xmp(payload)
            elif marker == 0xE1 and payload.startswith(b"Exif"):
                exif_date, thumb = parse_exif(payload)
            elif marker == 0xED:
                iptc = parse_iptc(payload)
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
    date = (pick("date") or exif_date)[:10]
    return {
        "title": pick("title"),
        "description": pick("description"),
        "keywords": clean,
        "city": pick("city"),
        "state": pick("state"),
        "country": pick("country"),
        "date": date,
        "label": xmp.get("label", ""),
    }, thumb


# "BOSTON, MASSACHUSETTS - JULY 4, 2026: ..." / "Rome, Italy - 04.09.2026: ..."
_EDITORIAL_RE = re.compile(
    r"^\s*[^:\n]{2,100}?\s[-–—]\s+[^:\n]{0,40}?\b(?:19|20)\d{2}\b[^:\n]{0,12}:\s*\S")


def looks_editorial(text):
    """True if a caption already starts with a location and a dated year."""
    return bool(text and _EDITORIAL_RE.match(text))


# --------------------------------------------------------------------------
# Batch state
# --------------------------------------------------------------------------

class Batch:
    def __init__(self, folder):
        self.folder = os.path.abspath(folder)
        self.state_path = os.path.join(self.folder, STATE_NAME)
        self.thumbs = {}
        self.meta = {}
        self.lock = threading.Lock()
        self.files = sorted(
            (f for f in os.listdir(self.folder)
             if f.lower().endswith(IMAGE_EXT) and not f.startswith(".")),
            key=lambda s: s.lower())
        for f in self.files:
            m, thumb = read_metadata(os.path.join(self.folder, f))
            self.meta[f] = m
            if thumb:
                self.thumbs[f] = thumb
        self.state = self._load_state()

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
            m = self.meta[f]
            s = st.setdefault(f, {})
            if "editorial" not in s:  # first time we see this photo: pre-check it?
                s["editorial"] = bool(self.auto_reason(f, opts))
            s.setdefault("ss_cats", [])
            s.setdefault("adobe_cat", None)
            s.setdefault("ed_city", m["city"])
            s.setdefault("ed_region", m["country"] or m["state"])
            s.setdefault("ed_date", m["date"])
        return {f: st[f] for f in self.files}

    def save(self, incoming=None):
        with self.lock:
            if incoming:
                for f, s in incoming.items():
                    if f in self.state:
                        self.state[f].update({
                            "editorial": bool(s.get("editorial")),
                            "ss_cats": [c for c in s.get("ss_cats", [])
                                        if c in SHUTTERSTOCK_CATEGORIES][:2],
                            "adobe_cat": (int(s["adobe_cat"]) if s.get("adobe_cat")
                                          and int(s["adobe_cat"]) in ADOBE_CATEGORIES else None),
                            "ed_city": str(s.get("ed_city", "")).strip(),
                            "ed_region": str(s.get("ed_region", "")).strip(),
                            "ed_date": str(s.get("ed_date", "")).strip(),
                        })
            tmp = self.state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"version": VERSION, "files": self.state}, fh, indent=1)
            os.replace(tmp, self.state_path)

    def auto_reason(self, f, opts):
        """Why a photo looks editorial before you touch it ('' if it doesn't)."""
        m = self.meta[f]
        kw = opts.get("editorial_keyword", "").strip().lower()
        if kw and kw in (k.lower() for k in m["keywords"]):
            return f'keyword "{kw}"'
        lab = opts.get("editorial_label", "").strip().lower()
        if lab and m.get("label", "").strip().lower() == lab:
            return f"{m['label']} label"
        if opts.get("flag_formatted") and (looks_editorial(m["description"])
                                           or looks_editorial(m["title"])):
            return "caption already formatted"
        return ""

    def reapply_auto(self):
        """Re-run the auto-flag rules on every photo (after changing the options)."""
        opts = load_options()
        n = 0
        with self.lock:
            for f in self.files:
                if self.auto_reason(f, opts) and not self.state[f]["editorial"]:
                    self.state[f]["editorial"] = True
                    n += 1
        self.save()
        return n

    def items(self):
        opts = load_options()
        out = []
        for f in self.files:
            m = self.meta[f]
            out.append({"file": f, "title": m["title"], "description": m["description"],
                        "keywords": m["keywords"], "label": m.get("label", ""),
                        "preformatted": looks_editorial(m["description"]) or looks_editorial(m["title"]),
                        "auto": self.auto_reason(f, opts), **self.state[f]})
        return out

    def thumbnail(self, f):
        if f in self.thumbs and len(self.thumbs[f]) > 2000:
            return self.thumbs[f]
        try:
            from PIL import Image  # optional
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


# --------------------------------------------------------------------------
# Building each site's files
# --------------------------------------------------------------------------

def editorial_caption(text, city, region, date):
    """Shutterstock style: 'CITY, COUNTRY - MONTH D, YYYY: Caption.'"""
    if looks_editorial(text):
        return text  # already in editorial format: keep it exactly
    place = ", ".join(p.upper() for p in (city, region) if p)
    when = ""
    try:
        d = datetime.strptime(date[:10], "%Y-%m-%d")
        when = "%s %d, %d" % (d.strftime("%B").upper(), d.day, d.year)
    except ValueError:
        pass
    prefix = " - ".join(p for p in (place, when) if p)
    return "%s: %s" % (prefix, text) if prefix else text


def write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, quoting=csv.QUOTE_MINIMAL)
        w.writerow(header)
        w.writerows(rows)


def link_or_copy(src, dst):
    if os.path.exists(dst):
        return
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def build_outputs(batch):
    out = os.path.join(batch.folder, OUT_NAME)
    os.makedirs(out, exist_ok=True)
    warnings = []
    ss_rows, adobe_rows, editorial = [], [], []
    opts = load_options()
    keep = opts["caption_mode"] == "keep"
    marker = opts["editorial_keyword"].strip().lower()

    for it in batch.items():
        f = it["file"]
        title = it["title"] or it["description"]
        desc = it["description"] or it["title"]
        kws = it["keywords"]
        if marker and opts["drop_marker_keyword"]:
            kws = [k for k in kws if k.lower() != marker]
        if not title:
            warnings.append(f"{f}: no title or description embedded")
        if len(kws) < 7:
            warnings.append(f"{f}: only {len(kws)} keywords (Shutterstock needs 7+)")
        if len(kws) > 50:
            warnings.append(f"{f}: {len(kws)} keywords, first 50 kept for Shutterstock, 49 for Adobe")

        # Shutterstock: one Description field; editorial captions get the location/date prefix
        ss_desc = desc
        if it["editorial"]:
            editorial.append(f)
            if looks_editorial(desc):
                pass  # already "PLACE - DATE: ..." - used exactly as written
            elif keep:
                warnings.append(f"{f}: editorial, kept as written, but the caption has no "
                                f"'PLACE - DATE:' start (Shutterstock may reject it)")
            else:
                if not (it["ed_city"] or it["ed_region"]) or not it["ed_date"]:
                    warnings.append(f"{f}: editorial but missing location or date for the caption")
                ss_desc = editorial_caption(desc, it["ed_city"], it["ed_region"], it["ed_date"])
        if len(ss_desc) > 200:
            warnings.append(f"{f}: Shutterstock description is {len(ss_desc)} chars (limit 200)")
        if not it["ss_cats"]:
            warnings.append(f"{f}: no Shutterstock category (required)")
        ss_rows.append([f, ss_desc, ",".join(kws[:50]), ",".join(it["ss_cats"]),
                        "Yes" if it["editorial"] else "No"])

        # Adobe Stock
        a_title = title.replace(",", "") if opts["adobe_strip_commas"] and not keep else title
        if len(a_title) > 70:
            warnings.append(f"{f}: Adobe title is {len(a_title)} chars (Adobe recommends 70)")
        if len(f) > 30:
            warnings.append(f"{f}: filename over 30 chars, Adobe's CSV may not match it")
        adobe_rows.append([f, a_title, ",".join(kws[:49]),
                           it["adobe_cat"] or "", ""])

    write_csv(os.path.join(out, "shutterstock.csv"),
              ["Filename", "Description", "Keywords", "Categories", "Editorial"], ss_rows)
    stamp = datetime.now().strftime("%m_%d_%y")
    adobe_name = f"adobe_{stamp}.csv"
    write_csv(os.path.join(out, adobe_name),
              ["Filename", "Title", "Keywords", "Category", "Releases"], adobe_rows)

    with open(os.path.join(out, "editorial_files.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(editorial) + ("\n" if editorial else ""))

    # iStock wants creative and editorial in separate batches
    for sub in ("istock_creative", "istock_editorial"):
        d = os.path.join(out, sub)
        if os.path.isdir(d):
            shutil.rmtree(d)
        os.makedirs(d)
    for f in batch.files:
        sub = "istock_editorial" if f in editorial else "istock_creative"
        link_or_copy(os.path.join(batch.folder, f), os.path.join(out, sub, f))

    with open(os.path.join(out, "warnings.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(warnings) + "\n" if warnings else "No warnings.\n")

    return {
        "folder": out,
        "files": ["shutterstock.csv", adobe_name, "editorial_files.txt",
                  "istock_creative/", "istock_editorial/", "warnings.txt"],
        "count": len(batch.files),
        "editorial": len(editorial),
        "warnings": warnings,
    }


# --------------------------------------------------------------------------
# Uploading
# --------------------------------------------------------------------------

def load_sites():
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            fh.write(DEFAULT_CONFIG)
    cp = configparser.ConfigParser(interpolation=None)
    cp.read(CONFIG_PATH, encoding="utf-8")
    sites = []
    for name in cp.sections():
        s = cp[name]
        sites.append({
            "name": name,
            "enabled": s.getboolean("enabled", fallback=False),
            "protocol": s.get("protocol", "ftp").lower(),
            "host": s.get("host", ""),
            "port": s.getint("port", fallback=22 if s.get("protocol") == "sftp" else 21),
            "username": s.get("username", ""),
            "password": s.get("password", ""),
            "remote_dir": s.get("remote_dir", "/") or "/",
        })
    return sites


class FTPConn:
    def __init__(self, site, timeout=60):
        self.site = site
        if site["protocol"] == "ftps":
            self.ftp = ftplib.FTP_TLS(timeout=timeout)
        else:
            self.ftp = ftplib.FTP(timeout=timeout)
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
            pass  # some servers don't allow SIZE; the transfer itself succeeded

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
        raise RuntimeError("username/password not set in stockup_sites.ini")
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
        if only:  # ticked on the page / --sites on the command line
            sites = [s for s in load_sites() if s["name"] in only]
        else:
            sites = [s for s in load_sites() if s["enabled"]]
        if not sites:
            return False
        done_log = self._load_log()
        total_bytes = sum(os.path.getsize(os.path.join(self.batch.folder, f))
                          for f in self.batch.files)
        with self.lock:
            self.running = True
            self.progress = {}
            for s in sites:
                done = [f for f in self.batch.files if f in done_log.get(s["name"], [])]
                self.progress[s["name"]] = {
                    "state": "waiting", "done": len(done), "total": len(self.batch.files),
                    "bytes": sum(os.path.getsize(os.path.join(self.batch.folder, f)) for f in done),
                    "total_bytes": total_bytes, "current": "", "errors": [], "skipped": len(done),
                }
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

    def _run_site(self, site):
        name = site["name"]
        done_log = set(self._load_log().get(name, []))
        todo = [f for f in self.batch.files if f not in done_log]
        if not todo:
            self._set(name, state="finished")
            return
        conn = None
        failures_in_row = 0
        for f in todo:
            local = os.path.join(self.batch.folder, f)
            ok = False
            for attempt in range(3):
                try:
                    if conn is None:
                        self._set(name, state="connecting")
                        conn = connect(site)
                    self._set(name, state="uploading", current=f)
                    start_bytes = self.progress[name]["bytes"]

                    def prog(n, _name=name):
                        with self.lock:
                            self.progress[_name]["bytes"] += n
                    conn.put(local, f, prog)
                    ok = True
                    break
                except Exception as e:  # network drop, timeout, auth...
                    with self.lock:
                        self.progress[name]["bytes"] = start_bytes if conn else self.progress[name]["bytes"]
                    if conn:
                        conn.close()
                    conn = None
                    msg = str(e) or e.__class__.__name__
                    if "530" in msg or "login" in msg.lower() or "auth" in msg.lower() \
                            or "not set" in msg or "paramiko" in msg:
                        self._set(name, state="error")
                        with self.lock:
                            self.progress[name]["errors"].append(f"Login failed: {msg}")
                        return
                    time.sleep(2 * (attempt + 1))
                    last = msg
            if ok:
                failures_in_row = 0
                self._mark_done(name, f)
                with self.lock:
                    self.progress[name]["done"] += 1
            else:
                failures_in_row += 1
                with self.lock:
                    self.progress[name]["errors"].append(f"{f}: {last}")
                if failures_in_row >= 5:
                    self._set(name, state="error", current="")
                    with self.lock:
                        self.progress[name]["errors"].append("Stopped after 5 failures in a row.")
                    return
        if conn:
            conn.close()
        errs = self.progress[name]["errors"]
        self._set(name, state="finished" if not errs else "finished with errors", current="")


def sites_summary():
    """Sites for the page's checkboxes: ticked = last choice, else enabled in the .ini."""
    chosen = load_options()["upload_sites"]
    out = []
    for s in load_sites():
        ready = bool(s["username"] and s["password"])
        out.append({"name": s["name"], "ready": ready, "protocol": s["protocol"],
                    "checked": ready and ((s["name"] in chosen) if chosen else s["enabled"])})
    return out


def test_sites():
    results = []
    for s in load_sites():
        r = {"name": s["name"], "enabled": s["enabled"], "host": s["host"]}
        if not s["username"]:
            r["result"] = "no username set"
            results.append(r)
            continue
        try:
            c = connect(s)
            listing = c.listdir()
            c.close()
            r["result"] = "OK"
            r["listing"] = listing[:30]
        except Exception as e:
            r["result"] = f"failed: {e}"
        results.append(r)
    return results


# --------------------------------------------------------------------------
# Updating from GitHub releases
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


_update_cache = {"t": 0, "data": None}


def check_update(force=False):
    if not force and _update_cache["data"] and time.time() - _update_cache["t"] < 3600:
        return _update_cache["data"]
    rel = json.loads(_http_get(f"https://api.github.com/repos/{REPO}/releases/latest"))
    latest = rel.get("tag_name", "").lstrip("v")
    asset = next((a["browser_download_url"] for a in rel.get("assets", [])
                  if a.get("name") == "stockup.py"), None)
    data = {"current": VERSION, "latest": latest,
            "available": bool(latest) and _vtuple(latest) > _vtuple(VERSION),
            "notes": (rel.get("body") or "").strip()[:1500],
            "url": rel.get("html_url", ""), "asset": asset,
            "tag": rel.get("tag_name", "")}
    _update_cache.update(t=time.time(), data=data)
    return data


def install_update():
    """Download the latest stockup.py, check it, keep a backup, swap it in."""
    info = check_update(force=True)
    if not info["available"]:
        return {"updated": False, "message": f"You already have the latest version ({VERSION})."}
    url = info["asset"] or f"https://raw.githubusercontent.com/{REPO}/{info['tag']}/stockup.py"
    src = _http_get(url, timeout=60)
    text = src.decode("utf-8")
    try:
        compile(text, "stockup.py", "exec")  # refuse anything that isn't valid Python
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
    """Restart in place on the same port so the open page reconnects by itself."""
    def go():
        time.sleep(0.8)
        server.shutdown()
        server.server_close()
        env = dict(os.environ, STOCKUP_PORT=str(server.server_address[1]))
        args = [sys.executable, os.path.abspath(__file__), folder]
        if os.name == "nt":  # execv on Windows doesn't replace the process cleanly
            import subprocess
            subprocess.Popen(args, env=env)
            os._exit(0)
        os.environ.update(env)
        os.execv(sys.executable, args)
    threading.Thread(target=go, daemon=True).start()


# --------------------------------------------------------------------------
# Review page
# --------------------------------------------------------------------------

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>StockUp</title>
<script>try{var t=localStorage.getItem('stockup-theme');if(t==='light'||t==='dark')document.documentElement.dataset.theme=t}catch(e){}</script>
<style>
:root{--bg:#f4f3ef;--panel:#ffffff;--ink:#1d1d1b;--mute:#6b6a65;--line:#dedcd5;
--accent:#1f5f8b;--on-accent:#ffffff;--ed:#b4441c;--ed-bg:#fbeee8;--ok:#2e7d4f;--thumb:#e6e4dd;--shadow:#0000002a;color-scheme:light}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#121211;--panel:#1d1d1b;--ink:#ecebe6;
--mute:#9a9891;--line:#34332e;--accent:#6aa9d8;--on-accent:#0d1b26;--ed:#f08a5d;--ed-bg:#33211a;--ok:#6cc58f;--thumb:#2a2a27;--shadow:#00000080;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#121211;--panel:#1d1d1b;--ink:#ecebe6;--mute:#9a9891;--line:#34332e;
--accent:#6aa9d8;--on-accent:#0d1b26;--ed:#f08a5d;--ed-bg:#33211a;--ok:#6cc58f;--thumb:#2a2a27;--shadow:#00000080;color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;background:var(--bg);color:var(--ink)}
header{position:sticky;top:0;z-index:5;background:var(--panel);border-bottom:1px solid var(--line);padding:10px 16px}
.top{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
h1{font-size:18px;margin:0}
.sub{color:var(--mute);font-size:13px;flex:1;min-width:160px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:8px}
.group{display:flex;flex-wrap:wrap;gap:6px;align-items:center;padding:4px 8px;border:1px solid var(--line);border-radius:8px}
.group b{font-size:12px;color:var(--mute);font-weight:600}
button,select,input{font:inherit;color:var(--ink);background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:5px 9px}
button{cursor:pointer}button:hover{border-color:var(--accent)}
button.primary{background:var(--accent);color:var(--on-accent);border-color:var(--accent);font-weight:600}
button.edbtn{border-color:var(--ed);color:var(--ed)}
button.link{border:none;background:none;color:var(--accent);padding:2px 4px}
input{width:120px}input[type=date]{width:150px}
input[type=checkbox],input[type=radio]{width:auto;padding:0;margin:0}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:6px;overflow:hidden}
.seg button{border:none;border-radius:0;padding:4px 9px;font-size:12px}
.seg button.on{background:var(--accent);color:var(--on-accent)}
#banner{display:none;margin-top:8px;padding:7px 10px;border-radius:8px;background:var(--ed-bg);font-size:13px}
main{padding:14px 16px 130px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:12px}
.card{background:var(--panel);border:2px solid var(--line);border-radius:10px;overflow:hidden;cursor:pointer;user-select:none;position:relative}
.card.sel{border-color:var(--accent);box-shadow:0 0 0 2px var(--accent)}
.card.ed{background:var(--ed-bg)}
.thumb{aspect-ratio:3/2;background:var(--thumb);display:block;width:100%;object-fit:cover}
.meta{padding:7px 9px}
.fn{font-size:11px;color:var(--mute);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tt{font-weight:600;font-size:13px;margin:2px 0 5px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.tags{display:flex;flex-wrap:wrap;gap:4px;font-size:11px}
.tag{padding:1px 7px;border-radius:9px;border:1px solid var(--line);color:var(--mute)}
.tag.ed{background:var(--ed);border-color:var(--ed);color:var(--panel);font-weight:700}
.tag.ok{border-color:var(--ok);color:var(--ok)}
.tag.warn{border-color:var(--ed);color:var(--ed)}
.check{position:absolute;top:6px;left:6px;width:22px;height:22px;border-radius:50%;background:#ffffffd9;border:2px solid #00000059;display:flex;align-items:center;justify-content:center;font-size:13px;color:#fff}
.card.sel .check{background:var(--accent);border-color:var(--accent);color:var(--on-accent)}
footer{position:fixed;bottom:0;left:0;right:0;background:var(--panel);border-top:1px solid var(--line);padding:9px 16px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
#msg{color:var(--mute);flex:1;min-width:200px;font-size:13px}
.sites{display:flex;gap:10px;flex-wrap:wrap;align-items:center;font-size:13px}
.sites label{display:flex;gap:4px;align-items:center;cursor:pointer}
.sites label.off{color:var(--mute);cursor:default}
#panel{position:fixed;right:16px;bottom:72px;width:min(480px,calc(100% - 32px));max-height:65vh;overflow:auto;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;display:none;box-shadow:0 8px 30px var(--shadow);z-index:9}
#panel h3{margin:0 0 8px;font-size:15px}
#panel label.row{display:flex;gap:8px;align-items:flex-start;margin:8px 0}
#panel label.row input[type=checkbox],#panel label.row input[type=radio]{width:auto;margin-top:3px}
.site{margin:8px 0}.prog{height:8px;background:var(--line);border-radius:4px;overflow:hidden;margin:4px 0}
.prog i{display:block;height:100%;background:var(--accent);width:0}
.err{color:var(--ed);font-size:12px}
pre{white-space:pre-wrap;font-size:12px;margin:4px 0}
.hint{font-size:12px;color:var(--mute)}
.empty{color:var(--mute);padding:40px;text-align:center}
@media (max-width:600px){input:not([type=checkbox]):not([type=radio]){width:100px}.group{width:100%}}
</style></head><body>
<header>
  <div class="top">
    <h1>StockUp</h1><span class="sub" id="folder"></span>
    <div class="seg" id="theme" title="Light or dark look">
      <button data-t="auto">Auto</button><button data-t="light">Light</button><button data-t="dark">Dark</button>
    </div>
    <button onclick="openSettings()">Settings</button>
  </div>
  <div id="banner"></div>
  <div class="bar">
    <div class="group"><b>Pick</b>
      <button onclick="selAll(true)">All</button><button onclick="selAll(false)">None</button>
      <select id="filter" onchange="render()">
        <option value="all">Show every photo</option><option value="ed">Show editorial</option>
        <option value="com">Show creative</option><option value="nocat">Show ones missing a category</option>
      </select>
      <span class="hint" id="selcount">0 picked</span>
    </div>
    <div class="group"><b>Type</b>
      <button class="edbtn" onclick="setEd(true)">Editorial</button>
      <button onclick="setEd(false)">Creative</button>
    </div>
  </div>
  <div class="bar">
    <div class="group"><b>Shutterstock category</b>
      <select id="ss1"></select><select id="ss2"></select>
      <button onclick="applySS()">Set</button>
    </div>
    <div class="group"><b>Adobe category</b><select id="ad"></select><button onclick="applyAd()">Set</button></div>
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
const SS=__SS__, AD=__AD__;
let items=[], sel=new Set(), last=null, dirty=false, saveT=null, opts={}, sites=[];
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'})[c]);
async function api(path,body){
  const r=await fetch(path,body===undefined?{}:{method:'POST',body:JSON.stringify(body)});
  const j=await r.json().catch(()=>({error:'StockUp stopped responding. Is its window still open?'}));
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
function visible(){
  const f=$('filter').value;
  return items.filter(it=>f=='all'||(f=='ed'&&it.editorial)||(f=='com'&&!it.editorial)||(f=='nocat'&&(!it.ss_cats.length||!it.adobe_cat)));
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
    t.push(`<span class="tag ${it.keywords.length<7?'warn':''}">${it.keywords.length} keywords</span>`);
    t.push(it.ss_cats.length?`<span class="tag">SS: ${esc(it.ss_cats.join(' + '))}</span>`:'<span class="tag warn">no Shutterstock category</span>');
    if(it.adobe_cat)t.push(`<span class="tag">Adobe: ${esc(AD[it.adobe_cat])}</span>`);
    return `<div class="card ${sel.has(it.file)?'sel':''} ${it.editorial?'ed':''}" data-f="${esc(it.file)}" title="${esc(it.auto?'Marked editorial automatically: '+it.auto:'')}">
      <div class="check">${sel.has(it.file)?'✓':''}</div>
      <img class="thumb" loading="lazy" src="/thumb/${encodeURIComponent(it.file)}" alt="">
      <div class="meta"><div class="fn">${esc(it.file)}</div><div class="tt">${esc(it.description||it.title||'(no title)')}</div>
      <div class="tags">${t.join('')}</div></div></div>`}).join(''):'<div class="empty">No photos to show here.</div>';
  $('selcount').textContent=sel.size+' picked';
}
$('grid').addEventListener('click',e=>{
  const c=e.target.closest('.card');if(!c)return;
  const f=c.dataset.f,vis=visible().map(i=>i.file);
  if(e.shiftKey&&last!==null){const a=vis.indexOf(last),b=vis.indexOf(f);
    if(a>=0&&b>=0)for(let i=Math.min(a,b);i<=Math.max(a,b);i++)sel.add(vis[i]);}
  else if(e.ctrlKey||e.metaKey){sel.has(f)?sel.delete(f):sel.add(f)}
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
function changed(){dirty=true;render();clearTimeout(saveT);saveT=setTimeout(save,500)}
async function save(){
  const body={};items.forEach(it=>body[it.file]={editorial:it.editorial,ss_cats:it.ss_cats,adobe_cat:it.adobe_cat,ed_city:it.ed_city,ed_region:it.ed_region,ed_date:it.ed_date});
  try{await api('/api/save',body);dirty=false;note('Saved · '+counts())}catch(e){note(e.message)}
}
function panel(html){const p=$('panel');p.innerHTML=html+'<p><button onclick="$(\'panel\').style.display=\'none\'">Close</button></p>';p.style.display='block'}

/* settings */
function openSettings(){
  const o=opts;
  panel(`<h3>Settings</h3>
  <div class="hint">Titles and captions</div>
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
    flag_formatted:$('o_fmt').checked,editorial_keyword:$('o_kw').value,drop_marker_keyword:$('o_drop').checked,editorial_label:$('o_lab').value};
  try{const r=await api('/api/options',o);opts=r.options;await load();
    note(r.newly_flagged?`Settings saved. ${r.newly_flagged} more photos marked editorial.`:'Settings saved.');$('panel').style.display='none';}
  catch(e){note(e.message)}
}

/* sites */
function renderSites(){
  $('sites').innerHTML='<span class="hint">Send to:</span>'+sites.map(s=>`<label class="${s.ready?'':'off'}" title="${s.ready?'':'Add your login in stockup_sites.ini to use this site'}">
    <input type="checkbox" data-s="${esc(s.name)}" ${s.checked?'checked':''} ${s.ready?'':'disabled'}>${esc(nice(s.name))}</label>`).join('');
  $('sites').querySelectorAll('input').forEach(i=>i.onchange=()=>{const s=sites.find(x=>x.name==i.dataset.s);s.checked=i.checked;
    api('/api/options',{upload_sites:sites.filter(x=>x.checked).map(x=>x.name)}).catch(()=>{})});
}
const nice=n=>({shutterstock:'Shutterstock',dreamstime:'Dreamstime',alamy:'Alamy',adobe:'Adobe'})[n]||n;

async function build(){
  await save();note('Making files…');
  try{const r=await api('/api/build',{});
    note(`Files ready for ${r.count} photos · ${counts()}`);
    panel(`<h3>Files ready</h3><div class="hint">Saved in ${esc(r.folder)}</div><pre>${r.files.map(esc).join('\n')}</pre>`+
      (r.warnings.length?`<h3>Check these (${r.warnings.length})</h3><pre class="err">${r.warnings.map(esc).join('\n')}</pre>`:'<p>Everything looks good.</p>'));}
  catch(e){note(e.message)}
}
async function testSites(){
  panel('<h3>Checking logins…</h3>');
  try{const r=await api('/api/test',{});
    panel('<h3>Logins</h3>'+r.map(s=>`<div class="site"><b>${esc(nice(s.name))}</b>
      <div class="${s.result=='OK'?'':'err'}">${s.result=='OK'?'Connected':esc(s.result)}</div>${s.listing?`<div class="hint">On the server: ${esc(s.listing.join(', ')||'(empty)')}</div>`:''}</div>`).join('')+
      '<p class="hint">Logins live in stockup_sites.ini, next to stockup.py.</p>');}
  catch(e){panel('<h3>Logins</h3><p class="err">'+esc(e.message)+'</p>')}
}
let poll=null;
async function upload(){
  const pick=sites.filter(s=>s.checked).map(s=>s.name);
  if(!pick.length)return panel('<h3>Pick a site</h3><p>Tick at least one site next to “Send to”. Sites are greyed out until their login is added to stockup_sites.ini.</p>');
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
    return `<div class="site"><b>${esc(nice(n))}</b> — ${esc(p.state)} · ${p.done} of ${p.total} photos · ${mb(p.bytes)} of ${mb(p.total_bytes)}
      ${p.skipped?`<span class="hint">(${p.skipped} sent earlier)</span>`:''}
      <div class="prog"><i style="width:${pct}%"></i></div>${p.current?`<div class="hint">${esc(p.current)}</div>`:''}
      ${p.errors.map(e=>`<div class="err">${esc(e)}</div>`).join('')}</div>`}).join('')+
    (s.running?'':'<p class="hint">Next: add shutterstock.csv on Shutterstock’s Submit page, and the adobe file under Uploaded Files on Adobe. Something failed? Press Upload again — photos already sent are skipped.</p>'));
  if(!s.running)clearInterval(poll);
}

/* updates */
let VERSION='';
async function checkUpdate(manual){
  try{const u=await api('/api/version'+(manual?'?force=1':''));VERSION=u.current;
    if(u.available){$('banner').style.display='block';
      $('banner').innerHTML=`StockUp ${esc(u.latest)} is available (you have ${esc(u.current)}). <button class="primary" onclick="doUpdate()">Update now</button> <a href="${esc(u.url)}" target="_blank" rel="noopener" class="hint">What’s new</a>`;
      if(manual)$('panel').style.display='none';}
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

async function load(){
  const r=await api('/api/items');items=r.items;opts=r.options;VERSION=r.version;
  $('folder').textContent=r.folder.split(/[\\/]/).filter(Boolean).pop()+' · '+items.length+' photos';$('folder').title=r.folder;render();note('Ready · '+counts());
}
window.addEventListener('beforeunload',()=>{if(dirty)save()});
(async()=>{fill();try{await load();sites=await api('/api/sites');renderSites();checkUpdate(false)}catch(e){note(e.message)}})();
</script></body></html>"""


def make_handler(batch, uploader):
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

        def _local_only(self):
            # refuse requests from other websites poking this local server
            origin = self.headers.get("Origin")
            host = self.headers.get("Host", "")
            if origin and urlparse(origin).netloc != host:
                self._send(403, {"error": "forbidden"})
                return False
            return True

        def do_GET(self):
            u = urlparse(self.path)
            p = u.path
            try:
                if p == "/":
                    page = PAGE.replace("__SS__", json.dumps(SHUTTERSTOCK_CATEGORIES)) \
                               .replace("__AD__", json.dumps({str(k): v for k, v in ADOBE_CATEGORIES.items()}))
                    return self._send(200, page, "text/html; charset=utf-8")
                if p == "/api/items":
                    return self._send(200, {"folder": batch.folder, "items": batch.items(),
                                            "options": load_options(), "version": VERSION})
                if p == "/api/sites":
                    return self._send(200, sites_summary())
                if p == "/api/status":
                    return self._send(200, uploader.status())
                if p == "/api/version":
                    try:
                        return self._send(200, check_update(force="force=1" in u.query))
                    except Exception as e:
                        return self._send(200, {"current": VERSION, "available": False, "error": str(e)}
                                          if "force=1" not in u.query else {"error": str(e)})
                if p.startswith("/thumb/"):
                    f = unquote(p[len("/thumb/"):])
                    if f not in batch.meta:
                        return self._send(404, "not found", "text/plain")
                    return self._send(200, batch.thumbnail(f), "image/jpeg")
            except Exception as e:
                return self._send(500, {"error": str(e)})
            self._send(404, "not found", "text/plain")

        def do_POST(self):
            if not self._local_only():
                return
            p = urlparse(self.path).path
            try:
                body = self._json()
                if p == "/api/save":
                    batch.save(body)
                    return self._send(200, {"ok": True})
                if p == "/api/options":
                    before = load_options()
                    opts = save_options(body)
                    rules = ("editorial_keyword", "editorial_label", "flag_formatted")
                    n = batch.reapply_auto() if any(before[k] != opts[k] for k in rules) else 0
                    return self._send(200, {"options": opts, "newly_flagged": n})
                if p == "/api/build":
                    batch.save()
                    return self._send(200, build_outputs(batch))
                if p == "/api/test":
                    return self._send(200, test_sites())
                if p == "/api/upload":
                    if uploader.running:
                        return self._send(200, {"started": True})
                    names = [s["name"] for s in sites_summary() if s["ready"]]
                    pick = [n for n in body.get("sites", []) if n in names]
                    if not pick:
                        return self._send(200, {"started": False, "reason":
                            "None of the ticked sites has a login yet. Add it to stockup_sites.ini "
                            "(next to stockup.py), then reopen StockUp."})
                    save_options({"upload_sites": pick})
                    return self._send(200, {"started": uploader.start(only=pick)})
                if p == "/api/update":
                    if uploader.running:
                        return self._send(200, {"updated": False,
                                                "message": "Wait for the upload to finish, then update."})
                    r = install_update()
                    self._send(200, r)
                    if r.get("updated"):
                        restart_program(self.server, batch.folder)
                    return
            except Exception as e:
                return self._send(500, {"error": str(e)})
            self._send(404, {"error": "not found"})
    return H


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------

def pick_folder():
    """No folder given (e.g. double-clicked): ask with a folder window."""
    try:
        import tkinter
        from tkinter import filedialog
        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        folder = filedialog.askdirectory(title="StockUp: choose the folder with this batch of photos")
        root.destroy()
        return folder
    except Exception:
        return input("Drag the batch folder here and press Enter: ").strip().strip('"').strip("'")


def main(argv):
    if len(argv) > 1 and argv[1] in ("-h", "--help"):
        print(__doc__)
        print("Other commands:\n"
              "  python stockup.py build  FOLDER                 make the site files only\n"
              "  python stockup.py upload FOLDER [--sites a,b]   upload without the page\n"
              "  python stockup.py test                          check the logins\n"
              "  python stockup.py update                        install the latest version")
        return 0
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "test":
        load_sites()
        for r in test_sites():
            print(f"{r['name']:12} {r['result']}")
            if r.get("listing"):
                print("             on server:", ", ".join(r["listing"]))
        return 0
    if cmd == "update":
        try:
            print(install_update()["message"])
        except Exception as e:
            print("Update failed:", e)
            return 1
        return 0
    if cmd in ("build", "upload"):
        folder = argv[2] if len(argv) > 2 else ""
    else:
        folder = cmd or pick_folder()
    if not folder or not os.path.isdir(folder):
        print(f"Not a folder: {folder or '(none chosen)'}")
        return 1
    load_sites()  # creates stockup_sites.ini on first run
    print("Reading your photos…", end=" ", flush=True)
    batch = Batch(folder)
    print(f"{len(batch.files)} JPEGs found.")
    if not batch.files:
        return 1
    if cmd == "build":
        r = build_outputs(batch)
        print(f"Wrote {', '.join(r['files'])} to {r['folder']}")
        for w in r["warnings"]:
            print("  !", w)
        return 0
    if cmd == "upload":
        only = None
        if "--sites" in argv:
            i = argv.index("--sites")
            only = [s.strip() for s in (argv[i + 1] if i + 1 < len(argv) else "").split(",") if s.strip()]
        up = Uploader(batch)
        if not up.start(only=only):
            print("No sites to upload to. Check stockup_sites.ini:", CONFIG_PATH)
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

    uploader = Uploader(batch)
    restarting = os.environ.pop("STOCKUP_PORT", "")
    port = int(restarting) if restarting.isdigit() else 0
    server = None
    for _ in range(20):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(batch, uploader))
            break
        except OSError:
            time.sleep(0.25)  # old copy still letting go of the port after an update
    if server is None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(batch, uploader))
        restarting = ""
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"StockUp {VERSION} is open in your browser: {url}\n"
          "Keep this window open while you work. Close it (or press Ctrl+C) to quit.")
    if not restarting:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBye.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
