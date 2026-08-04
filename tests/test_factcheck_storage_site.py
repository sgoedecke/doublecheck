from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from doublecheck.article import ArticleMetadata
from doublecheck.factcheck import FactCheckResult
from doublecheck.factcheck_site import build_factcheck_site
from doublecheck.factcheck_storage import (
    FactCheckRecord,
    load_factchecks,
    upsert_factcheck,
)


def make_factcheck_record() -> FactCheckRecord:
    article = ArticleMetadata(
        article_id="article-1",
        url="https://news.example/story",
        canonical_url="https://news.example/story",
        title="Unsafe <Article>",
        publisher="Example News",
        authors=("Ada Reporter",),
        published_at="2026-08-03",
        fetched_at="2026-08-03T01:00:00+00:00",
        content_hash="hash",
        text="Article text",
    )
    result = FactCheckResult(
        verdict="errors-found",
        summary="One date error.",
        findings=(
            {
                "id": "F1",
                "severity": "minor",
                "category": "date-error",
                "evidence_basis": "authoritative-primary-source",
                "article_quote": "The event happened on 2 January.",
                "location": "Paragraph 2",
                "correction": "The event happened on 3 January.",
                "analysis": "The official record contradicts the article.",
                "sources": (
                    {
                        "title": "Official record",
                        "publisher": "Example Agency",
                        "url": "https://agency.example.gov/record",
                        "quote": "The event happened on 3 January.",
                    },
                ),
                "confidence": "high",
            },
        ),
        sources_consulted=(
            {
                "title": "Official record",
                "publisher": "Example Agency",
                "url": "https://agency.example.gov/record",
            },
            {
                "title": "Archive",
                "publisher": "Example Archive",
                "url": "https://archive.example.org/register",
            },
        ),
        raw_response="{}",
    )
    return FactCheckRecord.from_result(
        article,
        result,
        model="gpt-5.6-sol",
        effort="high",
        checked_at=datetime(2026, 8, 3, 2, tzinfo=timezone.utc),
    )


class FactCheckStorageSiteTests(unittest.TestCase):
    def test_factcheck_csv_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            csv_path = Path(temporary) / "factchecks.csv"
            upsert_factcheck(csv_path, make_factcheck_record())
            records = load_factchecks(csv_path)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].error_tags, ("date-error",))

    def test_factcheck_site_renders_filters_and_escapes_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "docs" / "news" / "index.html"
            build_factcheck_site([make_factcheck_record()], output)
            rendered = output.read_text(encoding="utf-8")
            self.assertIn("Double-Check: News Fact Checks", rendered)
            self.assertIn('id="publisher-filter"', rendered)
            self.assertIn('id="error-filter"', rendered)
            self.assertIn('id="severity-filter"', rendered)
            self.assertIn("Unsafe &lt;Article&gt;", rendered)
            self.assertNotIn("Unsafe <Article>", rendered)
            self.assertIn("Official record", rendered)


if __name__ == "__main__":
    unittest.main()
