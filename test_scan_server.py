"""Uji Scan Kantor tanpa scanner (SCAN_FAKE=1):  python -m pytest test_scan_server.py"""

import io
import json
import os
import tempfile
import threading
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer

import pytest

os.environ["SCAN_FAKE"] = "1"
os.environ["SCAN_DATA"] = tempfile.mkdtemp()

import scan_server  # noqa: E402

BATCH = "a1b2c3d4e5f6a7b8c9d0e1f2"


@pytest.fixture(scope="module")
def base():
    server = ThreadingHTTPServer(("127.0.0.1", 0), scan_server.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def call(base, path, data=None):
    req = urllib.request.Request(base + path, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.headers.get("Content-Type"), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type"), e.read()


def test_scan_pages_and_download_pdf_jpg_png(base):
    assert call(base, "/")[0] == 200
    for _ in range(3):
        status, _ct, body = call(base, "/api/scan", {"batch": BATCH, "mode": "abu", "dpi": "300"})
        assert status == 200
    assert json.loads(body)["pages"] == ["page-001", "page-002", "page-003"]

    # Hapus halaman 2 -> tersisa 2 halaman, urutan tetap.
    pages = json.loads(call(base, "/api/delete", {"batch": BATCH, "page": "page-002"})[2])["pages"]
    assert pages == ["page-001", "page-003"]

    status, ctype, pdf = call(base, f"/api/download?batch={BATCH}&format=pdf&dpi=300")
    assert status == 200 and ctype == "application/pdf"
    assert pdf.startswith(b"%PDF-1.4") and pdf.rstrip().endswith(b"%%EOF") and pdf.count(b"/Type /Page ") == 2

    status, ctype, data = call(base, f"/api/download?batch={BATCH}&format=png")
    assert ctype == "application/zip"
    assert sorted(zipfile.ZipFile(io.BytesIO(data)).namelist())[0].endswith("-hal01.png")

    call(base, "/api/delete", {"batch": BATCH, "page": "page-003"})
    status, ctype, data = call(base, f"/api/download?batch={BATCH}&format=jpg")
    assert ctype == "image/jpeg" and data.startswith(b"\xff\xd8")

    assert json.loads(call(base, "/api/reset", {"batch": BATCH})[2])["pages"] == []


def test_invalid_input_and_busy_scanner(base):
    assert call(base, "/api/scan", {"batch": "../etc"})[0] == 400
    assert call(base, "/api/page/a1b2c3d4e5f6a7b8/..%2Fx.jpg")[0] == 404
    assert call(base, f"/api/download?batch={BATCH}&format=exe")[0] == 400
    # Scanner sedang dipakai -> 409, tidak antre diam-diam.
    scan_server.scan_lock.acquire()
    try:
        status, _ct, body = call(base, "/api/scan", {"batch": BATCH})
        assert status == 409 and "sedang dipakai" in json.loads(body)["error"]
        assert json.loads(call(base, f"/api/status?batch={BATCH}")[2])["busy"] is True
    finally:
        scan_server.scan_lock.release()


def test_pdf_page_size_follows_dpi():
    pdf = scan_server.build_pdf([scan_server.FAKE_JPG], 72)
    assert b"/MediaBox [0 0 1.00 1.00]" in pdf
