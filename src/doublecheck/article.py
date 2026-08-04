from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import gzip
import re
import socket
import ssl
import zlib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser

USER_AGENT = "doublecheck-factcheck/0.1 (+https://github.com/sgoedecke/doublecheck)"
MAX_ARTICLE_BYTES = 10 * 1024 * 1024
MAX_ARTICLE_TEXT_CHARS = 250_000
MIN_ARTICLE_TEXT_CHARS = 200


class ArticleError(RuntimeError):
    pass


@dataclass(frozen=True)
class ArticleMetadata:
    article_id: str
    url: str
    canonical_url: str
    title: str
    publisher: str
    authors: tuple[str, ...]
    published_at: str
    fetched_at: str
    content_hash: str
    text: str


def fetch_article(url: str, timeout: int = 45) -> ArticleMetadata:
    requested_url = normalize_article_url(url)
    final_url, headers, payload = safe_http_get(
        requested_url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
        },
        timeout=timeout,
        max_bytes=MAX_ARTICLE_BYTES,
    )
    content_type = headers.get_content_type()
    if content_type not in {"text/html", "application/xhtml+xml"}:
        raise ArticleError(
            f"article URL returned unsupported content type: {content_type}"
        )
    charset = headers.get_content_charset() or "utf-8"

    try:
        document = payload.decode(charset, errors="replace")
    except LookupError as exc:
        raise ArticleError(f"article returned an unknown charset: {charset}") from exc
    return parse_article_html(document, final_url)


def normalize_article_url(value: str) -> str:
    candidate = value.strip()
    parsed = urllib.parse.urlparse(candidate)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ArticleError("article URL must use http or https")
    if not parsed.hostname:
        raise ArticleError("article URL is missing a hostname")
    if parsed.username or parsed.password:
        raise ArticleError("article URL cannot contain credentials")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ArticleError("article URL has an invalid port") from exc
    _validate_public_host(parsed.hostname, port)
    normalized = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        fragment="",
    )
    return urllib.parse.urlunparse(normalized)


def safe_http_get(
    url: str,
    *,
    headers: dict[str, str],
    timeout: int,
    max_bytes: int,
    max_redirects: int = 5,
) -> tuple[str, object, bytes]:
    current_url = normalize_article_url(url)
    for redirect_count in range(max_redirects + 1):
        parsed = urllib.parse.urlparse(current_url)
        hostname = parsed.hostname or ""
        try:
            port = parsed.port
        except ValueError as exc:
            raise ArticleError("URL has an invalid port") from exc
        port = port or (443 if parsed.scheme == "https" else 80)
        addresses = _public_addresses(hostname, port)
        path = urllib.parse.urlunparse(
            ("", "", parsed.path or "/", parsed.params, parsed.query, "")
        )
        response = None
        last_error: OSError | None = None
        for address in addresses:
            connection = _connection_for(
                parsed.scheme,
                hostname,
                port,
                address,
                timeout,
            )
            try:
                connection.request("GET", path, headers=headers)
                response = connection.getresponse()
                payload = _read_limited(response, max_bytes)
                response_headers = response.headers
                payload = _decode_content_encoding(
                    payload,
                    response_headers.get("Content-Encoding", ""),
                    max_bytes,
                )
                status = response.status
            except (OSError, http.client.HTTPException) as exc:
                last_error = exc
                connection.close()
                continue
            connection.close()
            if status in {301, 302, 303, 307, 308}:
                location = response_headers.get("Location")
                if not location:
                    raise ArticleError("redirect response is missing Location")
                if redirect_count == max_redirects:
                    raise ArticleError("too many article redirects")
                current_url = normalize_article_url(
                    urllib.parse.urljoin(current_url, location)
                )
                break
            if status >= 400:
                raise ArticleError(f"could not fetch URL: HTTP {status}")
            return current_url, response_headers, payload
        else:
            raise ArticleError(f"could not connect to public URL: {last_error}")
    raise ArticleError("too many article redirects")


