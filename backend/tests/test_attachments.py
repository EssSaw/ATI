"""Tests for ATI backend attachments preparation and validation."""

import asyncio
import base64
import io
import zipfile
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pypdf

from backend.app.attachments import (
    MAX_DOCX_ENTRIES,
    MAX_DOCX_EXPANDED_BYTES,
    MAX_IMAGE_SIZE_BYTES,
    MAX_TEXT_CHARACTERS,
    PreparedAttachment,
    reset_semaphore,
    router as attachments_router,
    sanitize_filename,
    validate_attachment,
)

# Minimal 1x1 valid image fixtures
PNG_1X1_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)
JPEG_1X1_BYTES = bytes.fromhex(
    "ffd8ffe000104a46494600010101004800480000ffdb004300080606070605080707070909080a0c140d0c0b0b0c"
    "1912130f141d1a1f1e1d1a1c1c20242e2720222c231c1c2837292c30313434341f27393d38323c2e333432ffc000"
    "0b080001000101011100ffc4001f0000010501010101010100000000000000000102030405060708090a0bffda00"
    "08010100003f007f00ffd9"
)
WEBP_1X1_BYTES = bytes.fromhex("524946461a000000574542505650384c0d0000002f00000010071011118888fe0700")


def make_docx(text: str = "Hello DOCX world", extra_files: dict = None) -> bytes:
    """Generate a valid DOCX byte stream using stdlib zipfile."""
    bio = io.BytesIO()
    doc_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>{text}</w:t></w:r></w:p>
  </w:body>
</w:document>"""
    with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", doc_xml)
        if extra_files:
            for k, v in extra_files.items():
                zf.writestr(k, v)
    return bio.getvalue()


def make_pdf(pages: int = 1, encrypted: bool = False) -> bytes:
    """Generate a minimal valid PDF using pypdf.PdfWriter."""
    writer = pypdf.PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=100, height=100)
    if encrypted:
        writer.encrypt("secret-key")
    bio = io.BytesIO()
    writer.write(bio)
    return bio.getvalue()


def make_pdf_with_text(text: str = "Hello PDF") -> bytes:
    """Generate a minimal valid PDF with an extractable text stream."""
    return f"""%PDF-1.4
