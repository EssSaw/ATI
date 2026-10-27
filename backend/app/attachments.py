"""Backend attachments service for ATI.

Provides bounded, safe in-memory preparation of text, code, document, and image attachments.
Strictly parses local data with no disk writes, no network calls, and no code execution.
"""

import asyncio
import base64
import io
import os
import re
import secrets
import xml.etree.ElementTree as ET
import zipfile
from typing import Any, Dict, List, Literal, Optional, Set, Tuple, Union

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

# Safety and budget caps
MAX_BASE64_LENGTH = 7 * 1024 * 1024  # 7MB encoded string cap
MAX_DOC_SIZE_BYTES = 5 * 1024 * 1024  # 5MB decoded doc size cap
MAX_IMAGE_SIZE_BYTES = 4 * 1024 * 1024  # 4MB decoded image cap
MAX_TEXT_CHARACTERS = 100_000  # 100k characters text limit
MAX_PDF_PAGES = 100  # 100 pages cap
MAX_DOCX_ENTRIES = 1000  # 1000 entries cap in docx zip
MAX_DOCX_EXPANDED_BYTES = 10 * 1024 * 1024  # 10MB expanded limit
MAX_DOCX_COMPRESSION_RATIO = 100  # 100:1 compression ratio cap
MAX_FILENAME_LENGTH = 200  # 200 characters max filename

# Concurrency limits: max 2 concurrent parses
MAX_CONCURRENT_PARSES = 2
PARSE_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_PARSES)


def reset_semaphore() -> None:
    """Reset module-level parse semaphore for tests or clean restarts."""
    global PARSE_SEMAPHORE
    PARSE_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_PARSES)


# Regex patterns
SAFE_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
SAFE_MIME_REGEX = re.compile(r"^[a-zA-Z0-9_+.-]+/[a-zA-Z0-9_+.-]+$")
DATA_URL_REGEX = re.compile(r"^data:(image\/(png|jpeg|webp));base64,([A-Za-z0-9+/=]+)$")

# Supported text/code file extensions
TEXT_CODE_EXTENSIONS: Set[str] = {
    "txt", "text", "log", "md", "markdown", "rst",
    "json", "csv", "tsv", "yaml", "yml", "toml", "xml", "ini", "cfg", "conf", "env",
    "js", "jsx", "ts", "tsx", "mjs", "cjs", "html", "htm", "css", "scss", "sass", "less",
    "vue", "svelte", "astro", "graphql", "gql",
    "py", "pyi", "c", "h", "cpp", "hpp", "cs", "java", "go", "rs", "rb", "php", "swift",
    "kt", "kts", "sql", "sh", "bash", "zsh", "dockerfile", "makefile",
}

TEXT_MIME_TYPES: Set[str] = {
    "text/plain", "text/markdown", "text/csv", "text/tab-separated-values",
    "text/html", "text/css", "text/javascript", "text/x-python", "text/x-shellscript",
    "text/xml", "application/json", "application/xml", "application/javascript",
    "application/x-javascript", "application/typescript", "application/x-yaml",
    "application/yaml", "application/toml", "application/x-sh", "application/sql",
}


def sanitize_filename(name: str) -> str:
    """Sanitize attachment name to a safe basename without traversal or control chars."""
    if not name or not isinstance(name, str):
        raise HTTPException(status_code=422, detail="Attachment name must be a non-empty string.")
    cleaned = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", name).strip()
    cleaned = os.path.basename(cleaned.replace("\\", "/"))
    if not cleaned or cleaned in {".", ".."}:
        raise HTTPException(status_code=422, detail="Invalid attachment name: empty or reserved name.")
    if len(cleaned) > MAX_FILENAME_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=f"Attachment name exceeds maximum length of {MAX_FILENAME_LENGTH} characters.",
        )
    return cleaned


class AttachmentPrepareRequest(BaseModel):
    """Incoming request payload for attachment preparation."""
    name: str = Field(..., min_length=1, max_length=MAX_FILENAME_LENGTH)
    mime_type: str = Field(..., min_length=1, max_length=128)
    data_base64: str = Field(..., min_length=1, max_length=MAX_BASE64_LENGTH)


