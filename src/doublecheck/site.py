from __future__ import annotations

import html
from pathlib import Path

from doublecheck.storage import ReviewRecord

VERDICT_LABELS = {
    "errors-found": "Glaring errors found",
    "no-glaring-errors-found": "No glaring errors found",
    "inconclusive": "Inconclusive",
}


def build_site(records: list[ReviewRecord], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    cards = "\n".join(_render_record(record) for record in records)
    if not cards:
        cards = (
            '<p class="empty">No papers have been reviewed yet. '
            "Run <code>doublecheck review &lt;arxiv-id&gt;</code>.</p>"
        )
    content = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="LLM-assisted technical audits of arXiv papers.">
  <title>arXiv Double-Check</title>
  <style>
    :root {{ color-scheme: light dark; font-family: system-ui, sans-serif; }}
    body {{ max-width: 62rem; margin: 0 auto; padding: 2rem 1rem 4rem; line-height: 1.55; }}
    header {{ margin-bottom: 2rem; border-bottom: 1px solid #8885; }}
    h1, h2, h3 {{ line-height: 1.2; }}
    article {{ border: 1px solid #8885; border-radius: .65rem; margin: 1rem 0;
      padding: 1.25rem; }}
    article h3 {{ margin-top: 0; }}
    .meta {{ color: #777; }}
    .status {{ margin: .5rem 0 1rem; }}
    .badge, .tag {{ display: inline-block; border: 1px solid #8888; border-radius: 1rem;
      padding: .12rem .55rem; margin: .15rem .2rem .15rem 0; font-size: .85rem; }}
    .errors-found {{ border-color: #b33; color: #b33; }}
    .no-glaring-errors-found {{ border-color: #287a3c; color: #287a3c; }}
    .inconclusive {{ border-color: #9a6a00; color: #9a6a00; }}
    details {{ margin: .7rem 0; }}
    summary {{ cursor: pointer; }}
    .findings-body, .details-body {{ border-left: 2px solid #8884; margin: .65rem 0 0 .45rem;
      padding-left: 1rem; }}
    .finding {{ margin: .7rem 0; }}
    .details-body p:first-child {{ margin-top: 0; }}
    dt {{ font-weight: 650; margin-top: .5rem; }}
    dd {{ margin-left: 0; }}
    code {{ background: #8882; padding: .1rem .25rem; }}
    a {{ color: inherit; }}
  </style>
</head>
<body>
  <header>
    <h1>arXiv Double-Check</h1>
    <p>LLM-assisted audits for open scientific papers.</p>
    <p><a href="https://github.com/sgoedecke/doublecheck">Contributions welcome on GitHub.</a></p>
  </header>
  <main>
    <h2>Reviewed papers</h2>
    {cards}
  </main>
</body>
</html>
"""
    output.write_text(content, encoding="utf-8")
    (output.parent / ".nojekyll").touch()


def _render_record(record: ReviewRecord) -> str:
    title = html.escape(record.title)
    authors = html.escape(", ".join(record.authors))
    summary = html.escape(record.summary)
    arxiv_url = html.escape(record.arxiv_url, quote=True)
    pdf_url = html.escape(record.pdf_url, quote=True)
    reviewed_at = html.escape(record.reviewed_at.replace("T", " "))
    model = html.escape(record.model)
    effort = html.escape(record.effort)
    verdict = html.escape(
        VERDICT_LABELS.get(record.verdict, record.verdict.replace("-", " ").title())
    )
    tags = "".join(
        f'<span class="tag">{html.escape(tag)}</span>'
        for tag in record.problem_tags
    )
    if not tags:
        tags = '<span class="tag">no-errors-tagged</span>'
    findings = "\n".join(_render_finding(item) for item in record.findings)
    if not findings:
        findings = "<p>No glaring errors were recorded.</p>"
    return f"""<article>
  <h3><a href="{arxiv_url}">{title}</a></h3>
  <p class="status">
    <span class="badge {html.escape(record.verdict)}">{verdict}</span>
    {tags}
  </p>
  <details class="findings">
    <summary>Findings ({len(record.findings)})</summary>
    <div class="findings-body">
      {findings}
    </div>
  </details>
  <details class="paper-details">
    <summary>Paper details</summary>
    <div class="details-body">
      <p><strong>Authors:</strong> {authors}</p>
      <p>{summary}</p>
      <p>
        <a href="{arxiv_url}">arXiv</a> · <a href="{pdf_url}">PDF</a> ·
        {html.escape(record.arxiv_id)}
      </p>
      <p class="meta">Reviewed {reviewed_at} UTC · {model} ({effort})</p>
    </div>
  </details>
</article>"""


def _render_finding(finding: dict[str, str]) -> str:
    claim = html.escape(finding.get("claim", "Finding"))
    severity = html.escape(finding.get("severity", "unknown"))
    confidence = html.escape(finding.get("confidence", "unknown"))
    category = html.escape(finding.get("category", "other"))
    evidence_type = html.escape(finding.get("evidence_type", "unknown"))
    location = html.escape(finding.get("location", "Not specified"))
    analysis = html.escape(finding.get("analysis", ""))
    evidence = html.escape(finding.get("evidence", ""))
    return f"""<details class="finding">
  <summary><strong>{claim}</strong> ({severity}, {confidence} confidence)</summary>
  <dl>
    <dt>Tag</dt><dd>{category}</dd>
    <dt>Evidence type</dt><dd>{evidence_type}</dd>
    <dt>Location</dt><dd>{location}</dd>
    <dt>Analysis</dt><dd>{analysis}</dd>
    <dt>Evidence</dt><dd>{evidence}</dd>
  </dl>
</details>"""
