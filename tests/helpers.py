"""Test helpers: make tagged JPEGs, and a small FTP/FTPS server that runs inside the tests.

None of this is part of StockUp itself.
"""
import os
import random
import shutil
import socket
import socketserver
import ssl
import struct
import subprocess
import tempfile
import threading
import time

from PIL import Image, ImageCms

SRGB_ICC = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
ADOBE_ICC = SRGB_ICC.replace("sRGB built-in".encode("utf-16-be"), "AdobeRGB 1998".encode("utf-16-be"))
GPS_EXIF = {1: "N", 2: (40.0, 44.0, 54.0), 3: "W", 4: (73.0, 59.0, 10.0)}

KEYWORDS = ["harbor", "boat", "summer", "water", "travel", "sea", "coast", "sunset", "calm", "evening"]


def xmp_segment(title, desc, keywords, city="", country="", date="2026-07-04T10:00:00", label=""):
    li = "".join(f"<rdf:li>{k}</rdf:li>" for k in keywords)
    lab = f' xmp:Label="{label}"' if label else ""
    xml = (
        '<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?><x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/" '
        f'xmlns:xmp="http://ns.adobe.com/xap/1.0/" photoshop:City="{city}" photoshop:Country="{country}" '
        f'photoshop:DateCreated="{date}"{lab}>'
        f'<dc:title><rdf:Alt><rdf:li xml:lang="x-default">{title}</rdf:li></rdf:Alt></dc:title>'
        f'<dc:description><rdf:Alt><rdf:li xml:lang="x-default">{desc}</rdf:li></rdf:Alt></dc:description>'
        f"<dc:subject><rdf:Bag>{li}</rdf:Bag></dc:subject></rdf:Description></rdf:RDF></x:xmpmeta>"
        '<?xpacket end="w"?>').encode()
    return b"http://ns.adobe.com/xap/1.0/\x00" + xml


def iptc_segment(title, caption, keywords, date="20260512"):
    def ds(n, v):
        v = v.encode()
        return bytes([0x1C, 2, n]) + struct.pack(">H", len(v)) + v
    iim = bytes([0x1C, 1, 90, 0, 3]) + b"\x1b%G" + ds(5, title) + ds(120, caption) \
        + b"".join(ds(25, k) for k in keywords) + ds(55, date)
    if len(iim) % 2:
        iim += b"\x00"
    irb = b"8BIM" + struct.pack(">H", 0x0404) + b"\x00\x00" + struct.pack(">I", len(iim)) + iim
    return b"Photoshop 3.0\x00" + irb


def inject(path, payload, marker):
    data = open(path, "rb").read()
    seg = bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload
    with open(path, "wb") as fh:
        fh.write(data[:2] + seg + data[2:])