class PreparedAttachment(BaseModel):
    """Normalized, validated prepared attachment schema exported for reuse across the app."""
    id: str = Field(..., description="Unique attachment identifier")
    name: str = Field(..., min_length=1, max_length=MAX_FILENAME_LENGTH, description="Cleaned basename")
    mime_type: str = Field(..., min_length=1, max_length=128, description="Normalized MIME type")
    kind: Literal["text", "image"] = Field(..., description="Attachment category ('text' or 'image')")
    text: Optional[str] = Field(default=None, description="Extracted UTF-8 text for text documents")
    data_url: Optional[str] = Field(default=None, description="Data URL for images")
    size: int = Field(..., ge=1, description="Decoded size in bytes")

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not isinstance(v, str) or not SAFE_ID_REGEX.match(v):
            raise ValueError("Attachment id must be 1-64 characters matching [a-zA-Z0-9_-].")
        return v

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not isinstance(v, str):
            raise ValueError("Attachment name must be a string.")
        cleaned = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", v).strip()
        cleaned_base = os.path.basename(cleaned.replace("\\", "/"))
        if not cleaned_base or cleaned_base in {".", ".."} or "/" in v or "\\" in v:
            raise ValueError("Attachment name must be a clean basename without path traversal or control characters.")
        if len(cleaned_base) > MAX_FILENAME_LENGTH:
            raise ValueError(f"Attachment name exceeds {MAX_FILENAME_LENGTH} characters.")
        return cleaned_base

    @field_validator("mime_type")
    @classmethod
    def validate_mime_type(cls, v: str) -> str:
        if not isinstance(v, str) or not SAFE_MIME_REGEX.match(v):
            raise ValueError("Invalid MIME type format.")
        return v.lower()

    @model_validator(mode="after")
    def validate_content_payload(self) -> "PreparedAttachment":
        if self.kind == "text":
            if self.text is None or not isinstance(self.text, str):
                raise ValueError("Text attachment must have 'text' string populated.")
            if len(self.text) > MAX_TEXT_CHARACTERS:
                raise ValueError(
                    f"Text attachment length ({len(self.text)}) exceeds maximum of {MAX_TEXT_CHARACTERS} characters."
                )
            if self.data_url is not None:
                raise ValueError("Text attachment must not have 'data_url' populated.")
            if self.size > MAX_DOC_SIZE_BYTES:
                raise ValueError(
                    f"Text attachment size ({self.size}) exceeds maximum of {MAX_DOC_SIZE_BYTES} bytes."
                )
        elif self.kind == "image":
            if self.data_url is None or not isinstance(self.data_url, str):
                raise ValueError("Image attachment must have 'data_url' populated.")
            if self.text is not None:
                raise ValueError("Image attachment must not have 'text' populated.")
            if self.mime_type not in {"image/png", "image/jpeg", "image/webp"}:
                raise ValueError(
                    f"Unsupported image MIME type: '{self.mime_type}'. Only image/png, image/jpeg, and image/webp are allowed."
                )
            if self.size > MAX_IMAGE_SIZE_BYTES:
                raise ValueError(
                    f"Image attachment size ({self.size}) exceeds maximum of {MAX_IMAGE_SIZE_BYTES} bytes."
                )
            match = DATA_URL_REGEX.match(self.data_url)
            if not match:
                raise ValueError("Invalid image data_url format. Expected data:image/(png|jpeg|webp);base64,...")
            url_mime, _, b64_data = match.groups()
            if url_mime != self.mime_type:
                raise ValueError(
                    f"Image data_url MIME type '{url_mime}' does not match attachment mime_type '{self.mime_type}'."
                )
            try:
                decoded = base64.b64decode(b64_data, validate=True)
            except Exception:
                raise ValueError("Image data_url has invalid base64 data.")
            if len(decoded) > MAX_IMAGE_SIZE_BYTES:
                raise ValueError(
                    f"Image decoded size ({len(decoded)}) exceeds maximum of {MAX_IMAGE_SIZE_BYTES} bytes."
                )
            # Verify magic bytes of decoded image
            if url_mime == "image/png":
                if not decoded.startswith(b"\x89PNG\r\n\x1a\n") or len(decoded) < 24:
                    raise ValueError("Image data_url does not contain valid PNG magic bytes.")
            elif url_mime == "image/jpeg":
                if not decoded.startswith(b"\xff\xd8") or len(decoded) < 10:
                    raise ValueError("Image data_url does not contain valid JPEG magic bytes.")
            elif url_mime == "image/webp":
                if not (decoded.startswith(b"RIFF") and len(decoded) >= 12 and decoded[8:12] == b"WEBP"):
                    raise ValueError("Image data_url does not contain valid WebP magic bytes.")
        else:
            raise ValueError(f"Invalid kind '{self.kind}'. Must be 'text' or 'image'.")
        return self


