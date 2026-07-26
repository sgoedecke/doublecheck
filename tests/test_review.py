import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from doublecheck.arxiv import PaperMetadata, SourceExtraction
from doublecheck.cli import _save_pending_review
from doublecheck.review import (
    ReviewError,
    build_copilot_command,
    build_review_prompt,
    extract_pdf_text,
    extract_source_text,
    parse_review_response,
)


def valid_payload() -> dict[str, object]:
    return {
        "verdict": "errors-found",
        "summary": "A central estimate is not justified.",
        "findings": [
            {
                "id": "F1",
                "severity": "major",
                "category": "mathematical-error",
                "evidence_type": "counterexample",
                "claim": "The bound drops a necessary term.",
                "location": "Equation 8",
                "analysis": "The preceding inequality does not imply the bound.",
                "evidence": "Setting x=0 leaves a nonzero omitted term.",
                "confidence": "high",
            }
        ],
        "limitations": ["No external replication was attempted."],
    }


class ParseReviewResponseTests(unittest.TestCase):
    def test_parses_valid_response_and_tags(self) -> None:
        result = parse_review_response(json.dumps(valid_payload()))
        self.assertEqual(result.verdict, "errors-found")
        self.assertEqual(result.problem_tags, ("mathematical-error",))

    def test_extracts_json_from_incidental_fence(self) -> None:
        raw = f"```json\n{json.dumps(valid_payload())}\n```"
        result = parse_review_response(raw)
        self.assertEqual(result.findings[0]["id"], "F1")

    def test_repairs_terminal_wrapping_inside_json_strings(self) -> None:
        raw = json.dumps(valid_payload()).replace(
            "central estimate",
            "central\nestimate",
        )
        result = parse_review_response(raw)
        self.assertEqual(
            result.summary,
            "A central estimate is not justified.",
        )

    def test_repairs_terminal_wrapping_inside_enum_values(self) -> None:
        raw = json.dumps(valid_payload()).replace(
            "counterexample",
            "c\nounterexample",
        )
        result = parse_review_response(raw)
        self.assertEqual(
            result.findings[0]["evidence_type"],
            "counterexample",
        )

    def test_repairs_terminal_wrapping_inside_json_keys(self) -> None:
        raw = json.dumps(valid_payload()).replace(
            "evidence_type",
            "evidence_typ\ne",
        )
        result = parse_review_response(raw)
        self.assertEqual(
            result.findings[0]["evidence_type"],
            "counterexample",
        )

    def test_rejects_inconsistent_verdict(self) -> None:
        payload = valid_payload()
        payload["verdict"] = "no-glaring-errors-found"
        with self.assertRaises(ReviewError):
            parse_review_response(json.dumps(payload))

    def test_rejects_soft_review_category(self) -> None:
        payload = valid_payload()
        payload["findings"][0]["category"] = "reproducibility"
        with self.assertRaisesRegex(ReviewError, "invalid category"):
            parse_review_response(json.dumps(payload))

    def test_rejects_non_high_confidence_finding(self) -> None:
        payload = valid_payload()
        payload["findings"][0]["confidence"] = "medium"
        with self.assertRaisesRegex(ReviewError, "invalid confidence"):
            parse_review_response(json.dumps(payload))

    def test_prompt_excludes_soft_review_findings(self) -> None:
        metadata = PaperMetadata(
            arxiv_id="2501.12345v1",
            title="Paper",
            authors=("Author",),
            summary="Abstract",
            published="2025-01-01T00:00:00Z",
            updated="2025-01-01T00:00:00Z",
        )
        prompt = build_review_prompt(
            metadata,
            SourceExtraction(False, 0, "unavailable"),
        )
        self.assertIn("Report only glaring, demonstrable internal errors", prompt)
        self.assertIn("Do NOT report", prompt)
        self.assertIn("missing implementation details", prompt)
        self.assertIn("absent repeated trials", prompt)
        self.assertIn("incomplete ablations", prompt)
        self.assertIn("direct-contradiction", prompt)
        self.assertIn("arithmetic-error", prompt)

    def test_builds_restricted_copilot_command(self) -> None:
        command = build_copilot_command(
            executable="/usr/local/bin/copilot",
            workspace=Path("/tmp/paper"),
            prompt="audit",
            model="gpt-5.6-sol",
            effort="high",
        )
        self.assertIn("--model", command)
        self.assertIn("gpt-5.6-sol", command)
        self.assertIn("--effort", command)
        self.assertIn("high", command)
        self.assertIn("--deny-tool=shell", command)
        self.assertIn("--deny-tool=write", command)
        self.assertIn("--deny-tool=url", command)
        self.assertIn("--disable-builtin-mcps", command)
        self.assertNotIn("--attachment", command)
        self.assertNotIn("--allow-all", command)

    def test_extract_pdf_text_requires_pdftotext(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch(
            "doublecheck.review.shutil.which",
            return_value=None,
        ):
            with self.assertRaisesRegex(ReviewError, "pdftotext is required"):
                extract_pdf_text(
                    Path(temporary) / "paper.pdf",
                    Path(temporary) / "paper.txt",
                )

    def test_saves_unparsed_review_for_agent_adjudication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = _save_pending_review(
                Path(temporary) / "reviews.csv",
                "math/0303109v1",
                "unstructured review",
            )
            self.assertEqual(output.name, "math_0303109v1.txt")
            self.assertEqual(
                output.read_text(encoding="utf-8"),
                "unstructured review",
            )

    def test_extracts_text_from_latex_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.mkdir()
            (source / "main.tex").write_text(
                "\\\\section{Result} A demonstrable claim.",
                encoding="utf-8",
            )
            output = Path(temporary) / "paper.txt"
            extract_source_text(source, output)
            self.assertIn("A demonstrable claim.", output.read_text())

    def test_extracts_text_from_postscript_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.mkdir()
            (source / "paper.ps").write_bytes(
                b"%!PS\\n(This is a paper) show\\n"
            )
            output = Path(temporary) / "paper.txt"
            extract_source_text(source, output)
            self.assertIn("This is a paper", output.read_text())


if __name__ == "__main__":
    unittest.main()
