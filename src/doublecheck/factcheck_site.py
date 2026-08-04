from __future__ import annotations

import html
from pathlib import Path

from doublecheck.factcheck_storage import FactCheckRecord

VERDICT_LABELS = {
    "errors-found": "Errors found",
    "no-obvious-errors-found": "No errors found",
    "inconclusive": "Inconclusive",
}


def build_factcheck_site(
    records: list[FactCheckRecord],
    output: Path,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = "\n".join(_render_record(record) for record in records)
    if not rows:
        rows = (
            '<tbody><tr><td colspan="5">No articles have been checked yet.</td>'
            "</tr></tbody>"
        )
    publisher_options = _options(record.publisher for record in records)
    error_options = _options(
        finding["category"]
        for record in records
        for finding in record.findings
    )
    severities = {
        str(finding["severity"])
        for record in records
        for finding in record.findings
    }
    if any(not record.findings for record in records):
        severities.add("none")
    severity_options = _options(severities)
    content = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="LLM-assisted checks of discrete facts in open articles.">
  <title>Double-Check: News Fact Checks</title>
  <style>
    :root {{ color-scheme: light dark; font-family: system-ui, sans-serif; }}
    body {{ max-width: 72rem; margin: 0 auto; padding: 2rem 1rem 4rem; line-height: 1.55; }}
    header {{ margin-bottom: 2rem; border-bottom: 1px solid #8885; }}
    h1, h2, h3 {{ line-height: 1.2; }}
    .meta {{ color: #777; }}
    .badge, .tag {{ display: inline-block; border: 1px solid #8888; border-radius: 1rem;
      padding: .12rem .55rem; margin: .15rem .2rem .15rem 0; font-size: .85rem; }}
    .errors-found {{ border-color: #b33; color: #b33; }}
    .no-obvious-errors-found {{ border-color: #287a3c; color: #287a3c; }}
    .inconclusive {{ border-color: #9a6a00; color: #9a6a00; }}
    details {{ margin: .7rem 0; }}
    summary {{ cursor: pointer; }}
    .details-body, .findings-body {{ border-left: 2px solid #8884; margin: .65rem 0 0 .45rem;
      padding-left: 1rem; }}
    .filters {{ align-items: end; display: grid; gap: .75rem; grid-template-columns:
      repeat(3, minmax(10rem, 1fr)) auto; margin: 1.5rem 0 1rem; }}
    .filters label {{ display: grid; font-size: .85rem; font-weight: 650; gap: .25rem; }}
    select, button {{ background: Canvas; border: 1px solid #8888; border-radius: .35rem;
      color: CanvasText; font: inherit; padding: .45rem .55rem; }}
    button {{ cursor: pointer; }}
    .table-wrap {{ overflow-x: auto; }}
    table {{ border-collapse: collapse; min-width: 62rem; width: 100%; }}
    th, td {{ border-bottom: 1px solid #8885; padding: .75rem; text-align: left;
      vertical-align: top; }}
    th {{ font-size: .85rem; }}
    tbody.article-entry:hover > tr:first-child {{ background: #8881; }}
    .article-detail-row td {{ padding: 0 .75rem .75rem; }}
    .article-entry[hidden] {{ display: none; }}
    dt {{ font-weight: 650; margin-top: .5rem; }}
    dd {{ margin-left: 0; }}
    blockquote {{ border-left: 3px solid #8885; margin-left: 0; padding-left: 1rem; }}
    a {{ color: inherit; }}
    @media (max-width: 48rem) {{
      .filters {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Double-Check: News Fact Checks</h1>
    <p>LLM-assisted checks of obvious, discrete facts in news and other open articles.
    <a href="../">Scientific paper audits</a> ·
    <a href="https://github.com/sgoedecke/doublecheck">Contributions welcome on GitHub.</a></p>
  </header>
  <main>
    <div class="filters" aria-label="Fact-check filters">
      <label>Publisher
        <select id="publisher-filter"><option value="">All publishers</option>{publisher_options}</select>
      </label>
      <label>Error type
        <select id="error-filter"><option value="">All error types</option>{error_options}</select>
      </label>
      <label>Severity
        <select id="severity-filter"><option value="">All severities</option>{severity_options}</select>
      </label>
      <button type="button" id="clear-filters">Clear filters</button>
    </div>
    <p id="result-count">{len(records)} {"article" if len(records) == 1 else "articles"}</p>
    <div class="table-wrap">
      <table>
        <thead>
          <tr><th>Article</th><th>Publisher</th><th>Verdict</th><th>Error type</th><th>Severity</th></tr>
        </thead>
        {rows}
      </table>
    </div>
    <p id="no-results" hidden>No articles match these filters.</p>
  </main>
  <script>
    const entries = [...document.querySelectorAll(".article-entry")];
    const publisherFilter = document.querySelector("#publisher-filter");
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
          (!publisherFilter.value || entry.dataset.publisher === publisherFilter.value) &&
          (!errorFilter.value || values(entry, "errorTypes").includes(errorFilter.value)) &&
          (!severityFilter.value || values(entry, "severities").includes(severityFilter.value));
        entry.hidden = !matches;
        if (matches) visible += 1;
      }}
      resultCount.textContent = `${{visible}} ${{visible === 1 ? "article" : "articles"}}`;
      noResults.hidden = visible !== 0;
    }}
    for (const filter of [publisherFilter, errorFilter, severityFilter]) {{
      filter.addEventListener("change", applyFilters);
    }}
    document.querySelector("#clear-filters").addEventListener("click", () => {{
      publisherFilter.value = "";
      errorFilter.value = "";
      severityFilter.value = "";
      applyFilters();
    }});
  </script>
</body>
</html>
"""
    output.write_text(content, encoding="utf-8")


def _render_record(record: FactCheckRecord) -> str:
    title = html.escape(record.title)
    url = html.escape(record.canonical_url, quote=True)
    publisher = html.escape(record.publisher)
    publisher_attr = html.escape(record.publisher, quote=True)
    verdict = html.escape(
        VERDICT_LABELS.get(record.verdict, record.verdict.replace("-", " ").title())
    )
    error_attr = html.escape(",".join(record.error_tags), quote=True)
    severity_attr = html.escape(",".join(record.severities) or "none", quote=True)
    tags = "".join(
        f'<span class="tag">{html.escape(tag)}</span>'
        for tag in record.error_tags
    ) or '<span class="meta">None</span>'
    severity_tags = "".join(
        f'<span class="tag">{html.escape(value)}</span>'
        for value in record.severities
    ) or '<span class="meta">None</span>'
    findings = "\n".join(_render_finding(finding) for finding in record.findings)
    if not findings:
        findings = "<p>No obvious factual errors were recorded.</p>"
    authors = html.escape(", ".join(record.authors)) or "Not listed"
    published_at = html.escape(record.published_at) or "Not listed"
    sources = "".join(
        f'<li><a href="{html.escape(source["url"], quote=True)}">'
        f'{html.escape(source["title"])}</a> — '
        f'{html.escape(source["publisher"])}</li>'
        for source in record.sources_consulted
    )
    return f"""<tbody class="article-entry" data-publisher="{publisher_attr}"
  data-error-types="{error_attr}" data-severities="{severity_attr}">
  <tr>
    <td><a href="{url}"><strong>{title}</strong></a></td>
    <td>{publisher}</td>
    <td><span class="badge {html.escape(record.verdict)}">{verdict}</span></td>
    <td>{tags}</td>
    <td>{severity_tags}</td>
  </tr>
  <tr class="article-detail-row">
    <td colspan="5">
      <details>
        <summary>Fact-check details</summary>
        <div class="details-body">
          <p>{html.escape(record.summary)}</p>
          <details>
            <summary>Findings ({len(record.findings)})</summary>
            <div class="findings-body">{findings}</div>
          </details>
          <details>
            <summary>Article and research details</summary>
            <div class="details-body">
              <p><strong>Authors:</strong> {authors}</p>
              <p><strong>Published:</strong> {published_at}</p>
              <p class="meta">Checked {html.escape(record.checked_at.replace("T", " "))} UTC ·
              {html.escape(record.model)} ({html.escape(record.effort)})</p>
              <p><strong>Sources consulted:</strong></p><ul>{sources}</ul>
            </div>
          </details>
        </div>
      </details>
    </td>
  </tr>
</tbody>"""


def _render_finding(finding: dict[str, object]) -> str:
    sources = "".join(
        f'<li><a href="{html.escape(str(source["url"]), quote=True)}">'
        f'{html.escape(str(source["title"]))}</a> — '
        f'{html.escape(str(source["publisher"]))}'
        f'<blockquote>{html.escape(str(source["quote"]))}</blockquote></li>'
        for source in finding["sources"]
    )
    return f"""<details>
  <summary><strong>{html.escape(str(finding["correction"]))}</strong>
  ({html.escape(str(finding["severity"]))})</summary>
  <dl>
    <dt>Article claim</dt><dd><blockquote>{html.escape(str(finding["article_quote"]))}</blockquote></dd>
    <dt>Location</dt><dd>{html.escape(str(finding["location"]))}</dd>
    <dt>Error type</dt><dd>{html.escape(str(finding["category"]))}</dd>
    <dt>Analysis</dt><dd>{html.escape(str(finding["analysis"]))}</dd>
    <dt>Evidence</dt><dd><ul>{sources}</ul></dd>
  </dl>
</details>"""


def _options(values: object) -> str:
    return "".join(
        f'<option value="{html.escape(value, quote=True)}">'
        f"{html.escape(value.replace('-', ' ').title())}</option>"
        for value in sorted(set(values))
    )
