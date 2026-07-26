from __future__ import annotations

import gzip
import io
import re
import tarfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath

USER_AGENT = "arxiv-doublecheck/0.1 (+https://github.com/)"
MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
MAX_EXTRACTED_BYTES = 200 * 1024 * 1024
MAX_SOURCE_FILES = 5_000
MIN_REQUEST_INTERVAL_SECONDS = 3.0
MAX_REQUEST_ATTEMPTS = 5
RETRYABLE_HTTP_STATUS = {429, 500, 502, 503, 504}

MODERN_ID_RE = re.compile(r"^\d{4}\.\d{4,5}(?:v\d+)?$")
LEGACY_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9.-]*/\d{7}(?:v\d+)?$")
VERSION_RE = re.compile(r"v(\d+)$")
_REQUEST_LOCK = threading.Lock()
_last_request_started = 0.0


class ArxivError(RuntimeError):
    pass


@dataclass(frozen=True)
class PaperMetadata:
    arxiv_id: str
    title: str
    authors: tuple[str, ...]
    summary: str
    published: str
    updated: str
    primary_category: str = "unknown"

    @property
    def version(self) -> int:
        match = VERSION_RE.search(self.arxiv_id)
        return int(match.group(1)) if match else 1

    @property
    def abstract_url(self) -> str:
        return f"https://arxiv.org/abs/{self.arxiv_id}"

    @property
    def pdf_url(self) -> str:
        return f"https://arxiv.org/pdf/{self.arxiv_id}.pdf"


@dataclass(frozen=True)
class SourceExtraction:
    available: bool
    files: int
    note: str


def normalize_arxiv_id(value: str) -> str:
    candidate = value.strip()
    if candidate.lower().startswith("arxiv:"):
        candidate = candidate[6:].strip()

    parsed = urllib.parse.urlparse(candidate)
    if parsed.scheme or parsed.netloc:
        if parsed.netloc.lower() not in {
            "arxiv.org",
            "www.arxiv.org",
            "export.arxiv.org",
        }:
            raise ArxivError(f"not an arXiv URL: {value}")
        path = urllib.parse.unquote(parsed.path).strip("/")
        for prefix in ("abs/", "pdf/", "src/", "e-print/"):
            if path.startswith(prefix):
                candidate = path[len(prefix) :]
                break
        else:
            raise ArxivError(f"unsupported arXiv URL: {value}")
        if candidate.endswith(".pdf"):
            candidate = candidate[:-4]

    if not (MODERN_ID_RE.fullmatch(candidate) or LEGACY_ID_RE.fullmatch(candidate)):
        raise ArxivError(f"invalid arXiv identifier: {value}")
    return candidate


def fetch_metadata(arxiv_id: str, timeout: int = 30) -> PaperMetadata:
    try:
        return _fetch_api_metadata(arxiv_id, timeout)
    except ArxivError as api_error:
        try:
            return _fetch_html_metadata(arxiv_id, timeout)
        except ArxivError as html_error:
            raise ArxivError(
                f"could not fetch arXiv metadata: API: {api_error}; "
                f"paper page: {html_error}"
            ) from html_error


def _fetch_api_metadata(arxiv_id: str, timeout: int) -> PaperMetadata:
    query = urllib.parse.urlencode({"id_list": arxiv_id})
    request = urllib.request.Request(
        f"https://export.arxiv.org/api/query?{query}",
        headers={"User-Agent": USER_AGENT},
    )
    try:
        with _urlopen(request, timeout=timeout, max_attempts=1) as response:
            body = _read_limited(response, 5 * 1024 * 1024)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ArxivError(str(exc)) from exc

    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise ArxivError("arXiv returned invalid metadata XML") from exc

    atom = {"atom": "http://www.w3.org/2005/Atom"}
    namespaces = {
        **atom,
        "arxiv": "http://arxiv.org/schemas/atom",
    }
    entry = root.find("atom:entry", atom)
    if entry is None:
        raise ArxivError(f"arXiv paper not found: {arxiv_id}")

    identifier = _arxiv_id_from_entry_url(
        _required_text(entry, "atom:id", atom)
    )
    title = _collapse_whitespace(_required_text(entry, "atom:title", atom))
    summary = _collapse_whitespace(_required_text(entry, "atom:summary", atom))
    authors = tuple(
        _collapse_whitespace(name.text or "")
        for name in entry.findall("atom:author/atom:name", atom)
        if (name.text or "").strip()
    )
    primary_category = entry.find("arxiv:primary_category", namespaces)
    if primary_category is not None:
        category = primary_category.attrib.get("term", "unknown")
    else:
        fallback_category = entry.find("atom:category", atom)
        category = (
            fallback_category.attrib.get("term", "unknown")
            if fallback_category is not None
            else "unknown"
        )
    return PaperMetadata(
        arxiv_id=identifier,
        title=title,
        authors=authors,
        summary=summary,
        published=_required_text(entry, "atom:published", atom),
        updated=_required_text(entry, "atom:updated", atom),
        primary_category=category,
    )


