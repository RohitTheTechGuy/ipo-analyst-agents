"""Downloads an RHP. The bytes are untrusted: only a PDF is ever written, under a fixed name."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import httpx

MAX_BYTES = 300 * 1024 * 1024
PDF_MAGIC = b"%PDF-"
ZIP_MAGIC = b"PK\x03\x04"


class RhpDownloadError(Exception):
    """The link did not give a usable RHP PDF."""


def _pdf_from_zip(data: bytes) -> bytes:
    # NSE publishes RHPs as a .zip. Never extract to disk by member name (path traversal);
    # read the largest .pdf member into memory instead.
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        pdfs = [m for m in z.infolist() if m.filename.lower().endswith(".pdf") and not m.is_dir()]
        if not pdfs:
            raise RhpDownloadError("zip has no PDF")
        member = max(pdfs, key=lambda m: m.file_size)
        if member.file_size > MAX_BYTES:
            raise RhpDownloadError("PDF in zip is too large")
        return z.read(member)


class HttpRhpDownloader:
    def __init__(self, client: httpx.Client):
        self._client = client

    def download(self, url: str, dest_dir: Path) -> Path:
        resp = self._client.get(url)
        resp.raise_for_status()
        data = resp.content
        if len(data) > MAX_BYTES:
            raise RhpDownloadError("download is too large")
        if data.startswith(ZIP_MAGIC):
            data = _pdf_from_zip(data)
        if not data.startswith(PDF_MAGIC):
            raise RhpDownloadError(f"not a PDF: {url}")
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        path = dest / "rhp.pdf"
        path.write_bytes(data)
        return path
