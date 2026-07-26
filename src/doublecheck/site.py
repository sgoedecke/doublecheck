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
    rows = "\n".join(_render_record(record) for record in records)
    if not rows:
        rows = (
            '<tbody><tr><td colspan="5" class="empty">No papers have been reviewed yet. '
            "Run <code>doublecheck review &lt;arxiv-id&gt;</code>."
            "</td></tr></tbody>"
        )
    field_options = _render_options(record.field for record in records)
    error_options = _render_options(
        finding["category"]
        for record in records
        for finding in record.findings
    )
    severity_values = {
        finding["severity"]
        for record in records
        for finding in record.findings
    }
    if any(not record.findings for record in records):
        severity_values.add("none")
    severity_options = _render_options(severity_values)
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
    .meta {{ color: #777; }}
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
    .filters {{ align-items: end; display: grid; gap: .75rem; grid-template-columns:
      repeat(3, minmax(10rem, 1fr)) auto; margin: 1.5rem 0 1rem; }}
    .filters label {{ display: grid; font-size: .85rem; font-weight: 650; gap: .25rem; }}
    select, button {{ background: Canvas; border: 1px solid #8888; border-radius: .35rem;
      color: CanvasText; font: inherit; padding: .45rem .55rem; }}
    button {{ cursor: pointer; }}
    .table-wrap {{ overflow-x: auto; }}
    table {{ border-collapse: collapse; min-width: 54rem; width: 100%; }}
    th, td {{ border-bottom: 1px solid #8885; padding: .75rem; text-align: left;
      vertical-align: top; }}
    th {{ font-size: .85rem; }}
    tbody.paper-entry:hover > tr:first-child {{ background: #8881; }}
    .paper-review-row td {{ padding: 0 .75rem .75rem; }}
    .paper-review-row details {{ margin: .5rem 0; }}
    .paper-entry[hidden] {{ display: none; }}
    #result-count {{ color: #777; font-size: .9rem; }}
    dt {{ font-weight: 650; margin-top: .5rem; }}
    dd {{ margin-left: 0; }}
    code {{ background: #8882; padding: .1rem .25rem; }}
    a {{ color: inherit; }}
    @media (max-width: 48rem) {{
      .filters {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>arXiv Double-Check</h1>
    <p>LLM-assisted audits for open scientific papers.
    <a href="https://github.com/sgoedecke/doublecheck">Contributions welcome on GitHub.</a></p>
  </header>
  <main>
    <div class="filters" aria-label="Review filters">
      <label>Field
        <select id="field-filter"><option value="">All fields</option>{field_options}</select>
      </label>
      <label>Error type
        <select id="error-filter"><option value="">All error types</option>{error_options}</select>
      </label>
      <label>Severity
        <select id="severity-filter"><option value="">All severities</option>{severity_options}</select>
      </label>
      <button type="button" id="clear-filters">Clear filters</button>
    </div>
    <p id="result-count">{len(records)} papers</p>
    <div class="table-wrap">
      <table>
        <thead>
          <tr><th>Paper</th><th>Field</th><th>Verdict</th><th>Error type</th><th>Severity</th></tr>
        </thead>
        {rows}
      </table>
    </div>
    <p id="no-results" hidden>No papers match these filters.</p>
  </main>
  <script>
    const entries = [...document.querySelectorAll(".paper-entry")];
    const fieldFilter = document.querySelector("#field-filter");
    const errorFilter = document.querySelector("#error-filter");
    const severityFilter = document.querySelector("#severity-filter");
    const resultCount = document.querySelector("#result-count");
    const noResults = document.querySelector("#no-results");

    function values(entry, key) {{
      return entry.dataset[key].split(",").filter(Boolean);
    }}

    function applyFilters() {{
      let visible = 0;
      for (const entry of entries) {{
        const matches =
          (!fieldFilter.value || entry.dataset.field === fieldFilter.value) &&
          (!errorFilter.value || values(entry, "errorTypes").includes(errorFilter.value)) &&
          (!severityFilter.value || values(entry, "severities").includes(severityFilter.value));
        entry.hidden = !matches;
        if (matches) visible += 1;
      }}
      resultCount.textContent = `${{visible}} ${{visible === 1 ? "paper" : "papers"}}`;
      noResults.hidden = visible !== 0;
    }}

    for (const filter of [fieldFilter, errorFilter, severityFilter]) {{
      filter.addEventListener("change", applyFilters);
    }}
    document.querySelector("#clear-filters").addEventListener("click", () => {{
      fieldFilter.value = "";
      errorFilter.value = "";
      severityFilter.value = "";
      applyFilters();
    }});
  </script>
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
    field = html.escape(record.field)
    field_attr = html.escape(record.field, quote=True)
    error_types = tuple(sorted({item["category"] for item in record.findings}))
    severities = tuple(sorted({item["severity"] for item in record.findings}))
    error_attr = html.escape(",".join(error_types), quote=True)
    severity_attr = html.escape(",".join(severities) or "none", quote=True)
    tags = "".join(
        f'<span class="tag">{html.escape(tag)}</span>'
        for tag in record.problem_tags
    )
    if not tags:
        tags = '<span class="tag">no-errors-tagged</span>'
    findings = "\n".join(_render_finding(item) for item in record.findings)
    if not findings:
        findings = "<p>No glaring errors were recorded.</p>"
    severity_tags = "".join(
        f'<span class="tag">{html.escape(value)}</span>'
        for value in severities
    ) or '<span class="meta">None</span>'
    return f"""<tbody class="paper-entry" data-field="{field_attr}"
  data-error-types="{error_attr}" data-severities="{severity_attr}">
  <tr>
    <td><a href="{arxiv_url}"><strong>{title}</strong></a></td>
    <td>{field}</td>
    <td><span class="badge {html.escape(record.verdict)}">{verdict}</span></td>
    <td>{tags}</td>
    <td>{severity_tags}</td>
  </tr>
  <tr class="paper-review-row">
    <td colspan="5">
      <details>
        <summary>Review details</summary>
        <div class="details-body">
          <details class="findings">
            <summary>Findings ({len(record.findings)})</summary>
            <div class="findings-body">{findings}</div>
          </details>
          <details class="paper-details">
            <summary>Paper details</summary>
            <div class="details-body">
              <p><strong>Authors:</strong> {authors}</p>
              <p>{summary}</p>
              <p><a href="{arxiv_url}">arXiv</a> · <a href="{pdf_url}">PDF</a> ·
              {html.escape(record.arxiv_id)}</p>
              <p class="meta">Reviewed {reviewed_at} UTC · {model} ({effort})</p>
            </div>
          </details>
        </div>
      </details>
    </td>
  </tr>
</tbody>"""


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


def _render_options(values: object) -> str:
    return "".join(
        f'<option value="{html.escape(value, quote=True)}">'
        f"{html.escape(value.replace('-', ' ').title())}</option>"
        for value in sorted(set(values))
    )