def _fetch_html_metadata(arxiv_id: str, timeout: int) -> PaperMetadata:
    request = urllib.request.Request(
        f"https://arxiv.org/abs/{arxiv_id}",
        headers={"User-Agent": USER_AGENT},
    )
    try:
        with _urlopen(request, timeout=timeout) as response:
            body = _read_limited(response, 5 * 1024 * 1024)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ArxivError(str(exc)) from exc
    return _parse_html_metadata(body.decode("utf-8", errors="replace"), arxiv_id)


def _parse_html_metadata(document: str, requested_id: str) -> PaperMetadata:
    parser = _ArxivHtmlMetadataParser()
    parser.feed(document)
    title = parser.first("citation_title")
    summary = parser.first("citation_abstract")
    published = _html_date(parser.first("citation_date"))
    updated = _html_date(
        parser.first("citation_online_date", required=False)
        or parser.first("citation_date")
    )
    authors = tuple(
        _citation_author(author)
        for author in parser.values.get("citation_author", ())
    )
    if not authors:
        raise ArxivError("paper page is missing authors")

    if VERSION_RE.search(requested_id):
        versioned_id = requested_id
    else:
        escaped_id = re.escape(requested_id)
        versions = [
            int(version)
            for version in re.findall(rf"{escaped_id}v(\d+)", document)
        ]
        versioned_id = (
            f"{requested_id}v{max(versions)}" if versions else requested_id
        )

    return PaperMetadata(
        arxiv_id=versioned_id,
        title=_collapse_whitespace(title),
        authors=authors,
        summary=_collapse_whitespace(summary),
        published=published,
        updated=updated,
        primary_category=parser.primary_category or "unknown",
    )


def download_pdf(metadata: PaperMetadata, destination: Path, timeout: int = 60) -> None:
    _download(metadata.pdf_url, destination, timeout=timeout)
    with destination.open("rb") as handle:
        signature = handle.read(5)
    if signature != b"%PDF-":
        destination.unlink(missing_ok=True)
        raise ArxivError("arXiv did not return a valid PDF")


def download_and_extract_source(
    metadata: PaperMetadata,
    destination: Path,
    timeout: int = 30,
) -> SourceExtraction:
    url = f"https://export.arxiv.org/e-print/{metadata.arxiv_id}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with _urlopen(
            request,
            timeout=timeout,
            max_attempts=1,
        ) as response:
            payload = _read_limited(response, MAX_DOWNLOAD_BYTES)
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code <= 399 or exc.code in {403, 404, 429} or 500 <= exc.code <= 599:
            return SourceExtraction(False, 0, f"source unavailable (HTTP {exc.code})")
        raise ArxivError(f"could not download arXiv source: HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        return SourceExtraction(False, 0, f"source unavailable ({exc})")

    destination.mkdir(parents=True, exist_ok=True)
    try:
        return _extract_tar(payload, destination)
    except (tarfile.TarError, EOFError):
        pass

    try:
        decompressed = gzip.decompress(payload)
    except (gzip.BadGzipFile, EOFError):
        decompressed = payload

    try:
        return _extract_tar(decompressed, destination)
    except (tarfile.TarError, EOFError):
        if b"\x00" in decompressed[:4096]:
            return SourceExtraction(False, 0, "source payload was not a supported archive")
        suffix = ".ps" if decompressed.startswith(b"%!PS") else ".tex"
        target = destination / f"paper{suffix}"
        target.write_bytes(decompressed)
        return SourceExtraction(True, 1, "single source file extracted")


def _download(url: str, destination: Path, timeout: int) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with _urlopen(request, timeout=timeout) as response:
            payload = _read_limited(response, MAX_DOWNLOAD_BYTES)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ArxivError(f"could not download {url}: {exc}") from exc
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)


def _urlopen(
    request: urllib.request.Request,
    timeout: int,
    max_attempts: int = MAX_REQUEST_ATTEMPTS,
) -> object:
    global _last_request_started
    for attempt in range(max_attempts):
        with _REQUEST_LOCK:
            wait_seconds = (
                _last_request_started
                + MIN_REQUEST_INTERVAL_SECONDS
                - time.monotonic()
            )
            if wait_seconds > 0:
                time.sleep(wait_seconds)
            _last_request_started = time.monotonic()
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if (
                exc.code not in RETRYABLE_HTTP_STATUS
                or attempt == max_attempts - 1
            ):
                raise
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            if exc.fp is not None:
                exc.close()
            try:
                retry_seconds = int(retry_after) if retry_after else 0
            except ValueError:
                retry_seconds = 0
            time.sleep(max(retry_seconds, min(15 * (2**attempt), 120)))
        except (urllib.error.URLError, TimeoutError):
            if attempt == max_attempts - 1:
                raise
            time.sleep(min(15 * (2**attempt), 120))
    raise AssertionError("unreachable")


