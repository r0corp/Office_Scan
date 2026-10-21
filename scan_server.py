"""
Scan Kantor - halaman scan sederhana untuk Epson L3210 di PC server.

Siapa pun di jaringan kantor membuka http://<server>:8500, menaruh kertas di
kaca scanner, klik Scan (bisa beberapa halaman), lalu Selesai dan unduh
PDF (semua halaman jadi satu), JPG, atau PNG (ZIP kalau lebih dari satu).

Hanya pustaka bawaan Python (tanpa pip). Scan lewat WIA Windows
(scan_page.ps1). Satu scanner = satu antrean: kalau sedang dipakai, yang
lain diberi tahu menunggu. File hasil dihapus otomatis setelah 24 jam.

    python scan_server.py              # port 8500
    set SCAN_FAKE=1 & python scan_server.py   # tanpa scanner (uji di laptop)
"""

import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import zipfile
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("SCAN_DATA") or os.path.join(BASE, "data")
HOST = os.environ.get("SCAN_HOST", "0.0.0.0")
PORT = int(os.environ.get("SCAN_PORT", "8500"))
FAKE = os.environ.get("SCAN_FAKE") == "1"
SCAN_TIMEOUT = 180          # detik per halaman
KEEP_SECONDS = 24 * 3600    # hasil scan disimpan 24 jam
MAX_PAGES = 50
MODES = ("warna", "abu", "hitamputih")
DPIS = (150, 200, 300)

BATCH_RE = re.compile(r"^[a-f0-9]{16,32}$")
PAGE_RE = re.compile(r"^page-(\d{3})$")

scan_lock = threading.Lock()
scan_state = {"since": None}


def log(message):
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}", flush=True)


# ============================================================
# FILE & HALAMAN
# ============================================================

def batch_dir(batch):
    return os.path.join(DATA, batch)


def pages(batch):
    folder = batch_dir(batch)
    if not os.path.isdir(folder):
        return []
    names = sorted(n[:-4] for n in os.listdir(folder) if n.endswith(".jpg") and PAGE_RE.match(n[:-4]))
    # Urutan pilihan pengguna (order.json); halaman baru menyusul di belakang.
    try:
        with open(os.path.join(folder, "order.json"), encoding="utf-8") as f:
            order = [n for n in json.load(f) if n in names]
    except (OSError, ValueError, TypeError):
        order = []
    return order + [n for n in names if n not in order]


def save_order(batch, names):
    with open(os.path.join(batch_dir(batch), "order.json"), "w", encoding="utf-8") as f:
        json.dump(names, f)


def content_disposition(fname):
    # filename= ASCII untuk browser lama, filename*= UTF-8 untuk nama ber-aksen.
    ascii_name = fname.encode("ascii", "replace").decode().replace("?", "_")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(fname)}"


def clean_filename(name):
    """Nama dokumen dari pengguna -> nama file aman (tanpa karakter terlarang Windows)."""
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*]+', " ", name or "")
    return re.sub(r"\s+", " ", name).strip(" .")[:80]


def next_page_name(batch):
    existing = [int(PAGE_RE.match(p).group(1)) for p in pages(batch)]
    return f"page-{(max(existing) if existing else 0) + 1:03d}"


def cleanup():
    """Hapus batch yang lebih tua dari 24 jam."""
    if not os.path.isdir(DATA):
        return
    limit = time.time() - KEEP_SECONDS
    for name in os.listdir(DATA):
        path = os.path.join(DATA, name)
        if BATCH_RE.match(name) and os.path.isdir(path) and os.path.getmtime(path) < limit:
            shutil.rmtree(path, ignore_errors=True)


# ============================================================
# SCAN
# ============================================================

# 1x1 piksel, untuk mode uji tanpa scanner (SCAN_FAKE=1).
FAKE_JPG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707070909080a0c140d0c0b0b0c1912130f141d1a1f1e1d"
    "1a1c1c20242e2720222c231c1c2837292c30313434341f27393d38323c2e333432ffc0000b080001000101011100ffc4001f00000105010101"
    "01010100000000000000000102030405060708090a0bffc400b5100002010303020403050504040000017d01020300041105122131410613"
    "516107227114328191a1082342b1c11552d1f02433627282090a161718191a25262728292a3435363738393a434445464748494a535455"
    "565758595a636465666768696a737475767778797a838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9"
    "bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9faffda0008010100003f00fbd3ffd9")
FAKE_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360f8cf0000030101007a5c1c3f0000000049454e44ae426082")


