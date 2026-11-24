"""Downloads an RHP. The bytes are untrusted: only a PDF is ever written, under a fixed name.

Only https links on the sources' own hosts (the exchanges and SEBI) are fetched, and every
redirect hop is checked before it is followed, so the final URL is always on the allowlist too.

SEBI links a filing from an HTML page rather than serving the PDF itself, so a page that is not a
document is read for the .pdf it links (one level only, still on the allowlist, still checked).
"""
from __future__ import annotations

import html
import io
import re
import zipfile
from pathlib import Path
from urllib.parse import parse_qsl, urljoin, urlsplit

import httpx

MAX_BYTES = 150 * 1024 * 1024
MAX_RATIO = 200  # uncompressed / compressed; real PDFs are close to 1
MAX_REDIRECTS = 5
MAX_HTML_BYTES = 2 * 1024 * 1024  # a filing page, not a document
MAX_PAGE_LINKS = 3  # document links tried on one filing page
ALLOWED_HOSTS = ("nseindia.com", "nsearchives.nseindia.com", "archives.nseindia.com",
                 "bseindia.com", "listing.bseindia.com", "sebi.gov.in")
PDF_MAGIC = b"%PDF-"
ZIP_MAGIC = b"PK\x03\x04"
# SEBI wraps the document: /web/?file=https://www.sebi.gov.in/sebi_data/attachdocs/...pdf
# A lookahead, so matches overlap: a viewer URL that wraps the PDF URL yields the inner one too.
PDF_URL = re.compile(r"(?=(https?://[^\"'\s<>]+?\.pdf(?:\?[^\"'\s<>]*)?))", re.IGNORECASE)
ATTRIBUTE = re.compile(r"(?:href|src)=[\"']([^\"']+)[\"']", re.IGNORECASE)


class RhpDownloadError(Exception):
    """The link did not give a usable RHP PDF."""


def _allowed(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)


def _check_url(url: str) -> None:
    if not _allowed(url):
        raise RhpDownloadError(f"RHP host not allowed: {url}")


def _looks_like_html(data: bytes) -> bool:
    head = data[:512].lstrip().lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html")


def _document_links(page: str, base: str) -> list[str]:
    """Absolute https .pdf links on a filing page, in page order: href/src attributes first, then any
    bare URL (SEBI passes the document as a query parameter). Relative links resolve against the page."""
    found: list[str] = []

    def add(raw: str) -> None:
        url = urljoin(base, html.unescape(raw).strip())
        path = urlsplit(url).path.lower()
        if urlsplit(url).scheme == "https" and path.endswith(".pdf") and url not in found:
            found.append(url)

    for value in ATTRIBUTE.findall(page):
        add(value)
        # A viewer link carries the document in its query, absolute or relative, maybe percent-encoded.
        for _, param in parse_qsl(urlsplit(html.unescape(value)).query):
            add(param)
    for value in PDF_URL.findall(html.unescape(page)):
        add(value)
    return found


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

    def _pdf_behind_page(self, page_url: str, page: bytes) -> bytes:
        """The PDF a filing page links to. Only links the allowlist permits are tried, and `_fetch`
        re-checks every redirect hop, so a page cannot point the downloader at another host. One level:
        the linked URL must itself be the PDF."""
        if len(page) > MAX_HTML_BYTES:
            raise RhpDownloadError(f"filing page is too large: {page_url}")
        links = [link for link in _document_links(page.decode("utf-8", "replace"), page_url) if _allowed(link)]
        if not links:
            raise RhpDownloadError(f"no PDF link on the filing page: {page_url}")
        for link in links[:MAX_PAGE_LINKS]:
            try:
                data = self._fetch(link)
            except (httpx.HTTPError, RhpDownloadError):
                continue  # a dead or blocked link: try the next one on the page
            if data.startswith(PDF_MAGIC):
                return data
        raise RhpDownloadError(f"no PDF found behind the filing page: {page_url}")

    def download(self, url: str, dest_dir: Path) -> Path:
        data = self._fetch(url)
        if _looks_like_html(data):
            data = self._pdf_behind_page(url, data)
        if data.startswith(ZIP_MAGIC):
            data = _pdf_from_zip(data)
        if not data.startswith(PDF_MAGIC):
            raise RhpDownloadError(f"not a PDF: {url}")
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        path = dest / "rhp.pdf"
        path.write_bytes(data)
        return path
