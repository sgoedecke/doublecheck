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

from doublecheck.arxiv import PaperMetadata, field_for_category
from doublecheck.review import (
    CATEGORIES,
    EFFORTS,
    VERDICTS,
    ReviewError,
    ReviewResult,
    validate_finding,
)

FIELDNAMES = (
    "arxiv_id",
    "title",
    "authors",
    "field",
    "version",
    "arxiv_url",
    "pdf_url",
    "published",
    "updated",
    "reviewed_at",
    "model",
    "effort",
    "verdict",
    "summary",
    "problem_tags",
    "findings",
    "limitations",
)


class StorageError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReviewRecord:
    arxiv_id: str
    title: str
    authors: tuple[str, ...]
    field: str
    version: int
    arxiv_url: str
    pdf_url: str
    published: str
    updated: str
    reviewed_at: str
    model: str
    effort: str
    verdict: str
    summary: str
    problem_tags: tuple[str, ...]
    findings: tuple[dict[str, str], ...]
    limitations: tuple[str, ...]

    @classmethod
    def from_review(
        cls,
        metadata: PaperMetadata,
        review: ReviewResult,
        model: str,
        effort: str,
        reviewed_at: datetime | None = None,
    ) -> "ReviewRecord":
        timestamp = reviewed_at or datetime.now(timezone.utc)
        return cls(
            arxiv_id=metadata.arxiv_id,
            title=metadata.title,
            authors=metadata.authors,
            field=field_for_category(metadata.primary_category),
            version=metadata.version,
            arxiv_url=metadata.abstract_url,
            pdf_url=metadata.pdf_url,
            published=metadata.published,
            updated=metadata.updated,
            reviewed_at=timestamp.replace(microsecond=0).isoformat(),
            model=model,
            effort=effort,
            verdict=review.verdict,
            summary=review.summary,
            problem_tags=review.problem_tags,
            findings=review.findings,
            limitations=review.limitations,
        )

    @classmethod
    def from_row(cls, row: dict[str, str]) -> "ReviewRecord":
        missing = [
            field
            for field in FIELDNAMES
            if field != "field" and field not in row
        ]
        if missing:
            raise StorageError(f"CSV is missing fields: {', '.join(missing)}")
        try:
            authors = _string_tuple(json.loads(row["authors"]), "authors")
            problem_tags = _string_tuple(
                json.loads(row["problem_tags"]),
                "problem_tags",
            )
            limitations = _string_tuple(
                json.loads(row["limitations"]),
                "limitations",
            )
            raw_findings = json.loads(row["findings"])
            if not isinstance(raw_findings, list):
                raise StorageError("findings must be a JSON array")
            findings = tuple(
                validate_finding(item, index)
                for index, item in enumerate(raw_findings, start=1)
            )
            version = int(row["version"])
        except (json.JSONDecodeError, ReviewError, ValueError) as exc:
            raise StorageError(
                f"invalid CSV row for {row.get('arxiv_id', '?')}"
            ) from exc
        if version < 1:
            raise StorageError("version must be greater than zero")
        field = row.get("field") or "Other"
        if not field.strip():
            raise StorageError("field must be a non-empty string")
        if row["verdict"] not in VERDICTS:
            raise StorageError(f"invalid verdict: {row['verdict']}")
        if row["effort"] not in EFFORTS:
            raise StorageError(f"invalid effort: {row['effort']}")
        invalid_tags = sorted(set(problem_tags) - CATEGORIES)
        if invalid_tags:
            raise StorageError(
                f"invalid problem tags: {', '.join(invalid_tags)}"
            )
        expected_tags = tuple(
            sorted({finding["category"] for finding in findings})
        )
        if problem_tags != expected_tags:
            raise StorageError("problem_tags do not match finding categories")
        if row["verdict"] == "errors-found" and not findings:
            raise StorageError("errors-found verdict requires findings")
        if row["verdict"] == "no-glaring-errors-found" and findings:
            raise StorageError(
                "no-glaring-errors-found verdict cannot include findings"
            )
        return cls(
            arxiv_id=row["arxiv_id"],
            title=row["title"],
            authors=authors,
            field=field,
            version=version,
            arxiv_url=row["arxiv_url"],
            pdf_url=row["pdf_url"],
            published=row["published"],
            updated=row["updated"],
            reviewed_at=row["reviewed_at"],
            model=row["model"],
            effort=row["effort"],
            verdict=row["verdict"],
            summary=row["summary"],
            problem_tags=problem_tags,
            findings=findings,
            limitations=limitations,
        )

    def to_row(self) -> dict[str, str]:
        return {
            "arxiv_id": self.arxiv_id,
            "title": self.title,
            "authors": json.dumps(self.authors),
            "field": self.field,
            "version": str(self.version),
            "arxiv_url": self.arxiv_url,
            "pdf_url": self.pdf_url,
            "published": self.published,
            "updated": self.updated,
            "reviewed_at": self.reviewed_at,
            "model": self.model,
            "effort": self.effort,
            "verdict": self.verdict,
            "summary": self.summary,
            "problem_tags": json.dumps(self.problem_tags),
            "findings": json.dumps(self.findings),
            "limitations": json.dumps(self.limitations),
        }


def load_records(path: Path) -> list[ReviewRecord]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        legacy_fieldnames = [
            field for field in FIELDNAMES if field != "field"
        ]
        if reader.fieldnames not in (list(FIELDNAMES), legacy_fieldnames):
            raise StorageError(
                "CSV header does not match the expected review schema"
            )
        return [ReviewRecord.from_row(row) for row in reader]


def upsert_record(path: Path, record: ReviewRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.parent / f".{path.name}.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            records = load_records(path)
            by_id = {item.arxiv_id: item for item in records}
            by_id[record.arxiv_id] = record
            ordered = sorted(
                by_id.values(),
                key=lambda item: (item.reviewed_at, item.arxiv_id),
                reverse=True,
            )
            write_records(path, ordered)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def write_records(path: Path, records: Iterable[ReviewRecord]) -> None:
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
                fieldnames=FIELDNAMES,
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