def scan_page(batch, mode, dpi):
    """Scan satu halaman ke data/<batch>/page-NNN.{jpg,png}. Return dict hasil."""
    os.makedirs(batch_dir(batch), exist_ok=True)
    base = os.path.join(batch_dir(batch), next_page_name(batch))
    if FAKE:
        time.sleep(0.2)
        with open(base + ".jpg", "wb") as f:
            f.write(FAKE_JPG)
        with open(base + ".png", "wb") as f:
            f.write(FAKE_PNG)
        return {"ok": True, "width": 1, "height": 1, "seconds": 0.2}
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
           os.path.join(BASE, "scan_page.ps1"), "-OutBase", base, "-Dpi", str(dpi), "-Mode", mode]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=SCAN_TIMEOUT,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        line = next((l for l in reversed(out.strip().splitlines()) if l.startswith("{")), "")
        result = json.loads(line) if line else {"ok": False, "error": "Scanner tidak memberi jawaban."}
    except subprocess.TimeoutExpired:
        result = {"ok": False, "error": "Scan terlalu lama (lebih dari 3 menit). Cek printer lalu coba lagi."}
    except (OSError, ValueError) as e:
        result = {"ok": False, "error": f"Gagal menjalankan scan: {e}"}
    if not result.get("ok"):
        for ext in (".jpg", ".png"):
            if os.path.exists(base + ext):
                os.remove(base + ext)
    return result


# ============================================================
# PDF (gambar JPG -> satu PDF, tanpa pustaka tambahan)
# ============================================================

def jpeg_size(data):
    """(lebar, tinggi, jumlah warna) dari header JPEG."""
    i = 2
    while i < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        length = struct.unpack(">H", data[i + 2:i + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2):
            height, width = struct.unpack(">HH", data[i + 5:i + 9])
            return width, height, data[i + 9]
        i += 2 + length
    raise ValueError("Bukan file JPEG yang valid.")


def build_pdf(jpegs, dpi):
    """PDF dengan satu halaman per JPEG; ukuran halaman mengikuti DPI scan."""
    objects = []

    def add(body):
        objects.append(body)
        return len(objects)

    catalog = add(None)
    pages_obj = add(None)
    kids = []
    for n, data in enumerate(jpegs, start=1):
        width, height, comps = jpeg_size(data)
        colorspace = "/DeviceGray" if comps == 1 else "/DeviceRGB"
        img = add(b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace %s /BitsPerComponent 8 "
                  b"/Filter /DCTDecode /Length %d >>\nstream\n" % (width, height, colorspace.encode(), len(data))
                  + data + b"\nendstream")
        w_pt, h_pt = width * 72 / dpi, height * 72 / dpi
        content = b"q %.2f 0 0 %.2f 0 0 cm /Im%d Do Q" % (w_pt, h_pt, n)
        stream = add(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
        page = add(b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %.2f %.2f] /Resources << /XObject << /Im%d %d 0 R >> >> "
                   b"/Contents %d 0 R >>" % (pages_obj, w_pt, h_pt, n, img, stream))
        kids.append(page)
    objects[catalog - 1] = b"<< /Type /Catalog /Pages %d 0 R >>" % pages_obj
    objects[pages_obj - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
        b" ".join(b"%d 0 R" % k for k in kids), len(kids))

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for num, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % num + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, catalog, xref))
    return out.getvalue()


# ============================================================
# HTTP
# ============================================================

