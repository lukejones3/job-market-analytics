"""Parse resume bytes into clean text. Supports .pdf, .docx, .txt."""

import io
import re
from typing import Optional

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

try:
    import docx
except ImportError:
    docx = None


def _clean_text(s: str) -> str:
    """Strip nbsp, collapse whitespace within lines, preserve newlines."""
    s = s.replace("\u00a0", " ")
    # Collapse multiple spaces/tabs but preserve newlines
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


def parse_resume(file_bytes: bytes, filename: str) -> str:
    """Parse a resume file's bytes into clean text.

    Args:
        file_bytes: Raw bytes of the uploaded file
        filename: Filename (used for extension detection)

    Returns:
        Clean text content of the resume

    Raises:
        ValueError: If file type is unsupported
        RuntimeError: If required parser library is not installed
    """
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""

    if suffix == "txt":
        try:
            text = file_bytes.decode("utf-8", errors="ignore")
        except Exception:
            text = file_bytes.decode("latin-1", errors="ignore")
        return _clean_text(text)

    if suffix == "pdf":
        if pdfplumber is None:
            raise RuntimeError("pdfplumber not installed. pip install pdfplumber")
        parts = []
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                t = page.extract_text() or ""
                parts.append(t)
        return _clean_text("\n".join(parts))

    if suffix == "docx":
        if docx is None:
            raise RuntimeError("python-docx not installed. pip install python-docx")
        d = docx.Document(io.BytesIO(file_bytes))
        parts = [p.text for p in d.paragraphs if p.text]
        return _clean_text("\n".join(parts))

    raise ValueError(f"Unsupported file type: .{suffix}. Use .pdf, .docx, or .txt")
