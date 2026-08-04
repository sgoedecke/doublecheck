import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from doublecheck.article import ArticleError
from doublecheck.webmcp import (
    _ExternalLinkParser,
    _handle_message,
    _tool_definitions,
    web_fetch,
)
import doublecheck.webmcp as webmcp


class SafeWebMCPTests(unittest.TestCase):
    def test_lists_search_and_fetch_tools(self) -> None:
        names = {tool["name"] for tool in _tool_definitions()}
        self.assertEqual(
            names,
            {"web_search", "web_fetch", "submit_factcheck"},
        )

    def test_mcp_initialize_and_tool_listing(self) -> None:
        initialized = _handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            }
        )
        self.assertEqual(
            initialized["result"]["serverInfo"]["name"],
            "doublecheck-safe-web",
        )
        tools = _handle_message(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        )
        self.assertEqual(len(tools["result"]["tools"]), 3)

    @patch("doublecheck.webmcp.normalize_article_url")
    def test_fetch_rejects_url_blocked_by_safety_layer(
        self,
        normalize: object,
    ) -> None:
        normalize.side_effect = ArticleError("private network")
        with self.assertRaisesRegex(ArticleError, "private network"):
            web_fetch("http://169.254.169.254/latest/meta-data")

    def test_tool_result_is_json_serializable(self) -> None:
        response = _handle_message(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "unknown", "arguments": {}},
            }
        )
        json.dumps(response)
        self.assertTrue(response["result"]["isError"])

    def test_parses_external_search_links(self) -> None:
        parser = _ExternalLinkParser()
        parser.feed(
            '<a href="https://www.nasa.gov/mission/apollo-11/">'
            "<span>Apollo 11</span></a>"
        )
        self.assertEqual(
            parser.results,
            [("Apollo 11", "https://www.nasa.gov/mission/apollo-11/")],
        )

    def test_submission_is_allowed_after_research_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            old_path = webmcp._submission_path
            old_calls = webmcp._tool_calls
            old_submitted = webmcp._submitted
            try:
                webmcp._submission_path = Path(temporary) / "submission.json"
                webmcp._tool_calls = webmcp.MAX_TOOL_CALLS
                webmcp._submitted = False
                result = webmcp._call_tool(
                    "submit_factcheck",
                    {
                        "verdict": "inconclusive",
                        "summary": "test",
                        "findings": [],
                        "sources_consulted": [],
                    },
                )
                self.assertTrue(result["accepted"])
            finally:
                webmcp._submission_path = old_path
                webmcp._tool_calls = old_calls
                webmcp._submitted = old_submitted


if __name__ == "__main__":
    unittest.main()
