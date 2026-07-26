from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from doublecheck.arxiv import (
    ArxivError,
    download_and_extract_source,
    download_pdf,
    fetch_metadata,
    normalize_arxiv_id,
)
from doublecheck.review import (
    DEFAULT_EFFORT,
    DEFAULT_MODEL,
    EFFORTS,
    ReviewError,
    extract_pdf_text,
    run_copilot_review,
)
from doublecheck.site import build_site
from doublecheck.storage import (
    ReviewRecord,
    StorageError,
    load_records,
    upsert_record,
)

DEFAULT_CSV = Path("data/reviews.csv")
DEFAULT_SITE = Path("docs/index.html")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="doublecheck",
        description="Review arXiv papers with GitHub Copilot CLI.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    review_parser = subparsers.add_parser(
        "review",
        help="review an arXiv paper and update the static site",
    )
    review_parser.add_argument("paper", help="arXiv ID or arxiv.org URL")
    review_parser.add_argument("--model", default=DEFAULT_MODEL)
    review_parser.add_argument(
        "--effort",
        choices=tuple(sorted(EFFORTS)),
        default=DEFAULT_EFFORT,
    )
    review_parser.add_argument(
        "--timeout",
        type=_positive_int,
        default=1_800,
        help="Copilot timeout in seconds (default: 1800)",
    )
    review_parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    review_parser.add_argument("--site", type=Path, default=DEFAULT_SITE)
    review_parser.add_argument(
        "--no-build",
        action="store_true",
        help="store the result without rebuilding the site",
    )

    build_parser = subparsers.add_parser(
        "build",
        help="rebuild the static site from the review CSV",
    )
    build_parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    build_parser.add_argument("--site", type=Path, default=DEFAULT_SITE)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "review":
            return _review(args)
        if args.command == "build":
            return _build(args.csv, args.site)
    except (ArxivError, ReviewError, StorageError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    raise AssertionError(f"unhandled command: {args.command}")


def _review(args: argparse.Namespace) -> int:
    requested_id = normalize_arxiv_id(args.paper)
    print(f"Fetching arXiv metadata for {requested_id}...", file=sys.stderr)
    metadata = fetch_metadata(requested_id)

    with tempfile.TemporaryDirectory(
        prefix=".doublecheck-",
        dir=Path.cwd(),
    ) as temporary:
        workspace = Path(temporary)
        pdf_path = workspace / "paper.pdf"
        paper_text_path = workspace / "paper.txt"
        print(f"Downloading {metadata.arxiv_id}...", file=sys.stderr)
        download_pdf(metadata, pdf_path)
        print("Extracting compact paper text...", file=sys.stderr)
        extract_pdf_text(pdf_path, paper_text_path)
        source = download_and_extract_source(metadata, workspace / "source")
        print(f"Source: {source.note}", file=sys.stderr)
        print(
            f"Reviewing with {args.model} at {args.effort} effort...",
            file=sys.stderr,
        )
        result = run_copilot_review(
            metadata=metadata,
            paper_text_path=paper_text_path,
            source=source,
            workspace=workspace,
            model=args.model,
            effort=args.effort,
            timeout_seconds=args.timeout,
        )

    record = ReviewRecord.from_review(
        metadata=metadata,
        review=result,
        model=args.model,
        effort=args.effort,
    )
    upsert_record(args.csv, record)
    if not args.no_build:
        _build(args.csv, args.site)
    tag_text = ", ".join(record.problem_tags) or "none"
    print(
        f"Stored {record.arxiv_id}: {record.verdict} (tags: {tag_text})",
        file=sys.stderr,
    )
    return 0


def _build(csv_path: Path, site_path: Path) -> int:
    records = load_records(csv_path)
    build_site(records, site_path)
    print(f"Built {site_path} from {len(records)} review(s).", file=sys.stderr)
    return 0


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed
