from __future__ import annotations

import csv
import fcntl
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from doublecheck.article import ArticleMetadata
from doublecheck.factcheck import (
    FACTCHECK_CATEGORIES,
    FACTCHECK_SEVERITIES,
    FACTCHECK_VERDICTS,
    FactCheckResult,
    validate_factcheck_finding,
    validate_source,
)
from doublecheck.review import EFFORTS, ReviewError
from doublecheck.storage import StorageError

FACTCHECK_FIELDNAMES = (
    "article_id",
    "url",
    "canonical_url",
    "title",
    "publisher",
    "authors",
    "published_at",
    "fetched_at",
    "content_hash",
    "checked_at",
    "model",
    "effort",
    "verdict",
    "summary",
    "error_tags",
    "severities",
    "findings",
    "sources_consulted",
)


@dataclass(frozen=True)
class FactCheckRecord:
    article_id: str
    url: str
    canonical_url: str
    title: str
    publisher: str
    authors: tuple[str, ...]
    published_at: str
    fetched_at: str
    content_hash: str
    checked_at: str
    model: str
    effort: str
    verdict: str
    summary: str
    error_tags: tuple[str, ...]
    severities: tuple[str, ...]
    findings: tuple[dict[str, object], ...]
    sources_consulted: tuple[dict[str, str], ...]

    @classmethod
    def from_result(
        cls,
        article: ArticleMetadata,
        result: FactCheckResult,
        model: str,
        effort: str,
        checked_at: datetime | None = None,
    ) -> "FactCheckRecord":
        timestamp = checked_at or datetime.now(timezone.utc)
        return cls(
            article_id=article.article_id,
            url=article.url,
            canonical_url=article.canonical_url,
            title=article.title,
            publisher=article.publisher,
            authors=article.authors,
            published_at=article.published_at,
            fetched_at=article.fetched_at,
            content_hash=article.content_hash,
            checked_at=timestamp.replace(microsecond=0).isoformat(),
            model=model,
            effort=effort,
            verdict=result.verdict,
            summary=result.summary,
            error_tags=result.error_tags,
            severities=result.severities,
            findings=result.findings,
            sources_consulted=result.sources_consulted,
        )

    @classmethod
    def from_row(cls, row: dict[str, str]) -> "FactCheckRecord":
        missing = [field for field in FACTCHECK_FIELDNAMES if field not in row]
        if missing:
            raise StorageError(
                f"fact-check CSV is missing fields: {', '.join(missing)}"
            )
        try:
            authors = _string_tuple(json.loads(row["authors"]), "authors")
            error_tags = _string_tuple(
                json.loads(row["error_tags"]),
                "error_tags",
            )
            severities = _string_tuple(
                json.loads(row["severities"]),
                "severities",
            )
            raw_findings = json.loads(row["findings"])
            if not isinstance(raw_findings, list):
                raise StorageError("findings must be a JSON array")
            findings = tuple(
                validate_factcheck_finding(item, index)
                for index, item in enumerate(raw_findings, start=1)
            )
            raw_sources = json.loads(row["sources_consulted"])
            if not isinstance(raw_sources, list):
                raise StorageError("sources_consulted must be a JSON array")
            sources = tuple(
                validate_source(
                    item,
                    f"source {index}",
                    require_quote=False,
                )
                for index, item in enumerate(raw_sources, start=1)
            )
        except (json.JSONDecodeError, ReviewError) as exc:
            raise StorageError(
                f"invalid fact-check row for {row.get('article_id', '?')}"
            ) from exc

        if row["verdict"] not in FACTCHECK_VERDICTS:
            raise StorageError(f"invalid fact-check verdict: {row['verdict']}")
        if row["effort"] not in EFFORTS:
            raise StorageError(f"invalid effort: {row['effort']}")
        if set(error_tags) - FACTCHECK_CATEGORIES:
            raise StorageError("invalid fact-check error tags")
        if set(severities) - FACTCHECK_SEVERITIES:
            raise StorageError("invalid fact-check severities")
        expected_tags = tuple(
            sorted({str(finding["category"]) for finding in findings})
        )
        expected_severities = tuple(
            sorted({str(finding["severity"]) for finding in findings})
        )
        if error_tags != expected_tags or severities != expected_severities:
            raise StorageError("fact-check summary tags do not match findings")
        if row["verdict"] == "errors-found" and not findings:
            raise StorageError("errors-found verdict requires findings")
        if row["verdict"] == "no-obvious-errors-found" and findings:
            raise StorageError(
                "no-obvious-errors-found verdict cannot include findings"
            )
        return cls(
            article_id=row["article_id"],
            url=row["url"],
            canonical_url=row["canonical_url"],
            title=row["title"],
            publisher=row["publisher"],
            authors=authors,
            published_at=row["published_at"],
            fetched_at=row["fetched_at"],
            content_hash=row["content_hash"],
            checked_at=row["checked_at"],
            model=row["model"],
            effort=row["effort"],
            verdict=row["verdict"],
            summary=row["summary"],
            error_tags=error_tags,
            severities=severities,
            findings=findings,
            sources_consulted=sources,
        )

    def to_row(self) -> dict[str, str]:
        return {
            "article_id": self.article_id,
            "url": self.url,
            "canonical_url": self.canonical_url,
            "title": self.title,
            "publisher": self.publisher,
            "authors": json.dumps(self.authors),
            "published_at": self.published_at,
            "fetched_at": self.fetched_at,
            "content_hash": self.content_hash,
            "checked_at": self.checked_at,
            "model": self.model,
            "effort": self.effort,
            "verdict": self.verdict,
            "summary": self.summary,
            "error_tags": json.dumps(self.error_tags),
            "severities": json.dumps(self.severities),
            "findings": json.dumps(self.findings),
            "sources_consulted": json.dumps(self.sources_consulted),
        }


def load_factchecks(path: Path) -> list[FactCheckRecord]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(FACTCHECK_FIELDNAMES):
            raise StorageError(
                "CSV header does not match the expected fact-check schema"
            )
        return [FactCheckRecord.from_row(row) for row in reader]


def upsert_factcheck(path: Path, record: FactCheckRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.parent / f".{path.name}.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            records = load_factchecks(path)
            by_id = {item.article_id: item for item in records}
            by_id[record.article_id] = record
            ordered = sorted(
                by_id.values(),
                key=lambda item: (item.checked_at, item.article_id),
                reverse=True,
            )
            write_factchecks(path, ordered)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def write_factchecks(
    path: Path,
    records: Iterable[FactCheckRecord],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    try:
        with os.fdopen(descriptor, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=FACTCHECK_FIELDNAMES,
                lineterminator="\n",
            )
            writer.writeheader()
            for record in records:
                writer.writerow(record.to_row())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _string_tuple(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise StorageError(f"{field} must be a JSON array of strings")
    return tuple(value)
