# arXiv Double-Check

`doublecheck` runs a restricted, non-interactive GitHub Copilot CLI session
against an arXiv paper, stores demonstrable internal errors in CSV, and
rebuilds a minimal static index for GitHub Pages.

The default reviewer is `gpt-5.6-sol` with high reasoning effort.

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

## Review data

The CSV stores paper metadata, verdict, summary, model configuration, findings,
limitations, and normalized problem tags. Structured values are JSON-encoded
inside their CSV fields.

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

## Tests

```sh
python -m unittest discover -s tests
```