class Handler(BaseHTTPRequestHandler):
    server_version = "ScanKantor/1.0"

    def log_message(self, fmt, *args):     # log ringkas lewat log()
        pass

    def _send(self, status, body, ctype="application/json", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _batch(self, value):
        return value if value and BATCH_RE.match(value) else None

    def _json_body(self):
        length = min(int(self.headers.get("Content-Length") or 0), 10_000)
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return {}

    # ---------- GET ----------
    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        cleanup()

        if url.path in ("/", "/index.html"):
            with open(os.path.join(BASE, "static", "index.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if url.path.startswith("/static/") and re.fullmatch(r"/static/[\w.-]+", url.path):
            path = os.path.join(BASE, "static", url.path[8:])
            if os.path.isfile(path):
                ctype = {"png": "image/png", "svg": "image/svg+xml", "css": "text/css"}.get(path.rsplit(".", 1)[-1],
                                                                                              "application/octet-stream")
                with open(path, "rb") as f:
                    return self._send(200, f.read(), ctype, {"Cache-Control": "max-age=86400"})
            return self._send(404, {"error": "tidak ditemukan"})
        if url.path == "/api/status":
            batch = self._batch(q.get("batch"))
            busy = scan_lock.locked()
            return self._send(200, {"busy": busy, "since": scan_state["since"], "pages": pages(batch) if batch else []})

        m = re.fullmatch(r"/api/page/([a-f0-9]{16,32})/(page-\d{3})\.jpg", url.path)
        if m:
            path = os.path.join(batch_dir(m.group(1)), m.group(2) + ".jpg")
            if os.path.isfile(path):
                with open(path, "rb") as f:
                    return self._send(200, f.read(), "image/jpeg")
            return self._send(404, {"error": "halaman tidak ditemukan"})

        if url.path == "/api/download":
            batch = self._batch(q.get("batch"))
            fmt = q.get("format", "pdf")
            names = pages(batch) if batch else []
            if not names or fmt not in ("pdf", "jpg", "png"):
                return self._send(400, {"error": "Belum ada halaman yang di-scan."})
            dpi = int(q.get("dpi", "200")) if q.get("dpi", "200").isdigit() else 200
            title = clean_filename(q.get("name")) or "scan-" + datetime.now().strftime("%Y%m%d-%H%M")
            folder = batch_dir(batch)
            if fmt == "pdf":
                jpegs = []
                for n in names:
                    with open(os.path.join(folder, n + ".jpg"), "rb") as f:
                        jpegs.append(f.read())
                data, ctype, fname = build_pdf(jpegs, dpi), "application/pdf", f"{title}.pdf"
            elif len(names) == 1:
                with open(os.path.join(folder, names[0] + "." + fmt), "rb") as f:
                    data = f.read()
                ctype, fname = ("image/jpeg" if fmt == "jpg" else "image/png"), f"{title}.{fmt}"
            else:
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
                    for i, n in enumerate(names, start=1):
                        zf.write(os.path.join(folder, n + "." + fmt), f"{title}-hal{i:02d}.{fmt}")
                data, ctype, fname = buf.getvalue(), "application/zip", f"{title}-{fmt}.zip"
            log(f"unduh {fmt} {len(names)} hal dari {self.client_address[0]}")
            return self._send(200, data, ctype, {"Content-Disposition": content_disposition(fname)})

        return self._send(404, {"error": "tidak ditemukan"})

    # ---------- POST ----------
    def do_POST(self):
        url = urlparse(self.path)
        body = self._json_body()
        batch = self._batch(body.get("batch"))
        if not batch:
            return self._send(400, {"error": "Sesi scan tidak valid. Muat ulang halaman."})

        if url.path == "/api/scan":
            mode = body.get("mode") if body.get("mode") in MODES else "warna"
            dpi = int(body.get("dpi")) if str(body.get("dpi")).isdigit() and int(body.get("dpi")) in DPIS else 200
            if len(pages(batch)) >= MAX_PAGES:
                return self._send(400, {"error": f"Maksimal {MAX_PAGES} halaman per dokumen."})
            if not scan_lock.acquire(blocking=False):
                return self._send(409, {"error": "Scanner sedang dipakai orang lain. Tunggu sebentar lalu coba lagi."})
            try:
                scan_state["since"] = time.time()
                log(f"scan mulai {mode} {dpi}dpi dari {self.client_address[0]}")
                result = scan_page(batch, mode, dpi)
            finally:
                scan_state["since"] = None
                scan_lock.release()
            log(f"scan {'OK' if result.get('ok') else 'GAGAL'} {result.get('seconds', '')}s {result.get('error', '')}")
            if not result.get("ok"):
                return self._send(500, {"error": result.get("error") or "Scan gagal."})
            return self._send(200, {"ok": True, "pages": pages(batch), "seconds": result.get("seconds")})

        if url.path == "/api/delete":
            name = body.get("page")
            if name in pages(batch):
                for ext in (".jpg", ".png"):
                    path = os.path.join(batch_dir(batch), name + ext)
                    if os.path.exists(path):
                        os.remove(path)
            return self._send(200, {"ok": True, "pages": pages(batch)})

        if url.path == "/api/order":
            current, wanted = pages(batch), body.get("pages")
            if not isinstance(wanted, list) or sorted(wanted) != sorted(current):
                return self._send(400, {"error": "Urutan halaman tidak valid. Muat ulang halaman."})
            save_order(batch, wanted)
            return self._send(200, {"ok": True, "pages": pages(batch)})

        if url.path == "/api/reset":
            shutil.rmtree(batch_dir(batch), ignore_errors=True)
            return self._send(200, {"ok": True, "pages": []})

        return self._send(404, {"error": "tidak ditemukan"})


def main():
    sys.stdout.reconfigure(line_buffering=True)
    os.makedirs(DATA, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    log(f"Scan Kantor berjalan di http://{HOST}:{PORT}{' (mode uji tanpa scanner)' if FAKE else ''}")
    server.serve_forever()


if __name__ == "__main__":
    main()
