# Contributing paper reviews

This repository is a public database of strict LLM audits for arXiv papers.
When asked to contribute, identify suitable papers, run the existing review
pipeline, validate the output, rebuild the site, and open a focused pull
request.

## Paper selection

- Check `data/reviews.csv` before doing any work. Do not review an arXiv version
  already present.
- Prefer the latest arXiv version unless the task explicitly targets an older
  version.
- Improve corpus coverage rather than repeatedly choosing one fashionable
  topic. Select across mathematics, physical sciences, life sciences,
  medicine, engineering, economics, social sciences, and computer science.
- Include both highly cited landmark papers and moderately cited papers. Use a
  reputable citation index such as Semantic Scholar or OpenAlex for selection,
  but use arXiv metadata as the authority for paper identity and title.
- Record clean audits as well as audits with findings. Never select or discard
  a paper based on whether the model is likely to find an error.

## Run the review

Set up the project if needed:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Review papers sequentially so arXiv and Copilot are not flooded:

```sh
doublecheck review <arxiv-id>
```

Use the repository defaults: `gpt-5.6-sol` with high reasoning effort. A review
may take several minutes. Transient arXiv source failures are acceptable because
the pipeline can review the extracted PDF text alone.

JSON parsing is only the automated fast path. If a completed model response
cannot be parsed, read the raw response saved under `data/pending/`, adjudicate
its proposed findings against the standard below, and use the project storage
helpers to write the normalized review into `data/reviews.csv`. Do not rerun an
expensive review solely because its formatting was imperfect.

## Finding standard

Only retain high-confidence, demonstrable internal errors established from the
paper itself:

- `direct-contradiction`: two specific statements or values cannot both be true;
- `counterexample`: a concrete case satisfying the stated assumptions violates
  the claim;
- `invalid-derivation`: an identifiable step does not follow under the stated
  assumptions; or
- `arithmetic-error`: recomputation from the paper's stated numbers disagrees
  with the paper.

Do not retain findings about missing implementation details, reproducibility,
absent uncertainty estimates, incomplete ablations, possible confounding,
external citations, stylistic issues, or claims that are merely under-supported.
Prefer `no-glaring-errors-found` over a debatable finding.

Before accepting a finding:

1. read the surrounding definitions, assumptions, and cited equations;
2. verify the quoted values or derivation against the paper source when
   available;
3. confirm the evidence is self-contained and does not depend on an external
   experiment or source; and
4. ensure the limitation text does not reintroduce excluded soft criticisms.

## Data and generated files

- `data/reviews.csv` is the source of truth.
- One row represents one reviewed arXiv version.
- Structured CSV fields contain JSON. Use the project storage helpers or
  `doublecheck review`; do not manually assemble CSV quoting.
- `docs/index.html` is generated. Never edit it by hand.
- Rebuild generated output with:

```sh
doublecheck build
```

- Do not commit PDFs, extracted sources, temporary `.doublecheck-*`
  directories, virtual environments, logs, or Copilot session data.

## Verification

Run:

```sh
python -m unittest discover -s tests
python -m compileall -q src tests
doublecheck build
```

Then inspect the diff and confirm:

- each intended paper appears exactly once;
- verdicts, tags, evidence types, and findings agree;
- the static index contains the new paper IDs;
- unrelated review rows were not modified; and
- no temporary or credential-bearing files are staged.

## Pull request

Keep review-only PRs limited to:

- `data/reviews.csv`;
- `docs/index.html`; and
- `docs/.nojekyll` only if it actually changed.

Documentation or pipeline changes may include their directly related tests.

The PR body should list the added arXiv IDs, explain how the papers improve
disciplinary or citation-range coverage, state the model and effort used, and
summarize any retained findings. Clearly say when all added papers received
`no-glaring-errors-found`.

## Contributing article fact checks

Article fact checks are separate from paper audits:

```sh
doublecheck factcheck <public-article-url>
```

- Check `data/factchecks.csv` first and do not duplicate a canonical URL.
- Only use open HTTP(S) articles whose text the local extractor can read.
- Retain only obvious discrete errors involving numbers, dates, identities,
  locations, quotations, or official records.
- Require an authoritative primary source or two independent reputable sources.
- Never retain findings about framing, omissions, bias, political arguments,
  policy merits, disputed terminology, predictions, or causal interpretation.
- Do not use search snippets or another fact-check article as evidence.
- Adjudicate unparsed responses from `data/factcheck-pending/`; do not rerun a
  completed model response solely because its JSON was imperfect.
- Rebuild with `doublecheck build-news`.
- A fact-check-only PR should normally contain `data/factchecks.csv` and
  `docs/news/index.html`.