def validate_attachment(data: Union[Dict[str, Any], PreparedAttachment]) -> PreparedAttachment:
    """Validate a prepared attachment dictionary or object against all security invariants.
    
    Ensures safe IDs, clean basenames, supported kinds, bounded text length,
    genuine image magic bytes in data URLs, and hard size limits.
    """
    if isinstance(data, PreparedAttachment):
        return PreparedAttachment.model_validate(data.model_dump())
    if isinstance(data, dict):
        return PreparedAttachment.model_validate(data)
    raise ValueError(f"Expected dict or PreparedAttachment, got {type(data).__name__}")


def _normalize_text_mime(mime_type: str, ext: str) -> str:
    """Resolve canonical MIME type for text/code files."""
    ext_map = {
        "json": "application/json",
        "md": "text/markdown",
        "markdown": "text/markdown",
        "csv": "text/csv",
        "tsv": "text/tab-separated-values",
        "html": "text/html",
        "htm": "text/html",
        "css": "text/css",
        "js": "text/javascript",
        "jsx": "text/javascript",
        "mjs": "text/javascript",
        "cjs": "text/javascript",
        "ts": "text/typescript",
        "tsx": "text/typescript",
        "py": "text/x-python",
        "pyi": "text/x-python",
        "xml": "application/xml",
        "yaml": "application/x-yaml",
        "yml": "application/x-yaml",
        "toml": "application/toml",
        "sh": "application/x-sh",
        "bash": "application/x-sh",
        "zsh": "application/x-sh",
        "sql": "application/sql",
    }
    if ext in ext_map:
        return ext_map[ext]
    if mime_type and (mime_type.startswith("text/") or "/" in mime_type):
        return mime_type.lower()
    return "text/plain"


