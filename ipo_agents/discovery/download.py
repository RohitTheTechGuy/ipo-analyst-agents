"""Downloads an RHP. The bytes are untrusted: only a PDF is ever written, under a fixed name.

Only https links on the exchanges' own hosts are fetched, and every redirect hop is checked
before it is followed, so the final URL is always on the allowlist too.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

MAX_BYTES = 150 * 1024 * 1024
MAX_RATIO = 200  # uncompressed / compressed; real PDFs are close to 1
MAX_REDIRECTS = 5
ALLOWED_HOSTS = ("nseindia.com", "nsearchives.nseindia.com", "archives.nseindia.com",
                 "bseindia.com", "listing.bseindia.com")
PDF_MAGIC = b"%PDF-"
ZIP_MAGIC = b"PK\x03\x04"


class RhpDownloadError(Exception):
    """The link did not give a usable RHP PDF."""


def _check_url(url: str) -> None:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS):
        raise RhpDownloadError(f"RHP host not allowed: {url}")


def _pdf_from_zip(data: bytes) -> bytes:
    # NSE publishes RHPs as a .zip. Never extract to disk by member name (path traversal);
    # the largest member must itself be the PDF, read into memory with size checks.
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            members = [m for m in z.infolist() if not m.is_dir()]
            if not members:
                raise RhpDownloadError("zip is empty")
            member = max(members, key=lambda m: m.file_size)
            if not member.filename.lower().endswith(".pdf"):
                raise RhpDownloadError(f"largest zip member is not a PDF: {member.filename!r}")
            if member.flag_bits & 0x1:
                raise RhpDownloadError("zip member is encrypted")
            if member.file_size > MAX_BYTES:
                raise RhpDownloadError("PDF in zip is too large")
            if member.file_size > MAX_RATIO * max(member.compress_size, 1):
                raise RhpDownloadError("zip member compression ratio is suspicious")
            with z.open(member) as f:
                pdf = f.read(MAX_BYTES + 1)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError, RuntimeError, EOFError) as exc:
        raise RhpDownloadError(f"unreadable zip: {exc!r}") from exc
    if len(pdf) > MAX_BYTES:
        raise RhpDownloadError("PDF in zip is too large")
    if pdf.startswith(ZIP_MAGIC):
        raise RhpDownloadError("nested zip")
    return pdf


class HttpRhpDownloader:
    def __init__(self, client: httpx.Client):
        self._client = client

    def _fetch(self, url: str) -> bytes:
        for _ in range(MAX_REDIRECTS + 1):
            _check_url(url)
            with self._client.stream("GET", url, follow_redirects=False) as resp:
                if resp.is_redirect:
                    url = urljoin(url, resp.headers["Location"])
                    continue
                resp.raise_for_status()
                if int(resp.headers.get("Content-Length") or 0) > MAX_BYTES:
                    raise RhpDownloadError("download is too large")
                chunks, total = [], 0
                for chunk in resp.iter_bytes():
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise RhpDownloadError("download is too large")
                    chunks.append(chunk)
                return b"".join(chunks)
        raise RhpDownloadError("too many redirects")

    def download(self, url: str, dest_dir: Path) -> Path:
        data = self._fetch(url)
        if data.startswith(ZIP_MAGIC):
            data = _pdf_from_zip(data)
        if not data.startswith(PDF_MAGIC):
            raise RhpDownloadError(f"not a PDF: {url}")
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        path = dest / "rhp.pdf"
        path.write_bytes(data)
        return path
