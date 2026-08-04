import json
import unittest
from pathlib import Path

from doublecheck.article import ArticleMetadata
from doublecheck.factcheck import (
    ReviewError,
    build_factcheck_prompt,
    parse_factcheck_response,
)
from doublecheck.review import build_copilot_command


def factcheck_payload() -> dict[str, object]:
    return {
        "verdict": "errors-found",
        "summary": "One discrete date error was found.",
        "findings": [
            {
                "id": "F1",
                "severity": "minor",
                "category": "date-error",
                "evidence_basis": "authoritative-primary-source",
                "article_quote": "The event occurred on 2 January.",
                "location": "Paragraph 3",
                "correction": "The event occurred on 3 January.",
                "analysis": "The official record gives a different date.",
                "sources": [
                    {
                        "title": "Official record",
                        "publisher": "Example Agency",
                        "url": "https://agency.example.gov/record",
                        "quote": "The event occurred on 3 January.",
                    }
                ],
                "confidence": "high",
            }
        ],
        "sources_consulted": [
            {
                "title": "Official record",
                "publisher": "Example Agency",
                "url": "https://agency.example.gov/record",
            },
            {
                "title": "Contemporaneous register",
                "publisher": "Example Archive",
                "url": "https://archive.example.org/register",
            },
        ],
    }


class FactCheckTests(unittest.TestCase):
    def test_parses_strict_factcheck_response(self) -> None:
        result = parse_factcheck_response(json.dumps(factcheck_payload()))
        self.assertEqual(result.verdict, "errors-found")
        self.assertEqual(result.error_tags, ("date-error",))
        self.assertEqual(result.severities, ("minor",))

    def test_rejects_source_that_was_not_fetched(self) -> None:
        with self.assertRaisesRegex(ReviewError, "were not fetched"):
            parse_factcheck_response(
                json.dumps(factcheck_payload()),
                fetched_urls={"https://agency.example.gov/record"},
            )

    def test_requires_two_domains_for_completed_check(self) -> None:
        payload = factcheck_payload()
        payload["sources_consulted"] = payload["sources_consulted"][:1]
        with self.assertRaisesRegex(ReviewError, "two source domains"):
            parse_factcheck_response(json.dumps(payload))

    def test_requires_independent_corroboration(self) -> None:
        payload = factcheck_payload()
        finding = payload["findings"][0]
        finding["evidence_basis"] = "independent-corroboration"
        finding["sources"].append(
            {
                "title": "Second page",
                "publisher": "Example Agency",
                "url": "https://agency.example.gov/second",
                "quote": "The event occurred on 3 January.",
            }
        )
        with self.assertRaisesRegex(ReviewError, "not independent"):
            parse_factcheck_response(json.dumps(payload))

    def test_prompt_excludes_political_and_interpretive_claims(self) -> None:
        article = ArticleMetadata(
            article_id="abc",
            url="https://example.com/story",
            canonical_url="https://example.com/story",
            title="Story",
            publisher="Example",
            authors=(),
            published_at="2026-08-03",
            fetched_at="2026-08-03T00:00:00+00:00",
            content_hash="hash",
            text="Article text",
        )
        prompt = build_factcheck_prompt(article)
        self.assertIn("political judgment", prompt)
        self.assertIn("authoritative primary source", prompt)
        self.assertIn("at least two independent reputable sources", prompt)
        self.assertIn("Do NOT report", prompt)

    def test_factcheck_command_allows_web_but_denies_shell_and_write(self) -> None:
        command = build_copilot_command(
            executable="/usr/local/bin/copilot",
            workspace=Path("/tmp/article"),
            prompt="fact check",
            model="gpt-5.6-sol",
            effort="high",
            allow_web=True,
        )
        self.assertIn("--additional-mcp-config", command)
        self.assertIn("--deny-tool=url", command)
        self.assertNotIn("--allow-all-urls", command)
        self.assertIn("--deny-tool=shell", command)
        self.assertIn("--deny-tool=write", command)
        config = json.loads(command[command.index("--additional-mcp-config") + 1])
        args = config["mcpServers"]["safe-web"]["args"]
        self.assertIn("--audit-log", args)
        self.assertIn("--submission-path", args)

    def test_rejects_private_evidence_links(self) -> None:
        payload = factcheck_payload()
        payload["sources_consulted"][0]["url"] = (
            "http://169.254.169.254/latest/meta-data"
        )
        with self.assertRaisesRegex(ReviewError, "invalid evidence source URL"):
            parse_factcheck_response(json.dumps(payload))

    def test_provenance_ignores_redirect_query_strings(self) -> None:
        payload = factcheck_payload()
        fetched_urls = {
            "https://agency.example.gov/record?session=1",
            "https://archive.example.org/register/",
        }
        result = parse_factcheck_response(
            json.dumps(payload),
            fetched_urls=fetched_urls,
        )
        self.assertEqual(result.verdict, "errors-found")

    def test_rejects_search_results_as_evidence(self) -> None:
        payload = factcheck_payload()
        payload["sources_consulted"][0]["url"] = (
            "https://www.google.com/search?q=official+record"
        )
        with self.assertRaisesRegex(ReviewError, "search result pages"):
            parse_factcheck_response(json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
