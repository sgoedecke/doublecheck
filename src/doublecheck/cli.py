from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from doublecheck.article import ArticleError, fetch_article
from doublecheck.arxiv import (
    ArxivError,
    download_and_extract_source,
    download_pdf,
    fetch_metadata,
    normalize_arxiv_id,
)
from doublecheck.factcheck import run_fact_check
from doublecheck.factcheck_site import build_factcheck_site
from doublecheck.factcheck_storage import (
    FactCheckRecord,
    load_factchecks,
    upsert_factcheck,
)
from doublecheck.review import (
    DEFAULT_EFFORT,
    DEFAULT_MODEL,
    EFFORTS,
    ReviewError,
    ReviewParseError,
    extract_pdf_text,
    extract_source_text,
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
DEFAULT_FACTCHECK_CSV = Path("data/factchecks.csv")
DEFAULT_FACTCHECK_SITE = Path("docs/news/index.html")


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
    build_parser.add_argument(
        "--factcheck-csv",
        type=Path,
        default=DEFAULT_FACTCHECK_CSV,
    )
    build_parser.add_argument(
        "--factcheck-site",
        type=Path,
        default=DEFAULT_FACTCHECK_SITE,
    )

    factcheck_parser = subparsers.add_parser(
        "factcheck",
        help="fact-check an open web article and update the fact-check site",
    )
    factcheck_parser.add_argument("url", help="public http(s) article URL")
    factcheck_parser.add_argument("--model", default=DEFAULT_MODEL)
    factcheck_parser.add_argument(
        "--effort",
        choices=tuple(sorted(EFFORTS)),
        default=DEFAULT_EFFORT,
    )
    factcheck_parser.add_argument(
        "--timeout",
        type=_positive_int,
        default=1_800,
        help="Copilot timeout in seconds (default: 1800)",
    )
    factcheck_parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_FACTCHECK_CSV,
    )
    factcheck_parser.add_argument(
        "--site",
        type=Path,
        default=DEFAULT_FACTCHECK_SITE,
    )
    factcheck_parser.add_argument(
        "--no-build",
        action="store_true",
        help="store the result without rebuilding the fact-check site",
    )

    factcheck_build_parser = subparsers.add_parser(
        "build-factchecks",
        help="rebuild the news fact-check site from its CSV",
    )
    factcheck_build_parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_FACTCHECK_CSV,
    )
    factcheck_build_parser.add_argument(
        "--site",
        type=Path,
        default=DEFAULT_FACTCHECK_SITE,
    )
    news_build_parser = subparsers.add_parser(
        "build-news",
        help="rebuild the news fact-check site from its CSV",
    )
    news_build_parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_FACTCHECK_CSV,
    )
    news_build_parser.add_argument(
        "--site",
        type=Path,
        default=DEFAULT_FACTCHECK_SITE,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "review":
            return _review(args)
        if args.command == "build":
            _build(args.csv, args.site)
            return _build_factchecks(args.factcheck_csv, args.factcheck_site)
        if args.command == "factcheck":
            return _factcheck(args)
        if args.command in {"build-factchecks", "build-news"}:
            return _build_factchecks(args.csv, args.site)
    except (
        ArticleError,
        ArxivError,
        ReviewError,
        StorageError,
        OSError,
    ) as exc:
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
        source_directory = workspace / "source"
        print(f"Downloading {metadata.arxiv_id}...", file=sys.stderr)
        source = download_and_extract_source(metadata, source_directory)
        print(f"Source: {source.note}", file=sys.stderr)
        try:
            download_pdf(metadata, pdf_path)
        except ArxivError:
            if not source.available:
                raise
            print(
                "PDF unavailable; extracting compact source text...",
                file=sys.stderr,
            )
            extract_source_text(source_directory, paper_text_path)
        else:
            print("Extracting compact paper text...", file=sys.stderr)
            extract_pdf_text(pdf_path, paper_text_path)
        print(
            f"Reviewing with {args.model} at {args.effort} effort...",
            file=sys.stderr,
        )
        try:
            result = run_copilot_review(
                metadata=metadata,
                paper_text_path=paper_text_path,
                source=source,
                workspace=workspace,
                model=args.model,
                effort=args.effort,
                timeout_seconds=args.timeout,
            )
        except ReviewParseError as exc:
            pending_path = _save_pending_review(
                args.csv,
                metadata.arxiv_id,
                exc.raw_response,
            )
            raise ReviewError(
                f"{exc}; full response saved to {pending_path}"
            ) from exc

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


def _factcheck(args: argparse.Namespace) -> int:
    print(f"Fetching article {args.url}...", file=sys.stderr)
    article = fetch_article(args.url)
    with tempfile.TemporaryDirectory(
        prefix=".doublecheck-factcheck-",
        dir=Path.cwd(),
    ) as temporary:
        workspace = Path(temporary)
        article_path = workspace / "article.txt"
        article_path.write_text(
            "\n".join(
                (
                    f"Title: {article.title}",
                    f"Publisher: {article.publisher}",
                    f"URL: {article.canonical_url}",
                    f"Published: {article.published_at or 'not listed'}",
                    "",
                    article.text,
                )
            ),
            encoding="utf-8",
        )
        print(
            f"Fact-checking with {args.model} at {args.effort} effort...",
            file=sys.stderr,
        )
        try:
            result = run_fact_check(
                article=article,
                article_text_path=article_path,
                workspace=workspace,
                model=args.model,
                effort=args.effort,
                timeout_seconds=args.timeout,
            )
        except ReviewParseError as exc:
            pending_path = _save_pending_factcheck(
                args.csv,
                article.article_id,
                exc.raw_response,
                workspace / "web-audit.jsonl",
            )
            raise ReviewError(
                f"{exc}; full response saved to {pending_path}"
            ) from exc

    record = FactCheckRecord.from_result(
        article=article,
        result=result,
        model=args.model,
        effort=args.effort,
    )
    upsert_factcheck(args.csv, record)
    if not args.no_build:
        _build_factchecks(args.csv, args.site)
    tag_text = ", ".join(record.error_tags) or "none"
    print(
        f"Stored {record.canonical_url}: {record.verdict} (tags: {tag_text})",
        file=sys.stderr,
    )
    return 0


def _build_factchecks(csv_path: Path, site_path: Path) -> int:
    records = load_factchecks(csv_path)
    build_factcheck_site(records, site_path)
    print(
        f"Built {site_path} from {len(records)} fact check(s).",
        file=sys.stderr,
    )
    return 0


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _save_pending_review(
    csv_path: Path,
    arxiv_id: str,
    raw_response: str,
) -> Path:
    pending_directory = csv_path.parent / "pending"
    pending_directory.mkdir(parents=True, exist_ok=True)
    safe_id = arxiv_id.replace("/", "_")
    output = pending_directory / f"{safe_id}.txt"
    output.write_text(raw_response, encoding="utf-8")
    return output


def _save_pending_factcheck(
    csv_path: Path,
    article_id: str,
    raw_response: str,
    audit_path: Path | None = None,
) -> Path:
    pending_directory = csv_path.parent / "factcheck-pending"
    pending_directory.mkdir(parents=True, exist_ok=True)
    output = pending_directory / f"{article_id}.txt"
    output.write_text(raw_response, encoding="utf-8")
    if audit_path is not None and audit_path.exists():
        audit_output = pending_directory / f"{article_id}.audit.jsonl"
        audit_output.write_text(
            audit_path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    return output