def parse_article_html(document: str, final_url: str) -> ArticleMetadata:
    parser = _ArticleHTMLParser()
    parser.feed(document)
    json_metadata = _json_ld_article_metadata(parser.json_ld_documents)

    title = (
        parser.first_meta("og:title", "twitter:title", "citation_title")
        or _string_value(json_metadata.get("headline"))
        or _string_value(json_metadata.get("name"))
        or parser.title
        or parser.heading
    )
    if not title:
        raise ArticleError("article page is missing a title")

    canonical_url = final_url
    if parser.canonical_url:
        candidate = urllib.parse.urljoin(final_url, parser.canonical_url)
        try:
            normalized_candidate = normalize_article_url(candidate)
        except ArticleError:
            pass
        else:
            if _equivalent_hosts(
                urllib.parse.urlparse(final_url).hostname or "",
                urllib.parse.urlparse(normalized_candidate).hostname or "",
            ):
                canonical_url = normalized_candidate

    publisher = (
        parser.first_meta("og:site_name", "application-name")
        or _publisher_name(json_metadata.get("publisher"))
        or (urllib.parse.urlparse(canonical_url).hostname or "").removeprefix("www.")
    )
    authors = _article_authors(parser, json_metadata)
    published_at = (
        parser.first_meta(
            "article:published_time",
            "date",
            "datepublished",
            "citation_publication_date",
            "citation_date",
        )
        or _string_value(json_metadata.get("datePublished"))
    )

    text = _clean_article_text(parser.article_parts or parser.body_parts)
    if len(text) < MIN_ARTICLE_TEXT_CHARS:
        raise ArticleError(
            "article page did not contain enough readable text; "
            "it may require JavaScript or authentication"
        )
    text = text[:MAX_ARTICLE_TEXT_CHARS]
    fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return ArticleMetadata(
        article_id=hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:20],
        url=final_url,
        canonical_url=canonical_url,
        title=_collapse_whitespace(title),
        publisher=_collapse_whitespace(publisher),
        authors=authors,
        published_at=_collapse_whitespace(published_at),
        fetched_at=fetched_at,
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        text=text,
    )


def _validate_public_host(hostname: str, port: int | None) -> None:
    _public_addresses(hostname, port or 443)


def _public_addresses(hostname: str, port: int) -> tuple[str, ...]:
    lowered = hostname.rstrip(".").lower()
    if lowered == "localhost" or lowered.endswith(".local"):
        raise ArticleError("article URL cannot target a local hostname")
    try:
        addresses = socket.getaddrinfo(
            hostname,
            port or 443,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise ArticleError(f"could not resolve article hostname: {exc}") from exc
    if not addresses:
        raise ArticleError("article hostname did not resolve")
    public_addresses: list[str] = []
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0].split("%", 1)[0])
        if not ip.is_global:
            raise ArticleError("article URL cannot target a private network")
        normalized = str(ip)
        if normalized not in public_addresses:
            public_addresses.append(normalized)
    return tuple(public_addresses)


def _read_limited(response: object, limit: int) -> bytes:
    content_length = getattr(response, "headers", {}).get("Content-Length")
    if content_length:
        try:
            declared_length = int(content_length)
        except ValueError as exc:
            raise ArticleError("article returned an invalid Content-Length") from exc
        if declared_length > limit:
            raise ArticleError("article exceeds the download size limit")
    payload = response.read(limit + 1)
    if len(payload) > limit:
        raise ArticleError("article exceeds the download size limit")
    return payload


def _decode_content_encoding(
    payload: bytes,
    encoding: str,
    limit: int,
) -> bytes:
    normalized = encoding.lower().strip()
    try:
        if normalized == "gzip":
            payload = gzip.decompress(payload)
        elif normalized == "deflate":
            try:
                payload = zlib.decompress(payload)
            except zlib.error:
                payload = zlib.decompress(payload, -zlib.MAX_WBITS)
        elif normalized not in {"", "identity"}:
            raise ArticleError(f"unsupported Content-Encoding: {encoding}")
    except (gzip.BadGzipFile, EOFError, zlib.error) as exc:
        raise ArticleError(f"invalid compressed HTTP response: {exc}") from exc
    if len(payload) > limit:
        raise ArticleError("decompressed response exceeds the size limit")
    return payload


def _clean_article_text(parts: list[str]) -> str:
    lines: list[str] = []
    previous = ""
    for part in parts:
        line = _collapse_whitespace(part)
        if not line or line == previous:
            continue
        lines.append(line)
        previous = line
    return "\n".join(lines)


def _article_authors(
    parser: "_ArticleHTMLParser",
    metadata: dict[str, object],
) -> tuple[str, ...]:
    values = parser.meta_values("author", "article:author", "citation_author")
    values.extend(_author_values(metadata.get("author")))
    authors: list[str] = []
    for value in values:
        normalized = _collapse_whitespace(value)
        if normalized and normalized not in authors:
            authors.append(normalized)
    return tuple(authors)


def _author_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        name = value.get("name")
        return [name] if isinstance(name, str) else []
    if isinstance(value, list):
        authors: list[str] = []
        for item in value:
            authors.extend(_author_values(item))
        return authors
    return []


