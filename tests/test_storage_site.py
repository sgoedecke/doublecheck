from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from doublecheck.arxiv import PaperMetadata
from doublecheck.review import ReviewResult
from doublecheck.site import build_site
from doublecheck.storage import (
    ReviewRecord,
    StorageError,
    load_records,
    upsert_record,
)


def make_record(
    *,
    arxiv_id: str = "2501.12345v1",
    summary: str = "A problem was found.",
    reviewed_at: datetime | None = None,
) -> ReviewRecord:
    metadata = PaperMetadata(
        arxiv_id=arxiv_id,
        title="Unsafe <Title>",
        authors=("Ada Lovelace", "Emmy Noether"),
        summary="Abstract",
        published="2025-01-01T00:00:00Z",
        updated="2025-01-02T00:00:00Z",
        primary_category="math.OC",
    )
    review = ReviewResult(
        verdict="errors-found",
        summary=summary,
        findings=(
            {
                "id": "F1",
                "severity": "major",
                "category": "logical-error",
                "evidence_type": "counterexample",
                "claim": "Missing case",
                "location": "Theorem 2",
                "analysis": "The proof omits a branch.",
                "evidence": "The branch is permitted by Definition 1.",
                "confidence": "high",
            },
        ),
        limitations=("Not machine-checked.",),
        raw_response="{}",
    )
    return ReviewRecord.from_review(
        metadata,
        review,
        model="gpt-5.6-sol",
        effort="high",
        reviewed_at=reviewed_at or datetime(2025, 1, 3, tzinfo=timezone.utc),
    )


class StorageAndSiteTests(unittest.TestCase):
    def test_upsert_replaces_same_arxiv_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            csv_path = Path(temporary) / "reviews.csv"
            upsert_record(csv_path, make_record(summary="First"))
            upsert_record(csv_path, make_record(summary="Replacement"))
            records = load_records(csv_path)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].summary, "Replacement")

    def test_upsert_keeps_distinct_versions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            csv_path = Path(temporary) / "reviews.csv"
            upsert_record(csv_path, make_record(arxiv_id="2501.12345v1"))
            upsert_record(csv_path, make_record(arxiv_id="2501.12345v2"))
            self.assertEqual(len(load_records(csv_path)), 2)

    def test_upsert_creates_cross_process_lock_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            csv_path = Path(temporary) / "reviews.csv"
            upsert_record(csv_path, make_record())
            self.assertTrue((csv_path.parent / ".reviews.csv.lock").exists())

    def test_site_renders_tags_and_escapes_paper_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "docs" / "index.html"
            build_site([make_record()], output)
            rendered = output.read_text(encoding="utf-8")
            self.assertIn("logical-error", rendered)
            self.assertIn("Glaring errors found", rendered)
            self.assertIn("counterexample", rendered)
            self.assertIn("Unsafe &lt;Title&gt;", rendered)
            self.assertNotIn("Unsafe <Title>", rendered)
            self.assertIn('<details class="paper-details">', rendered)
            self.assertIn("<summary>Paper details</summary>", rendered)
            self.assertIn('<div class="findings-body">', rendered)
            self.assertIn('<table>', rendered)
            self.assertIn('id="field-filter"', rendered)
            self.assertIn('id="error-filter"', rendered)
            self.assertIn('id="severity-filter"', rendered)
            self.assertIn('data-field="Mathematics"', rendered)
            self.assertIn(
                ".findings-body, .details-body { border-left:",
                rendered,
            )
            self.assertGreater(
                rendered.index("<strong>Authors:</strong>"),
                rendered.index("<summary>Paper details</summary>"),
            )
            self.assertNotIn("Review limitations", rendered)
            self.assertNotIn("Not machine-checked.", rendered)
            self.assertIn(
                "LLM-assisted audits for open scientific papers.",
                rendered,
            )
            self.assertIn(
                'href="https://github.com/sgoedecke/doublecheck"',
                rendered,
            )
            self.assertNotIn("<h2>Reviewed papers</h2>", rendered)
            self.assertNotIn(
                "scientific papers.</p>\n    <p><a",
                rendered,
            )
            self.assertTrue((output.parent / ".nojekyll").exists())

    def test_csv_rejects_invalid_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            csv_path = Path(temporary) / "reviews.csv"
            upsert_record(csv_path, make_record())
            content = csv_path.read_text(encoding="utf-8")
            csv_path.write_text(
                content.replace("errors-found", "invented-verdict", 1),
                encoding="utf-8",
            )
            with self.assertRaises(StorageError):
                load_records(csv_path)

    def test_site_can_filter_reviews_with_no_findings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clean_record = replace(
                make_record(arxiv_id="2501.12346v1"),
                verdict="no-glaring-errors-found",
                summary="No glaring errors found.",
                problem_tags=(),
                findings=(),
            )
            output = Path(temporary) / "docs" / "index.html"
            build_site([make_record(), clean_record], output)
            rendered = output.read_text(encoding="utf-8")
            self.assertIn('<option value="none">None</option>', rendered)
            self.assertIn('data-severities="none"', rendered)


if __name__ == "__main__":
    unittest.main()
