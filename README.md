# arXiv Double-Check

`doublecheck` runs a restricted, non-interactive GitHub Copilot CLI session
against an arXiv paper, stores demonstrable internal errors in CSV, and
rebuilds a minimal static index for GitHub Pages.

The default reviewer is `gpt-5.6-sol` with high reasoning effort.

## Vision

The goal is a central, public database of LLM-assisted audits for research
papers. A review belongs in the database whether it finds an error or not:
clean audits make the corpus useful, while concrete findings give readers a
specific claim to verify.

The corpus should span disciplines and include both highly cited landmark
papers and moderately cited work that is less likely to receive sustained
scrutiny. arXiv provides the stable paper identifiers and source artifacts;
the static site makes the accumulated reviews easy to browse.

Contributions are designed to be agent-friendly. You should be able to point a
capable coding agent at this repository and ask it to contribute paper reviews.
Repository-specific instructions are in [`AGENTS.md`](AGENTS.md).

## Requirements

- Python 3.9 or newer
- Poppler's `pdftotext` command (`brew install poppler` on macOS or
  `apt install poppler-utils` on Debian/Ubuntu)
- [GitHub Copilot CLI](https://docs.github.com/en/copilot/github-copilot-in-the-cli)
  installed and authenticated
- A Copilot plan with access to the configured model

## Setup

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

## Review a paper

```sh
doublecheck review 2501.12345
doublecheck review https://arxiv.org/abs/2501.12345v2
```

The command:

1. resolves arXiv metadata, downloads the PDF, and extracts compact plain text;
2. downloads and safely extracts the source when arXiv provides it;
3. asks Copilot to look only for high-confidence contradictions,
   counterexamples, invalid derivations, and arithmetic errors using the compact
   paper text plus source when available;
4. upserts one row per arXiv version into `data/reviews.csv`; and
5. rebuilds `docs/index.html`.

Override the model, effort, or timeout when needed:

```sh
doublecheck review 2501.12345 --model gpt-5.6-sol --effort xhigh --timeout 3600
```

Rebuild the site without running a review:

```sh
doublecheck build
```

## Contribute reviews

Contributions that add papers are welcome. A focused review PR should:

1. choose one or more arXiv papers that are not already in
   `data/reviews.csv`, preferably improving the corpus's disciplinary and
   citation-range coverage;
2. run `doublecheck review <arxiv-id>` for each paper, sequentially;
3. inspect every proposed finding and remove anything that does not meet the
   strict, demonstrable-error standard below;
4. run the tests and regenerate the static site;
5. commit the resulting `data/reviews.csv` and `docs/index.html` changes; and
6. explain the paper-selection rationale and summarize any findings in the PR.

Reviews with `no-glaring-errors-found` are valid and useful contributions. Do
not manufacture a finding to make a review seem more interesting.

Use the latest arXiv version unless there is a reason to audit a historically
important earlier version. Avoid duplicate work by checking both the base arXiv
ID and version already present in the CSV.

## Review data

The CSV stores paper metadata, broad arXiv-derived field, verdict, summary,
model configuration, findings, limitations, and normalized problem tags.
Structured values are JSON-encoded inside their CSV fields.

Error tags are selected from:

`mathematical-error`, `logical-error`, `experimental-error`,
`statistical-error`, and `factual-error`.

Reproducibility omissions, missing uncertainty estimates, incomplete
ablations, possible confounding, and merely under-supported claims are
explicitly excluded. Each stored finding must instead identify one of four
evidence types: `direct-contradiction`, `counterexample`,
`invalid-derivation`, or `arithmetic-error`.

## Publish with GitHub Pages

1. Create a GitHub repository and push this project to its `main` branch.
2. In **Settings → Pages**, set **Source** to **GitHub Actions**.
3. Commit and push `data/reviews.csv` after local reviews.

The included workflow rebuilds the static index from committed CSV data and
deploys `docs/`. Reviews remain local, so Copilot credentials and AI credits
are not required in GitHub Actions.

## Safety and limitations

The Copilot subprocess treats papers as untrusted input. It runs in a temporary
paper-only working directory with shell, writes, URL access, custom
instructions, built-in MCP servers, and remote control disabled.

An LLM review can miss errors or invent plausible-sounding objections. Every
finding should be independently checked; a clean result is not evidence that a
paper is correct.

## Related work

[To Err Is Human: Systematic Quantification of Errors in Published AI Papers via LLM Analysis](https://arxiv.org/abs/2512.05925)
is related; this project differs because it is not restricted to AI papers,
invites external contributors, and uses an
[agentic harness instead of a pipeline](https://www.seangoedecke.com/build-agents-not-pipelines/).

## Tests

```sh
python -m unittest discover -s tests
```