1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj
2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj
3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj
4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj
5 0 obj << /Length {len(text) + 33} >> stream
BT
/F1 12 Tf
72 712 Td
({text}) Tj
ET
endstream
endobj
xref
0 6
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000224 00000 n 
0000000293 00000 n 
trailer << /Size 6 /Root 1 0 R >>
startxref
388
%%EOF""".encode("latin-1")


# Standalone app instance matching main.py guards for isolated worker testing
local_app = FastAPI()


@local_app.middleware("http")
async def local_request_guard(request: Request, call_next):
    allowed_hosts = {"localhost", "127.0.0.1", "::1", "testserver"}
    if request.url.hostname not in allowed_hosts:
        return JSONResponse(status_code=403, content={"detail": "Local application access only."})
    origin = request.headers.get("origin")
    if origin:
        try:
            parsed = urlsplit(origin)
            valid_origin = (
                parsed.scheme in {"http", "https"}
                and parsed.hostname in allowed_hosts - {"testserver"}
                and not parsed.username
                and not parsed.password
            )
        except ValueError:
            valid_origin = False
        if not valid_origin:
            return JSONResponse(status_code=403, content={"detail": "Untrusted browser origin."})
    if request.headers.get("sec-fetch-site") == "cross-site":
        return JSONResponse(status_code=403, content={"detail": "Cross-site requests are not allowed."})
    if (
        request.method in {"POST", "PUT", "PATCH", "DELETE"}
        and request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json"
    ):
        return JSONResponse(status_code=415, content={"detail": "Application/json is required."})
    return await call_next(request)


@local_app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    msg = "; ".join([f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}" for err in errors])
    return JSONResponse(status_code=422, content={"detail": f"Validation error: {msg}"})


local_app.include_router(attachments_router)


@pytest.fixture
def client():
    """TestClient that uses main app if coordinator already wired the router, else local_app."""
    try:
        from backend.app.main import app as main_app

        has_route = any(
            getattr(route, "path", None) in ("/agent/attachments/prepare", "/prepare")
            for route in getattr(main_app, "routes", [])
        )
        if has_route:
            return TestClient(main_app)
    except Exception:
        pass
    return TestClient(local_app)


@pytest.fixture(autouse=True)
def reset_concurrency():
    reset_semaphore()
    yield
    reset_semaphore()


# -----------------------------------------------------------------------------
# 1. UTF-8 Text and Code Files
# -----------------------------------------------------------------------------


def test_prepare_plain_text(client):
    content = "Hello, this is a plain text note.\nSecond line."
    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    res = client.post(
        "/agent/attachments/prepare",
        json={"name": "notes.txt", "mime_type": "text/plain", "data_base64": b64},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["name"] == "notes.txt"
    assert data["kind"] == "text"
    assert data["mime_type"] == "text/plain"
    assert data["text"] == content
    assert data.get("data_url") is None
    assert data["size"] == len(content.encode("utf-8"))
    assert data["id"].startswith("att_")


def test_prepare_markdown(client):
    content = "# Project Title\n\nSome **markdown** explanation."
    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    res = client.post(
        "/agent/attachments/prepare",
        json={"name": "README.md", "mime_type": "text/markdown", "data_base64": b64},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["kind"] == "text"
    assert data["mime_type"] == "text/markdown"
    assert data["text"] == content


def test_prepare_json(client):
    content = '{"key": "value", "numbers": [1, 2, 3]}'
    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    res = client.post(
        "/agent/attachments/prepare",
        json={"name": "package.json", "mime_type": "application/json", "data_base64": b64},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["kind"] == "text"
    assert data["mime_type"] == "application/json"
    assert "package.json" in data["name"]
    assert data["text"] == content


def test_prepare_csv(client):
    content = "id,name,score\n1,Alice,95\n2,Bob,88"
    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    res = client.post(
        "/agent/attachments/prepare",
        json={"name": "students.csv", "mime_type": "text/csv", "data_base64": b64},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["kind"] == "text"
    assert data["mime_type"] == "text/csv"
    assert data["text"] == content


def test_prepare_code_files(client):
    python_code = "def add(a: int, b: int) -> int:\n    return a + b\n"
    b64 = base64.b64encode(python_code.encode("utf-8")).decode("ascii")
    res = client.post(
        "/agent/attachments/prepare",
        json={"name": "math_utils.py", "mime_type": "text/x-python", "data_base64": b64},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["kind"] == "text"
    assert data["mime_type"] == "text/x-python"
    assert data["text"] == python_code

    ts_code = "export const greet = (name: string): string => `Hello, ${name}!`;"
    b64_ts = base64.b64encode(ts_code.encode("utf-8")).decode("ascii")
    res_ts = client.post(
        "/agent/attachments/prepare",
        json={"name": "greet.tsx", "mime_type": "application/typescript", "data_base64": b64_ts},
    )
    assert res_ts.status_code == 200
    assert res_ts.json()["kind"] == "text"
    assert res_ts.json()["text"] == ts_code


def test_reject_non_utf8_text(client):
    raw_bad_bytes = b"\xff\xfe\x00\x00Invalid UTF-8"
    b64 = base64.b64encode(raw_bad_bytes).decode("ascii")
    res = client.post(
        "/agent/attachments/prepare",
        json={"name": "corrupt.txt", "mime_type": "text/plain", "data_base64": b64},
    )
    assert res.status_code == 422
    assert "not valid UTF-8" in res.json()["detail"]


def test_reject_null_bytes_in_text(client):
    content_with_null = b"Hello\x00World"
    b64 = base64.b64encode(content_with_null).decode("ascii")
    res = client.post(
        "/agent/attachments/prepare",
        json={"name": "bad.txt", "mime_type": "text/plain", "data_base64": b64},
    )
    assert res.status_code == 422
    assert "null bytes or binary" in res.json()["detail"]


def test_reject_empty_text(client):
    b64 = base64.b64encode(b"   \n\t  ").decode("ascii")
    res = client.post(
        "/agent/attachments/prepare",
        json={"name": "empty.txt", "mime_type": "text/plain", "data_base64": b64},
    )
    assert res.status_code == 422
    assert "empty" in res.json()["detail"].lower()


def test_reject_oversized_text(client):
    huge_text = "a" * (MAX_TEXT_CHARACTERS + 10)
    b64 = base64.b64encode(huge_text.encode("utf-8")).decode("ascii")
    res = client.post(
        "/agent/attachments/prepare",
        json={"name": "big.txt", "mime_type": "text/plain", "data_base64": b64},
    )
    assert res.status_code == 422
    assert "exceeds maximum limit of 100,000 characters" in res.json()["detail"]


# -----------------------------------------------------------------------------
# 2. Image Attachments (PNG, JPEG, WebP)
# -----------------------------------------------------------------------------


def test_prepare_valid_png(client):
    b64 = base64.b64encode(PNG_1X1_BYTES).decode("ascii")
    res = client.post(
        "/agent/attachments/prepare",
        json={"name": "pixel.png", "mime_type": "image/png", "data_base64": b64},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["kind"] == "image"
    assert data["mime_type"] == "image/png"
    assert data.get("text") is None
    assert data["data_url"].startswith("data:image/png;base64,")
    assert data["size"] == len(PNG_1X1_BYTES)


def test_prepare_valid_jpeg(client):
    b64 = base64.b64encode(JPEG_1X1_BYTES).decode("ascii")
    res = client.post(
        "/agent/attachments/prepare",
        json={"name": "photo.jpg", "mime_type": "image/jpeg", "data_base64": b64},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["kind"] == "image"
    assert data["mime_type"] == "image/jpeg"
    assert data["data_url"].startswith("data:image/jpeg;base64,")


def test_prepare_valid_webp(client):
    b64 = base64.b64encode(WEBP_1X1_BYTES).decode("ascii")
    res = client.post(
        "/agent/attachments/prepare",
        json={"name": "graphic.webp", "mime_type": "image/webp", "data_base64": b64},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["kind"] == "image"
    assert data["mime_type"] == "image/webp"
    assert data["data_url"].startswith("data:image/webp;base64,")


def test_reject_svg(client):
    svg_code = '<svg xmlns="http://www.w3.org/2000/svg"><circle r="10"/></svg>'
    b64 = base64.b64encode(svg_code.encode("utf-8")).decode("ascii")
    res = client.post(
        "/agent/attachments/prepare",
        json={"name": "icon.svg", "mime_type": "image/svg+xml", "data_base64": b64},
    )
    assert res.status_code == 422
    assert "SVG" in res.json()["detail"]


def test_reject_gif(client):
    gif_bytes = b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
    b64 = base64.b64encode(gif_bytes).decode("ascii")
    res = client.post(
        "/agent/attachments/prepare",
        json={"name": "anim.gif", "mime_type": "image/gif", "data_base64": b64},
    )
    assert res.status_code == 422
    assert "GIF" in res.json()["detail"]


def test_reject_fake_image(client):
    fake_png = b"This is just plain text masquerading as PNG."
    b64 = base64.b64encode(fake_png).decode("ascii")
    res = client.post(
        "/agent/attachments/prepare",
        json={"name": "fake.png", "mime_type": "image/png", "data_base64": b64},
    )
    assert res.status_code == 422
    assert "Only valid PNG, JPEG, and WebP" in res.json()["detail"]


def test_reject_truncated_image(client):
    truncated_png = PNG_1X1_BYTES[:15]
    b64 = base64.b64encode(truncated_png).decode("ascii")
    res = client.post(
        "/agent/attachments/prepare",
        json={"name": "truncated.png", "mime_type": "image/png", "data_base64": b64},
    )
    assert res.status_code == 422


# -----------------------------------------------------------------------------
# 3. DOCX Parsing and Security
# -----------------------------------------------------------------------------


def test_prepare_valid_docx(client):
    docx_bytes = make_docx("First paragraph in DOCX.\nSecond line.")
    b64 = base64.b64encode(docx_bytes).decode("ascii")
    res = client.post(
        "/agent/attachments/prepare",
        json={
            "name": "assignment.docx",
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "data_base64": b64,
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["kind"] == "text"
    assert "assignment.docx" in data["name"]
    assert "First paragraph in DOCX." in data["text"]


def test_prepare_docx_with_headers(client):
    docx_bytes = make_docx(
        "Body content here.",
        extra_files={
            "word/header1.xml": """<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
                <w:p><w:r><w:t>Header Note</w:t></w:r></w:p>
            </w:hdr>"""
        },
    )
    b64 = base64.b64encode(docx_bytes).decode("ascii")
    res = client.post(
        "/agent/attachments/prepare",
        json={"name": "doc_header.docx", "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "data_base64": b64},
    )
    assert res.status_code == 200
    data = res.json()
    assert "Body content here." in data["text"]
    assert "Header Note" in data["text"]


def test_reject_docx_missing_document_xml(client):
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w") as zf:
        zf.writestr("word/other.xml", "<root/>")
    b64 = base64.b64encode(bio.getvalue()).decode("ascii")
    res = client.post(
        "/agent/attachments/prepare",
        json={"name": "empty.docx", "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "data_base64": b64},
    )
    assert res.status_code == 422
    assert "missing word/document.xml" in res.json()["detail"]


def test_reject_docx_zipbomb_entry_count(client):
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w") as zf:
        zf.writestr("word/document.xml", "<w:document/>")
        for i in range(MAX_DOCX_ENTRIES + 5):
            zf.writestr(f"file_{i}.txt", "x")
    b64 = base64.b64encode(bio.getvalue()).decode("ascii")
    res = client.post(
        "/agent/attachments/prepare",
        json={"name": "bomb.docx", "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "data_base64": b64},
    )
    assert res.status_code == 422
    assert "maximum entries limit" in res.json()["detail"]


def test_reject_docx_zipbomb_expanded_size(client):
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", "<w:document/>")
        # Create an entry whose uncompressed size exceeds 10MB but compressed is small
        zf.writestr("big.bin", b"0" * (MAX_DOCX_EXPANDED_BYTES + 1024))
    b64 = base64.b64encode(bio.getvalue()).decode("ascii")
    res = client.post(
        "/agent/attachments/prepare",
        json={"name": "huge.docx", "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "data_base64": b64},
    )
    assert res.status_code == 422
    assert "expanded size exceeds maximum limit" in res.json()["detail"]


def test_reject_docx_zipbomb_compression_ratio(client):
    # Highly compressible zeroes with compression ratio > 100:1
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", "<w:document/>")
        zf.writestr("zeroes.bin", b"\x00" * 500_000)
    b64 = base64.b64encode(bio.getvalue()).decode("ascii")
    res = client.post(
        "/agent/attachments/prepare",
        json={"name": "ratio.docx", "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "data_base64": b64},
    )
    assert res.status_code == 422
    assert "compression ratio" in res.json()["detail"]


def test_reject_docx_path_traversal(client):
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w") as zf:
        zf.writestr("word/document.xml", "<w:document/>")
        zf.writestr("../../../etc/passwd", "root:x:0:0")
    b64 = base64.b64encode(bio.getvalue()).decode("ascii")
    res = client.post(
        "/agent/attachments/prepare",
        json={"name": "traversal.docx", "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "data_base64": b64},
    )
    assert res.status_code == 422
    assert "unsafe file paths" in res.json()["detail"]


def test_reject_docx_dtd_xxe(client):
    bio = io.BytesIO()
    doc_with_dtd = """<?xml version="1.0"?>
    <!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body><w:p><w:r><w:t>&xxe;</w:t></w:r></w:p></w:body>
    </w:document>"""
    with zipfile.ZipFile(bio, "w") as zf:
        zf.writestr("word/document.xml", doc_with_dtd)
    b64 = base64.b64encode(bio.getvalue()).decode("ascii")
    res = client.post(
        "/agent/attachments/prepare",
        json={"name": "xxe.docx", "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "data_base64": b64},
    )
    assert res.status_code == 422
    assert "forbidden DTD or entity" in res.json()["detail"]


# -----------------------------------------------------------------------------
# 4. PDF Parsing and Security
# -----------------------------------------------------------------------------


def test_prepare_valid_pdf_with_text(client):
    pdf_bytes = make_pdf_with_text("Sample Student Homework")
    b64 = base64.b64encode(pdf_bytes).decode("ascii")
    res = client.post(
        "/agent/attachments/prepare",
        json={"name": "hw.pdf", "mime_type": "application/pdf", "data_base64": b64},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["kind"] == "text"
    assert data["mime_type"] == "application/pdf"
    assert "Sample Student Homework" in data["text"]


def test_prepare_pdf_mock_extract_text(client):
    pdf_bytes = make_pdf(1)
    b64 = base64.b64encode(pdf_bytes).decode("ascii")
    with patch("pypdf.PageObject.extract_text", return_value="Extracted from mock page."):
        res = client.post(
            "/agent/attachments/prepare",
            json={"name": "mock.pdf", "mime_type": "application/pdf", "data_base64": b64},
        )
    assert res.status_code == 200
    assert res.json()["text"] == "Extracted from mock page."


def test_reject_scanned_or_empty_pdf(client):
    # Blank PDF produces empty text
    pdf_bytes = make_pdf(1)
    b64 = base64.b64encode(pdf_bytes).decode("ascii")
    res = client.post(
        "/agent/attachments/prepare",
        json={"name": "scanned.pdf", "mime_type": "application/pdf", "data_base64": b64},
    )
    assert res.status_code == 422
    assert "OCR is not performed" in res.json()["detail"]


def test_reject_encrypted_pdf(client):
    pdf_bytes = make_pdf(1, encrypted=True)
    b64 = base64.b64encode(pdf_bytes).decode("ascii")
    res = client.post(
        "/agent/attachments/prepare",
        json={"name": "locked.pdf", "mime_type": "application/pdf", "data_base64": b64},
    )
    assert res.status_code == 422
    assert "Encrypted PDF" in res.json()["detail"]


def test_reject_pdf_page_limit(client):
    pdf_bytes = make_pdf(pages=105)
    b64 = base64.b64encode(pdf_bytes).decode("ascii")
    res = client.post(
        "/agent/attachments/prepare",
        json={"name": "long.pdf", "mime_type": "application/pdf", "data_base64": b64},
    )
    assert res.status_code == 422
    assert "maximum page limit of 100 pages" in res.json()["detail"]


def test_reject_corrupt_pdf(client):
    corrupt_bytes = b"%PDF-1.4\nCorrupt header and broken body\x00\xff"
    b64 = base64.b64encode(corrupt_bytes).decode("ascii")
    res = client.post(
        "/agent/attachments/prepare",
        json={"name": "corrupt.pdf", "mime_type": "application/pdf", "data_base64": b64},
    )
    assert res.status_code == 422
    assert "Failed to parse PDF document" in res.json()["detail"]


# -----------------------------------------------------------------------------
# 5. Filename Sanitization and General Guards
# -----------------------------------------------------------------------------


def test_filename_sanitization():
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("..\\..\\windows\\system32\\calc.exe") == "calc.exe"
    assert sanitize_filename("safe_name.txt") == "safe_name.txt"
    assert sanitize_filename("foo\x00bar.py") == "foobar.py"

    with pytest.raises(Exception):
        sanitize_filename("")
    with pytest.raises(Exception):
        sanitize_filename("..")
    with pytest.raises(Exception):
        sanitize_filename("/")


def test_reject_invalid_base64(client):
    res = client.post(
        "/agent/attachments/prepare",
        json={"name": "test.txt", "mime_type": "text/plain", "data_base64": "!!!not_base64!!!"},
    )
    assert res.status_code == 422
    assert "Invalid base64" in res.json()["detail"]


def test_data_uri_prefix_handling(client):
    content = "Hello via data URI"
    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    data_uri = f"data:text/plain;base64,{b64}"
    res = client.post(
        "/agent/attachments/prepare",
        json={"name": "note.txt", "mime_type": "text/plain", "data_base64": data_uri},
    )
    assert res.status_code == 200
    assert res.json()["text"] == content


# -----------------------------------------------------------------------------
# 6. PreparedAttachment and validate_attachment Invariants
# -----------------------------------------------------------------------------


def test_validate_attachment_valid_text():
    raw_dict = {
        "id": "att_123456",
        "name": "example.py",
        "mime_type": "text/x-python",
        "kind": "text",
        "text": "print('hello')",
        "size": 14,
    }
    obj = validate_attachment(raw_dict)
    assert isinstance(obj, PreparedAttachment)
    assert obj.id == "att_123456"
    assert obj.text == "print('hello')"


def test_validate_attachment_valid_image():
    b64_png = base64.b64encode(PNG_1X1_BYTES).decode("ascii")
    raw_dict = {
        "id": "att_img_123",
        "name": "img.png",
        "mime_type": "image/png",
        "kind": "image",
        "data_url": f"data:image/png;base64,{b64_png}",
        "size": len(PNG_1X1_BYTES),
    }
    obj = validate_attachment(raw_dict)
    assert isinstance(obj, PreparedAttachment)
    assert obj.kind == "image"
    assert obj.data_url.startswith("data:image/png;base64,")


def test_validate_attachment_reject_forged_text_with_data_url():
    raw_dict = {
        "id": "att_forged",
        "name": "test.txt",
        "mime_type": "text/plain",
        "kind": "text",
        "text": "hello",
        "data_url": "http://malicious.link",
        "size": 5,
    }
    with pytest.raises(ValueError, match="must not have 'data_url'"):
        validate_attachment(raw_dict)


def test_validate_attachment_reject_forged_image_with_fake_bytes():
    raw_dict = {
        "id": "att_forged_img",
        "name": "evil.png",
        "mime_type": "image/png",
        "kind": "image",
        "data_url": "data:image/png;base64,ZmFrZQ==",  # decodes to 'fake'
        "size": 4,
    }
    with pytest.raises(ValueError, match="valid PNG magic bytes"):
        validate_attachment(raw_dict)


def test_validate_attachment_reject_path_traversal_name():
    raw_dict = {
        "id": "att_safe",
        "name": "../../../etc/passwd",
        "mime_type": "text/plain",
        "kind": "text",
        "text": "safe text",
        "size": 9,
    }
    with pytest.raises(ValueError, match="basename without path traversal"):
        validate_attachment(raw_dict)


def test_validate_attachment_reject_invalid_id():
    raw_dict = {
        "id": "../bad_id",
        "name": "file.txt",
        "mime_type": "text/plain",
        "kind": "text",
        "text": "safe text",
        "size": 9,
    }
    with pytest.raises(ValueError, match="matching"):
        validate_attachment(raw_dict)


# -----------------------------------------------------------------------------
# 7. Concurrency Semaphore
# -----------------------------------------------------------------------------


def test_concurrency_semaphore_limit():
    from backend.app.attachments import PARSE_SEMAPHORE

    assert PARSE_SEMAPHORE._value == 2