def _validate_image_magic(raw_bytes: bytes) -> str:
    """Validate image magic bytes and basic structural integrity. Returns canonical MIME type."""
    if len(raw_bytes) < 12:
        raise HTTPException(status_code=422, detail="Malformed image data: file is truncated.")

    # Check for forbidden image formats
    if raw_bytes.startswith(b"GIF87a") or raw_bytes.startswith(b"GIF89a"):
        raise HTTPException(
            status_code=422,
            detail="Unsupported image format. GIF is not supported (only PNG, JPEG, and WebP are allowed).",
        )
    if raw_bytes.startswith(b"BM"):
        raise HTTPException(
            status_code=422,
            detail="Unsupported image format. BMP is not supported (only PNG, JPEG, and WebP are allowed).",
        )
    if b"<svg" in raw_bytes[:1024].lower() or b"<?xml" in raw_bytes[:128].lower():
        if b"<svg" in raw_bytes[:2048].lower():
            raise HTTPException(
                status_code=422,
                detail="Unsupported image format. SVG is not supported (only PNG, JPEG, and WebP are allowed).",
            )

    # PNG validation
    if raw_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        if len(raw_bytes) < 24:
            raise HTTPException(status_code=422, detail="Malformed PNG image: missing IHDR chunk.")
        if raw_bytes[12:16] != b"IHDR":
            raise HTTPException(status_code=422, detail="Malformed PNG image: first chunk must be IHDR.")
        width = int.from_bytes(raw_bytes[16:20], "big")
        height = int.from_bytes(raw_bytes[20:24], "big")
        if width <= 0 or height <= 0:
            raise HTTPException(status_code=422, detail="Malformed PNG image: invalid dimensions.")
        return "image/png"

    # JPEG validation
    if raw_bytes.startswith(b"\xff\xd8"):
        if len(raw_bytes) < 10:
            raise HTTPException(status_code=422, detail="Malformed JPEG image: file is truncated.")
        if raw_bytes[2] != 0xFF:
            raise HTTPException(status_code=422, detail="Malformed JPEG image: invalid SOI marker.")
        return "image/jpeg"

    # WebP validation
    if raw_bytes.startswith(b"RIFF") and len(raw_bytes) >= 16 and raw_bytes[8:12] == b"WEBP":
        return "image/webp"

    raise HTTPException(
        status_code=422,
        detail="Unsupported or malformed image format. Only PNG, JPEG, and WebP are allowed.",
    )


def _parse_docx(raw_bytes: bytes) -> str:
    """Safe extraction of text from DOCX files using stdlib zipfile and xml.etree.ElementTree.
    
    Guards against zip bombs, path traversal, XXE, and entity expansion.
    """
    if len(raw_bytes) > MAX_DOC_SIZE_BYTES:
        raise HTTPException(
            status_code=422,
            detail=f"DOCX file size exceeds maximum limit of {MAX_DOC_SIZE_BYTES // (1024 * 1024)}MB.",
        )

    try:
        zf = zipfile.ZipFile(io.BytesIO(raw_bytes), "r")
    except zipfile.BadZipFile:
        raise HTTPException(status_code=422, detail="Invalid DOCX file: corrupted or invalid ZIP archive.")
    except Exception:
        raise HTTPException(status_code=422, detail="Failed to open DOCX archive.")

    with zf:
        entries = zf.infolist()
        if len(entries) > MAX_DOCX_ENTRIES:
            raise HTTPException(
                status_code=422,
                detail=f"DOCX archive exceeds maximum entries limit ({MAX_DOCX_ENTRIES}).",
            )

        total_expanded = 0
        for entry in entries:
            name = entry.filename
            # Path traversal check
            if name.startswith("/") or name.startswith("\\") or ".." in name.replace("\\", "/").split("/"):
                raise HTTPException(status_code=422, detail="DOCX archive contains unsafe file paths.")

            total_expanded += entry.file_size
            if total_expanded > MAX_DOCX_EXPANDED_BYTES:
                raise HTTPException(
                    status_code=422,
                    detail=f"DOCX expanded size exceeds maximum limit of {MAX_DOCX_EXPANDED_BYTES // (1024 * 1024)}MB.",
                )

            if entry.compress_size > 0:
                ratio = entry.file_size / entry.compress_size
                if ratio > MAX_DOCX_COMPRESSION_RATIO:
                    raise HTTPException(
                        status_code=422,
                        detail=f"DOCX compression ratio ({ratio:.1f}:1) exceeds safety limit ({MAX_DOCX_COMPRESSION_RATIO}:1).",
                    )
            elif entry.file_size > 1024:
                raise HTTPException(
                    status_code=422,
                    detail="DOCX archive contains entry with suspicious compression ratio.",
                )

        namelist = set(zf.namelist())
        if "word/document.xml" not in namelist:
            raise HTTPException(status_code=422, detail="Invalid DOCX file: missing word/document.xml.")

        # Read main document and headers/footers
        files_to_read = ["word/document.xml"]
        for fname in sorted(namelist):
            if re.match(r"^word/(header|footer)\d+\.xml$", fname):
                files_to_read.append(fname)

        total_read = 0
        extracted_paragraphs: List[str] = []
        for fname in files_to_read:
            try:
                with zf.open(fname, "r") as f:
                    xml_data = f.read(MAX_DOCX_EXPANDED_BYTES + 1)
            except Exception:
                raise HTTPException(status_code=422, detail=f"Failed to read {fname} from DOCX archive.")

            total_read += len(xml_data)
            if total_read > MAX_DOCX_EXPANDED_BYTES:
                raise HTTPException(
                    status_code=422,
                    detail=f"DOCX expanded size exceeds maximum limit of {MAX_DOCX_EXPANDED_BYTES // (1024 * 1024)}MB.",
                )

            # Check for DTD or Entity injection attempts
            upper_xml = xml_data[:2048].upper()
            if b"<!DOCTYPE" in upper_xml or b"<!ENTITY" in upper_xml:
                raise HTTPException(status_code=422, detail="DOCX XML contains forbidden DTD or entity declarations.")

            try:
                root = ET.fromstring(xml_data)
            except ET.ParseError:
                raise HTTPException(status_code=422, detail=f"Failed to parse XML in DOCX {fname}.")

            # Extract paragraphs and text runs
            for elem in root.iter():
                tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                if tag == "p":
                    p_parts = []
                    for child in elem.iter():
                        c_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                        if c_tag == "t" and child.text:
                            p_parts.append(child.text)
                        elif c_tag == "tab":
                            p_parts.append("\t")
                        elif c_tag in ("br", "cr"):
                            p_parts.append("\n")
                    text_p = "".join(p_parts).strip()
                    if text_p:
                        extracted_paragraphs.append(text_p)

        full_text = "\n\n".join(extracted_paragraphs).strip()
        if not full_text:
            raise HTTPException(status_code=422, detail="DOCX document contains no extractable text.")
        if len(full_text) > MAX_TEXT_CHARACTERS:
            raise HTTPException(
                status_code=422,
                detail=f"Extracted DOCX text exceeds maximum limit of {MAX_TEXT_CHARACTERS:,} characters.",
            )
        return full_text


