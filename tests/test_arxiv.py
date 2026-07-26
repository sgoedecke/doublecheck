import unittest
import urllib.error
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from doublecheck.arxiv import (
    ArxivError,
    PaperMetadata,
    _arxiv_id_from_entry_url,
    _parse_html_metadata,
    _read_limited,
    _urlopen,
    download_and_extract_source,
    field_for_category,
    normalize_arxiv_id,
)


class NormalizeArxivIdTests(unittest.TestCase):
    def test_accepts_modern_identifier(self) -> None:
        self.assertEqual(normalize_arxiv_id("2501.12345v2"), "2501.12345v2")

    def test_accepts_legacy_identifier(self) -> None:
        self.assertEqual(
            normalize_arxiv_id("math.GT/0309136v1"),
            "math.GT/0309136v1",
        )

    def test_preserves_legacy_category_in_entry_url(self) -> None:
        self.assertEqual(
            _arxiv_id_from_entry_url(
                "http://arxiv.org/abs/hep-th/9711200v3"
            ),
            "hep-th/9711200v3",
        )

    def test_extracts_identifier_from_pdf_url(self) -> None:
        self.assertEqual(
            normalize_arxiv_id("https://arxiv.org/pdf/2501.12345v2.pdf"),
            "2501.12345v2",
        )

    def test_rejects_non_arxiv_url(self) -> None:
        with self.assertRaises(ArxivError):
            normalize_arxiv_id("https://example.com/abs/2501.12345")

    def test_rejects_invalid_content_length(self) -> None:
        class Response:
            headers = {"Content-Length": "not-a-number"}

            def read(self, limit: int) -> bytes:
                return b""

        with self.assertRaises(ArxivError):
            _read_limited(Response(), 100)

    def test_source_http_failure_falls_back_to_pdf_only(self) -> None:
        metadata = PaperMetadata(
            arxiv_id="2501.12345v1",
            title="Paper",
            authors=("Author",),
            summary="Abstract",
            published="2025-01-01T00:00:00Z",
            updated="2025-01-01T00:00:00Z",
        )
        for status in (301, 429):
            with self.subTest(status=status):
                error = urllib.error.HTTPError(
                    metadata.abstract_url,
                    status,
                    "source unavailable",
                    hdrs=None,
                    fp=None,
                )
                with TemporaryDirectory() as temporary, patch(
                    "doublecheck.arxiv._urlopen",
                    side_effect=error,
                ) as urlopen:
                    result = download_and_extract_source(
                        metadata,
                        Path(temporary) / "source",
                    )
                self.assertFalse(result.available)
                self.assertIn(f"HTTP {status}", result.note)
                self.assertEqual(
                    urlopen.call_args.kwargs["max_attempts"],
                    1,
                )

    def test_maps_arxiv_categories_to_broad_fields(self) -> None:
        self.assertEqual(field_for_category("cs.CL"), "Computer Science")
        self.assertEqual(field_for_category("math.AG"), "Mathematics")
        self.assertEqual(
            field_for_category("astro-ph.CO"),
            "Physics and Astronomy",
        )

    def test_parses_arxiv_html_metadata_fallback(self) -> None:
        document = """
        <meta name="citation_title" content="A Paper">
        <meta name="citation_author" content="Noether, Emmy">
        <meta name="citation_date" content="2025/01/02">
        <meta name="citation_online_date" content="2025/02/03">
        <meta name="citation_abstract" content="An abstract.">
        <span class="primary-subject">Optimization (math.OC)</span>
        <a href="/abs/2501.12345v3">v3</a>
        """
        metadata = _parse_html_metadata(document, "2501.12345")
        self.assertEqual(metadata.arxiv_id, "2501.12345v3")
        self.assertEqual(metadata.authors, ("Emmy Noether",))
        self.assertEqual(metadata.primary_category, "math.OC")
        self.assertEqual(metadata.updated, "2025-02-03T00:00:00Z")

    @patch("doublecheck.arxiv.MIN_REQUEST_INTERVAL_SECONDS", 0)
    @patch("doublecheck.arxiv.time.sleep")
    @patch("doublecheck.arxiv.urllib.request.urlopen")
    def test_retries_rate_limited_arxiv_requests(
        self,
        urlopen: object,
        sleep: object,
    ) -> None:
        error = urllib.error.HTTPError(
            "https://export.arxiv.org/api/query",
            429,
            "rate limited",
            hdrs=None,
            fp=None,
        )
        response = object()
        urlopen.side_effect = [error, response]
        request = urllib.request.Request("https://export.arxiv.org/api/query")
        self.assertIs(_urlopen(request, timeout=30), response)
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(15)


if __name__ == "__main__":
    unittest.main()