def picture(seed, w=2400, h=1600, noise=False):
    """A simple scene; different seeds look different. noise=True makes big, hard-to-compress files."""
    if noise:
        return Image.frombytes("RGB", (w, h), os.urandom(w * h * 3))
    im = Image.new("RGB", (w, h), ((seed * 70) % 256, (seed * 130 + 60) % 256, (seed * 30 + 120) % 256))
    for i in range(8):
        band = Image.new("RGB", (w, h // 8), ((seed * 50 + i * 30) % 256, (i * 40) % 256, (seed * 90) % 256))
        im.paste(band, (0, i * h // 8))
    return im


def make_photo(folder, name, title="Sailboats on a calm bay", desc="Sailboats anchored on a calm bay at sunset",
               keywords=KEYWORDS, seed=0, size=(3000, 2000), icc=SRGB_ICC, gps=False, progressive=False,
               quality=92, noise=False, iptc=False, city="", country="", label=""):
    path = os.path.join(folder, name)
    kw = {"quality": quality, "progressive": progressive}
    if icc:
        kw["icc_profile"] = icc
    if gps:
        ex = Image.Exif()
        ex[0x8825] = GPS_EXIF
        kw["exif"] = ex.tobytes()
    picture(seed, *size, noise=noise).save(path, "JPEG", **kw)
    if iptc:
        inject(path, iptc_segment(title, desc, keywords), 0xED)
    else:
        inject(path, xmp_segment(title, desc, keywords, city, country, label=label), 0xE1)
    return path


# --------------------------------------------------------------------------
# A small FTP server (plain or with TLS) for upload tests
# --------------------------------------------------------------------------

def make_cert(folder):
    """Self-signed certificate for the test server, or None if openssl isn't installed."""
    if not shutil.which("openssl"):
        return None
    key, cert = os.path.join(folder, "key.pem"), os.path.join(folder, "cert.pem")
    r = subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key,
                        "-out", cert, "-days", "2", "-subj", "/CN=localhost"],
                       capture_output=True)
    return (cert, key) if r.returncode == 0 else None


class FTPTestServer:
    """Accepts user 'u' with password 'pw'. Options: tls, slow (seconds per 64 KB received),
    drop_once (file names whose first upload is cut off part-way)."""

    def __init__(self, root, cert=None, slow=0.0, drop_once=()):
        self.root, self.cert, self.slow = root, cert, slow
        self.drop_once = set(drop_once)
        self.stors = []  # names of every STOR received, in order
        ctx = None
        if cert:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(*cert)
        server = self

        class Handler(socketserver.StreamRequestHandler):
            def send(self, line):
                self.wfile.write((line + "\r\n").encode())
                self.wfile.flush()

            def handle(self):
                prot, pasv = False, None
                self.send("220 test server")
                while True:
                    try:
                        line = self.rfile.readline()
                    except (OSError, ssl.SSLError):
                        return
                    if not line:
                        return
                    cmd, _, arg = line.decode().strip().partition(" ")
                    cmd = cmd.upper()
                    if cmd == "AUTH":
                        if not ctx:
                            self.send("502 no TLS here")
                            continue
                        self.send("234 go ahead")
                        s = ctx.wrap_socket(self.request, server_side=True)
                        self.request, self.rfile, self.wfile = s, s.makefile("rb"), s.makefile("wb")
                    elif cmd == "USER":
                        self.send("331 password please")
                    elif cmd == "PASS":
                        self.send("230 welcome" if arg == "pw" else "530 Login incorrect")
                    elif cmd in ("PBSZ", "TYPE", "CWD"):
                        self.send("200 ok")
                    elif cmd == "PROT":
                        prot = arg == "P"
                        self.send("200 ok")
                    elif cmd == "PASV":
                        pasv = socket.socket()
                        # a small receive window, so uploads go at the server's pace like a real
                        # internet connection, instead of vanishing into a big local buffer
                        pasv.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 32768)
                        pasv.bind(("127.0.0.1", 0))
                        pasv.listen(1)
                        p = pasv.getsockname()[1]
                        self.send("227 Entering Passive Mode (127,0,0,1,%d,%d)" % (p // 256, p % 256))
                    elif cmd in ("NLST", "STOR"):
                        c, _ = pasv.accept()
                        pasv.close()
                        self.send("150 opening data connection")
                        if prot:
                            c = ctx.wrap_socket(c, server_side=True)
                        try:
                            if cmd == "NLST":
                                c.sendall("".join(n + "\r\n" for n in sorted(os.listdir(server.root))).encode())
                            else:
                                server.stors.append(arg)
                                got = 0
                                with open(os.path.join(server.root, arg), "wb") as fh:
                                    while True:
                                        d = c.recv(65536)
                                        if not d:
                                            break
                                        fh.write(d)
                                        got += len(d)
                                        if server.slow:
                                            time.sleep(server.slow)
                                        if arg in server.drop_once and got > 200_000:
                                            server.drop_once.discard(arg)
                                            c.close()
                                            self.request.close()
                                            return
                            if prot:
                                try:
                                    c.unwrap()
                                except (OSError, ssl.SSLError):
                                    pass
                        except (OSError, ssl.SSLError):
                            c.close()
                            continue
                        c.close()
                        self.send("226 transfer complete")
                    elif cmd == "SIZE":
                        p = os.path.join(server.root, arg)
                        self.send("213 %d" % os.path.getsize(p) if os.path.exists(p) else "550 no such file")
                    elif cmd == "DELE":
                        p = os.path.join(server.root, arg)
                        if os.path.exists(p):
                            os.remove(p)
                            self.send("250 deleted")
                        else:
                            self.send("550 no such file")
                    elif cmd == "QUIT":
                        self.send("221 bye")
                        return
                    else:
                        self.send("502 not implemented")

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self.server = Server(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def site(self, name, protocol="ftp", password="pw"):
        return {"name": name, "enabled": True, "protocol": protocol, "host": "127.0.0.1",
                "port": self.port, "username": "u", "password": password, "remote_dir": "/"}

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def tempdir():
    return tempfile.mkdtemp(prefix="stockup-test-")