def _parse_pdf(raw_bytes: bytes) -> str:
    """Extract text from PDF using pypdf.
    
    Guards against encrypted, scanned/empty, oversized pages, and oversized text.
    Sanitizes parser errors so internal paths and secrets are never exposed.
    """
    if len(raw_bytes) > MAX_DOC_SIZE_BYTES:
        raise HTTPException(
            status_code=422,
            detail=f"PDF file size exceeds maximum limit of {MAX_DOC_SIZE_BYTES // (1024 * 1024)}MB.",
        )

    if not raw_bytes.startswith(b"%PDF-"):
        raise HTTPException(status_code=422, detail="Invalid PDF file: missing %PDF- header.")

    try:
        import pypdf
    except ImportError:
        raise HTTPException(status_code=500, detail="PDF processing dependency 'pypdf' is not installed.")

    try:
        reader = pypdf.PdfReader(io.BytesIO(raw_bytes))
    except Exception:
        raise HTTPException(status_code=422, detail="Failed to parse PDF document: invalid or corrupted file format.")

    try:
        if reader.is_encrypted:
            raise HTTPException(status_code=422, detail="Encrypted PDF files are not supported.")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=422, detail="Failed to check PDF encryption status: corrupted file format.")

    try:
        num_pages = len(reader.pages)
    except Exception:
        raise HTTPException(status_code=422, detail="Failed to read PDF pages: corrupted file format.")

    if num_pages == 0:
        raise HTTPException(status_code=422, detail="PDF contains no pages.")
    if num_pages > MAX_PDF_PAGES:
        raise HTTPException(
            status_code=422,
            detail=f"PDF exceeds maximum page limit of {MAX_PDF_PAGES} pages (found {num_pages}).",
        )

    page_texts: List[str] = []
    total_chars = 0
    for page_idx, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception:
            raise HTTPException(
                status_code=422,
                detail=f"Failed to extract text from PDF page {page_idx + 1}: corrupted page data.",
            )

        cleaned = text.strip()
        if cleaned:
            page_texts.append(cleaned)
            total_chars += len(cleaned)
            if total_chars > MAX_TEXT_CHARACTERS:
                raise HTTPException(
                    status_code=422,
                    detail=f"Extracted PDF text exceeds maximum limit of {MAX_TEXT_CHARACTERS:,} characters.",
                )

    full_text = "\n\n".join(page_texts).strip()
    if not full_text:
        raise HTTPException(
            status_code=422,
            detail="PDF contains no extractable text (scanned or image-only PDFs are not supported; OCR is not performed).",
        )
    return full_text