def _read_limited(response: object, limit: int) -> bytes:
    content_length = getattr(response, "headers", {}).get("Content-Length")
    if content_length:
        try:
            declared_length = int(content_length)
        except ValueError as exc:
            raise ArxivError("download returned an invalid Content-Length") from exc
        if declared_length > limit:
            raise ArxivError(
                f"download exceeds {limit // (1024 * 1024)} MiB limit"
            )
    body = response.read(limit + 1)
    if len(body) > limit:
        raise ArxivError(f"download exceeds {limit // (1024 * 1024)} MiB limit")
    return body


def _extract_tar(payload: bytes, destination: Path) -> SourceExtraction:
    file_count = 0
    extracted_bytes = 0
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:*") as archive:
        members = archive.getmembers()
        if len(members) > MAX_SOURCE_FILES:
            raise ArxivError("source archive contains too many entries")
        for member in members:
            relative = PurePosixPath(member.name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ArxivError("source archive contains an unsafe path")
            if member.issym() or member.islnk():
                continue
            target = destination.joinpath(*relative.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                continue
            extracted_bytes += member.size
            if extracted_bytes > MAX_EXTRACTED_BYTES:
                raise ArxivError("extracted source exceeds size limit")
            source = archive.extractfile(member)
            if source is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read())
            file_count += 1
    return SourceExtraction(True, file_count, f"{file_count} source files extracted")


def _required_text(
    parent: ET.Element,
    path: str,
    namespaces: dict[str, str],
) -> str:
    element = parent.find(path, namespaces)
    if element is None or not (element.text or "").strip():
        raise ArxivError(f"arXiv metadata is missing {path.rsplit(':', 1)[-1]}")
    return (element.text or "").strip()


def _collapse_whitespace(value: str) -> str:
    return " ".join(value.split())


def _arxiv_id_from_entry_url(value: str) -> str:
    path = urllib.parse.unquote(urllib.parse.urlparse(value).path)
    marker = "/abs/"
    if marker not in path:
        raise ArxivError(f"arXiv metadata returned an invalid identifier: {value}")
    return path.split(marker, 1)[1]


def _html_date(value: str) -> str:
    normalized = value.replace("/", "-")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized):
        raise ArxivError(f"paper page returned an invalid date: {value}")
    return f"{normalized}T00:00:00Z"


def _citation_author(value: str) -> str:
    if "," not in value:
        return _collapse_whitespace(value)
    family_name, given_names = value.split(",", 1)
    return _collapse_whitespace(f"{given_names} {family_name}")


class _ArxivHtmlMetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.values: dict[str, list[str]] = {}
        self.primary_category = ""
        self._in_primary_category = False

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = dict(attrs)
        if tag == "meta":
            name = attributes.get("name", "")
            content = attributes.get("content")
            if name.startswith("citation_") and content:
                self.values.setdefault(name, []).append(content)
        if tag == "span":
            classes = (attributes.get("class") or "").split()
            self._in_primary_category = "primary-subject" in classes

    def handle_endtag(self, tag: str) -> None:
        if tag == "span":
            self._in_primary_category = False

    def handle_data(self, data: str) -> None:
        if not self._in_primary_category:
            return
        match = re.search(r"\(([^()]+)\)", data)
        if match:
            self.primary_category = match.group(1)

    def first(self, name: str, required: bool = True) -> str:
        values = self.values.get(name)
        if values:
            return values[0]
        if required:
            raise ArxivError(f"paper page is missing {name}")
        return ""


def field_for_category(category: str) -> str:
    if category.startswith("cs."):
        return "Computer Science"
    if category.startswith("math."):
        return "Mathematics"
    if category.startswith("stat."):
        return "Statistics"
    if category.startswith("q-bio."):
        return "Quantitative Biology"
    if category.startswith("q-fin."):
        return "Quantitative Finance"
    if category.startswith("econ."):
        return "Economics"
    if category.startswith("eess."):
        return "Electrical Engineering"
    if category == "astro-ph" or category.startswith("astro-ph."):
        return "Physics and Astronomy"
    if category == "cond-mat" or category.startswith("cond-mat."):
        return "Physics and Astronomy"
    if category == "physics" or category.startswith("physics."):
        return "Physics and Astronomy"
    if category in {
        "gr-qc",
        "hep-ex",
        "hep-lat",
        "hep-ph",
        "hep-th",
        "nucl-ex",
        "nucl-th",
        "quant-ph",
    }:
        return "Physics and Astronomy"
    return "Other"
