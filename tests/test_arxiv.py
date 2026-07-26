import unittest
import urllib.error
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from doublecheck.arxiv import (
    ArxivError,
    PaperMetadata,
    _read_limited,
    download_and_extract_source,
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

    def test_source_rate_limit_falls_back_to_pdf_only(self) -> None:
        metadata = PaperMetadata(
            arxiv_id="2501.12345v1",
            title="Paper",
            authors=("Author",),
            summary="Abstract",
            published="2025-01-01T00:00:00Z",
            updated="2025-01-01T00:00:00Z",
        )
        error = urllib.error.HTTPError(
            metadata.abstract_url,
            429,
            "rate limited",
            hdrs=None,
            fp=None,
        )
        with TemporaryDirectory() as temporary, patch(
            "doublecheck.arxiv._urlopen",
            side_effect=error,
        ):
            result = download_and_extract_source(
                metadata,
                Path(temporary) / "source",
            )
        self.assertFalse(result.available)
        self.assertIn("HTTP 429", result.note)


if __name__ == "__main__":
    unittest.main()