def _parse_text(raw_bytes: bytes, mime_type: str, ext: str) -> Tuple[str, str]:
    """Parse and validate UTF-8 text/code file."""
    if len(raw_bytes) > MAX_DOC_SIZE_BYTES:
        raise HTTPException(
            status_code=422,
            detail=f"Document file size exceeds maximum limit of {MAX_DOC_SIZE_BYTES // (1024 * 1024)}MB.",
        )

    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=422, detail="Text attachment is not valid UTF-8.")

    if "\x00" in text:
        raise HTTPException(status_code=422, detail="Text attachment contains null bytes or binary data.")

    if not text.strip():
        raise HTTPException(status_code=422, detail="Text attachment is empty.")

    if len(text) > MAX_TEXT_CHARACTERS:
        raise HTTPException(
            status_code=422,
            detail=f"Text content exceeds maximum limit of {MAX_TEXT_CHARACTERS:,} characters (found {len(text):,}).",
        )

    canonical_mime = _normalize_text_mime(mime_type, ext)
    return text, canonical_mime


def _prepare_attachment_sync(req: AttachmentPrepareRequest) -> PreparedAttachment:
    """Synchronous parsing worker executed within the threadpool."""
    clean_name = sanitize_filename(req.name)

    # Clean and validate base64
    raw_b64 = req.data_base64.strip()
    if raw_b64.startswith("data:") and ";base64," in raw_b64:
        clean_base64 = raw_b64.split(";base64,", 1)[1].strip()
    else:
        clean_base64 = raw_b64

    if len(clean_base64) > MAX_BASE64_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=f"Base64 data exceeds maximum limit of {MAX_BASE64_LENGTH // (1024 * 1024)}MB.",
        )

    try:
        raw_bytes = base64.b64decode(clean_base64, validate=True)
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid base64 payload.")

    if len(raw_bytes) == 0:
        raise HTTPException(status_code=422, detail="Attachment payload is empty.")

    _, ext_with_dot = os.path.splitext(clean_name)
    ext = ext_with_dot.lower().lstrip(".")
    req_mime = req.mime_type.strip().lower()

    # Reject forbidden formats explicitly (SVG, GIF)
    if ext in {"svg", "gif"} or req_mime in {"image/svg+xml", "image/svg", "image/gif"}:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported format '{ext or req_mime}'. SVG and GIF are not supported (images only allow PNG, JPEG, and WebP).",
        )
    if raw_bytes.startswith(b"GIF87a") or raw_bytes.startswith(b"GIF89a"):
        raise HTTPException(
            status_code=422,
            detail="Unsupported format. GIF images are not supported (only PNG, JPEG, and WebP are allowed).",
        )
    if b"<svg" in raw_bytes[:1024].lower():
        raise HTTPException(
            status_code=422,
            detail="Unsupported format. SVG files are not supported.",
        )

    # 1. Images (PNG, JPEG, WebP)
    is_image_magic = (
        raw_bytes.startswith(b"\x89PNG\r\n\x1a\n")
        or raw_bytes.startswith(b"\xff\xd8")
        or (raw_bytes.startswith(b"RIFF") and len(raw_bytes) >= 12 and raw_bytes[8:12] == b"WEBP")
    )

    if is_image_magic:
        norm_mime = _validate_image_magic(raw_bytes)
        if len(raw_bytes) > MAX_IMAGE_SIZE_BYTES:
            raise HTTPException(
                status_code=422,
                detail=f"Image size ({len(raw_bytes):,} bytes) exceeds maximum limit of {MAX_IMAGE_SIZE_BYTES // (1024 * 1024)}MB.",
            )
        data_url = f"data:{norm_mime};base64,{clean_base64}"
        att_id = f"att_{secrets.token_hex(16)}"
        return PreparedAttachment(
            id=att_id,
            name=clean_name,
            mime_type=norm_mime,
            kind="image",
            data_url=data_url,
            size=len(raw_bytes),
        )
    elif req_mime.startswith("image/") or ext in {"png", "jpg", "jpeg", "webp"}:
        raise HTTPException(
            status_code=422,
            detail="Unsupported or malformed image format. Only valid PNG, JPEG, and WebP images are supported.",
        )

    # 2. PDF
    if raw_bytes.startswith(b"%PDF-") or ext == "pdf" or req_mime == "application/pdf":
        text = _parse_pdf(raw_bytes)
        att_id = f"att_{secrets.token_hex(16)}"
        return PreparedAttachment(
            id=att_id,
            name=clean_name,
            mime_type="application/pdf",
            kind="text",
            text=text,
            size=len(raw_bytes),
        )

    # 3. DOCX
    is_docx = (
        ext == "docx"
        or req_mime in {
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/msword",
        }
        or (raw_bytes.startswith(b"PK\x03\x04") and ext in {"docx", ""})
    )
    if is_docx:
        text = _parse_docx(raw_bytes)
        att_id = f"att_{secrets.token_hex(16)}"
        return PreparedAttachment(
            id=att_id,
            name=clean_name,
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            kind="text",
            text=text,
            size=len(raw_bytes),
        )

    # 4. Text and Code formats
    is_text = (
        ext in TEXT_CODE_EXTENSIONS
        or req_mime.startswith("text/")
        or req_mime in TEXT_MIME_TYPES
        or req_mime in {"application/json", "application/xml", "application/x-yaml"}
    )
    if is_text:
        text, norm_mime = _parse_text(raw_bytes, req_mime, ext)
        att_id = f"att_{secrets.token_hex(16)}"
        return PreparedAttachment(
            id=att_id,
            name=clean_name,
            mime_type=norm_mime,
            kind="text",
            text=text,
            size=len(raw_bytes),
        )

    # 5. Fallback UTF-8 probe for unknown plain text
    if not any(b == 0 for b in raw_bytes[:1024]) and len(raw_bytes) <= MAX_DOC_SIZE_BYTES:
        try:
            text = raw_bytes.decode("utf-8")
            if "\x00" not in text and text.strip() and len(text) <= MAX_TEXT_CHARACTERS:
                att_id = f"att_{secrets.token_hex(16)}"
                return PreparedAttachment(
                    id=att_id,
                    name=clean_name,
                    mime_type="text/plain",
                    kind="text",
                    text=text,
                    size=len(raw_bytes),
                )
        except Exception:
            pass

    raise HTTPException(
        status_code=422,
        detail=f"Unsupported attachment format or MIME type: '{req.mime_type}'.",
    )


# FastAPI Router
router = APIRouter(tags=["attachments"])


@router.post(
    "/agent/attachments/prepare",
    response_model=PreparedAttachment,
    response_model_exclude_none=True,
)
@router.post(
    "/prepare",
    response_model=PreparedAttachment,
    response_model_exclude_none=True,
)
async def prepare_attachment_endpoint(req: AttachmentPrepareRequest) -> PreparedAttachment:
    """Prepare and validate an attachment for project chat.
    
    Extracts bounded, safe text from documents (UTF-8, PDF, DOCX) or normalizes
    and verifies images (PNG, JPEG, WebP).
    Never executes contents; offloads CPU-bound parsing to the threadpool.
    """
    async with PARSE_SEMAPHORE:
        return await asyncio.to_thread(_prepare_attachment_sync, req)