def _publisher_name(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        name = value.get("name")
        return name if isinstance(name, str) else ""
    return ""


def _json_ld_article_metadata(documents: list[str]) -> dict[str, object]:
    for document in documents:
        try:
            value = json.loads(document)
        except json.JSONDecodeError:
            continue
        for item in _json_objects(value):
            item_type = item.get("@type")
            types = {item_type} if isinstance(item_type, str) else set(item_type or ())
            if types.intersection(
                {
                    "Article",
                    "NewsArticle",
                    "ReportageNewsArticle",
                    "AnalysisNewsArticle",
                    "BlogPosting",
                }
            ):
                return item
    return {}


def _json_objects(value: object) -> list[dict[str, object]]:
    if isinstance(value, dict):
        objects = [value]
        graph = value.get("@graph")
        if isinstance(graph, list):
            objects.extend(item for item in graph if isinstance(item, dict))
        return objects
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _string_value(value: object) -> str:
    return value if isinstance(value, str) else ""


def _collapse_whitespace(value: str) -> str:
    return " ".join(value.split())


def _equivalent_hosts(first: str, second: str) -> bool:
    return first.lower().removeprefix("www.") == second.lower().removeprefix("www.")


def _connection_for(
    scheme: str,
    hostname: str,
    port: int,
    address: str,
    timeout: int,
) -> http.client.HTTPConnection:
    if scheme == "https":
        return _PinnedHTTPSConnection(
            hostname,
            port=port,
            timeout=timeout,
            connect_address=address,
        )
    return _PinnedHTTPConnection(
        hostname,
        port=port,
        timeout=timeout,
        connect_address=address,
    )


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(
        self,
        host: str,
        *,
        port: int,
        timeout: int,
        connect_address: str,
    ) -> None:
        super().__init__(host, port=port, timeout=timeout)
        self._connect_address = connect_address

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._connect_address, self.port),
            self.timeout,
        )


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(
        self,
        host: str,
        *,
        port: int,
        timeout: int,
        connect_address: str,
    ) -> None:
        super().__init__(
            host,
            port=port,
            timeout=timeout,
            context=ssl.create_default_context(),
        )
        self._connect_address = connect_address

    def connect(self) -> None:
        raw_socket = socket.create_connection(
            (self._connect_address, self.port),
            self.timeout,
        )
        self.sock = self._context.wrap_socket(
            raw_socket,
            server_hostname=self.host,
        )


class _ArticleHTMLParser(HTMLParser):
    BLOCK_TAGS = {
        "address",
        "article",
        "blockquote",
        "br",
        "div",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "p",
        "section",
        "td",
        "th",
        "tr",
    }
    IGNORED_TAGS = {
        "aside",
        "footer",
        "form",
        "header",
        "nav",
        "noscript",
        "script",
        "style",
        "svg",
    }

    def __init__(self) -> None:
        super().__init__()
        self.metadata: dict[str, list[str]] = {}
        self.canonical_url = ""
        self.title = ""
        self.heading = ""
        self.body_parts: list[str] = []
        self.article_parts: list[str] = []
        self.json_ld_documents: list[str] = []
        self._body_depth = 0
        self._article_depth = 0
        self._ignored_depth = 0
        self._title_depth = 0
        self._heading_depth = 0
        self._json_ld_depth = 0
        self._json_ld_parts: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = {key.lower(): value or "" for key, value in attrs}
        if tag == "meta":
            raw_key = (
                attributes.get("property")
                or attributes.get("name")
                or attributes.get("itemprop")
            )
            key = raw_key.lower() if raw_key else ""
            content = attributes.get("content", "")
            if key and content:
                self.metadata.setdefault(key, []).append(content)
        elif tag == "link" and "canonical" in attributes.get("rel", "").split():
            self.canonical_url = attributes.get("href", "")

        if tag == "body":
            self._body_depth += 1
        if tag == "article":
            self._article_depth += 1
        if tag == "title":
            self._title_depth += 1
        if tag == "h1" and not self.heading:
            self._heading_depth += 1
        if tag in self.IGNORED_TAGS:
            self._ignored_depth += 1
        if (
            tag == "script"
            and attributes.get("type", "").lower() == "application/ld+json"
        ):
            self._json_ld_depth += 1
            self._json_ld_parts = []
        if tag in self.BLOCK_TAGS:
            self._append_part("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.BLOCK_TAGS:
            self._append_part("\n")
        if tag == "script" and self._json_ld_depth:
            self._json_ld_depth -= 1
            document = "".join(self._json_ld_parts).strip()
            if document:
                self.json_ld_documents.append(document)
            self._json_ld_parts = []
        if tag in self.IGNORED_TAGS and self._ignored_depth:
            self._ignored_depth -= 1
        if tag == "title" and self._title_depth:
            self._title_depth -= 1
        if tag == "h1" and self._heading_depth:
            self._heading_depth -= 1
        if tag == "article" and self._article_depth:
            self._article_depth -= 1
        if tag == "body" and self._body_depth:
            self._body_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._json_ld_depth:
            self._json_ld_parts.append(data)
            return
        if self._title_depth:
            self.title += data
        if self._heading_depth:
            self.heading += data
        if self._ignored_depth:
            return
        self._append_part(data)

    def _append_part(self, value: str) -> None:
        if self._body_depth:
            self.body_parts.append(value)
        if self._article_depth:
            self.article_parts.append(value)

    def first_meta(self, *keys: str) -> str:
        for key in keys:
            values = self.metadata.get(key.lower())
            if values:
                return values[0]
        return ""

    def meta_values(self, *keys: str) -> list[str]:
        values: list[str] = []
        for key in keys:
            values.extend(self.metadata.get(key.lower(), ()))
        return values
