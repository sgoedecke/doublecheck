import socket
import unittest
from unittest.mock import patch

from doublecheck.article import (
    ArticleError,
    _decode_content_encoding,
    normalize_article_url,
    parse_article_html,
)


class ArticleTests(unittest.TestCase):
    @patch("doublecheck.article.socket.getaddrinfo")
    def test_parses_article_metadata_and_text(self, getaddrinfo: object) -> None:
        getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ]
        document = """
        <html>
        <head>
          <title>Fallback title</title>
          <meta property="og:title" content="Example News">
          <meta property="og:site_name" content="Example Publisher">
          <meta property="article:published_time" content="2026-08-03">
          <meta name="author" content="Ada Reporter">
          <link rel="canonical" href="https://www.example.com/news/story">
        </head>
        <body>
          <nav>Navigation text</nav>
          <article>
            <p>This is the first paragraph of a sufficiently long article.</p>
            <p>This is the second paragraph with additional factual material
            for the checker to examine carefully and independently.</p>
            <p>This is the third paragraph, included so the extracted article
            comfortably exceeds the minimum readable-text threshold.</p>
          </article>
        </body>
        </html>
        """
        article = parse_article_html(
            document,
            "https://example.com/news/story?source=home",
        )
        self.assertEqual(article.title, "Example News")
        self.assertEqual(article.publisher, "Example Publisher")
        self.assertEqual(article.authors, ("Ada Reporter",))
        self.assertEqual(
            article.canonical_url,
            "https://www.example.com/news/story",
        )
        self.assertIn("first paragraph", article.text)
        self.assertNotIn("Navigation text", article.text)

    @patch("doublecheck.article.socket.getaddrinfo")
    def test_rejects_private_network_urls(self, getaddrinfo: object) -> None:
        getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))
        ]
        with self.assertRaisesRegex(ArticleError, "private network"):
            normalize_article_url("http://internal.example/article")

    def test_rejects_non_http_urls(self) -> None:
        with self.assertRaisesRegex(ArticleError, "http or https"):
            normalize_article_url("file:///etc/passwd")

    @patch("doublecheck.article.socket.getaddrinfo")
    def test_uses_h1_when_page_has_no_metadata_title(
        self,
        getaddrinfo: object,
    ) -> None:
        getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ]
        article = parse_article_html(
            "<html><body><h1>Headline</h1><article>"
            + "<p>Readable article content. "
            + ("Additional factual material. " * 20)
            + "</p></article></body></html>",
            "https://example.com/story",
        )
        self.assertEqual(article.title, "Headline")

    def test_decodes_gzip_content(self) -> None:
        import gzip

        payload = gzip.compress(b"readable response")
        self.assertEqual(
            _decode_content_encoding(payload, "gzip", 100),
            b"readable response",
        )


if __name__ == "__main__":
    unittest.main()
