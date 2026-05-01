# Implementation Plan

- [x] 1. Add `trafilatura` as a runtime dependency
  - Add `trafilatura>=2.0.0,<3` to `[project] dependencies` in `pyproject.toml`. Place it alongside the existing `feedparser`/`ebooklib`/`readability-lxml` entries; do not remove or modify any other dependency.
  - Run `python -c "import tomllib; tomllib.load(open('pyproject.toml','rb'))"` to confirm valid TOML.
  - Run `pip install -e .` in the project venv. Confirm `pip check` reports no broken requirements.
  - Run `python -c "import trafilatura; print(trafilatura.__version__)"` to confirm the import resolves.
  - Run `pytest -q` and confirm the full suite is still green: nothing imports `trafilatura` yet, so no behavior changes.
  - Observable completion: `pytest -q` exits 0; `python -c "import trafilatura, readability"` exits 0; `pip check` is clean.
  - _Requirements: 1.1_

- [x] 2. Implement the trafilatura → readability → RSS chain in `_extract_body` and lock it with tests
  - Add `import trafilatura  # type: ignore[import-untyped]` at the top of `src/renewsable/articles.py`, alongside the existing `import feedparser` and `from readability import Document` lines. The module-level import is the test monkeypatch seam (mirrors `articles.feedparser`).
  - Modify `_extract_body(entry, link, *, ua, retries, backoff_s) -> str` to fetch as today, then sequence three extractors:
    1. **trafilatura first**: call `trafilatura.extract(html_text, output_format='html', include_images=True, include_links=True, url=link)` inside a `try/except Exception`. On exception or when the result is `None` or `_has_text(result)` is false, log at INFO and fall through. Otherwise normalize the output (strip any `<html>`/`<body>` wrappers — see Implementation Notes below) and return.
    2. **readability second**: existing `Document(html_text).summary()` call inside its existing `try/except`. On exception or when the result is empty/`_has_text`-false, log at INFO and fall through.
    3. **RSS summary last**: call `_rss_fallback_html(entry)` and return its result (which may itself be empty — `_build_article` then drops the entry).
  - **Output-shape normalization**: parse the trafilatura result with `lxml.html.fromstring`; if a `<body>` element is present, return its inner HTML; else if an `<html>` element is present, return its inner HTML; else return the input string unchanged. This guards against trafilatura emitting a full document, which would otherwise survive `Cleaner(page_structure=False)` and surface as nested `<html>` tags inside the EPUB chapter wrapper.
  - Add INFO-level logs matching the existing tone: `"trafilatura returned empty body for %s; trying readability"` (None/empty case) and `"trafilatura raised for %s (%s); trying readability"` (exception case). Per-entry WARNING logging stays in `_build_article`; do not duplicate.
  - Update `tests/test_articles.py`:
    - Rename `test_happy_path_extracts_readability_body` → `test_happy_path_extracts_via_trafilatura` and verify the existing assertions still hold under the real new chain (substring `"actual story body" in a.html`; no `<script` substring).
    - Add `test_falls_back_to_readability_when_trafilatura_returns_none`: monkeypatch `articles_mod.trafilatura.extract` to return `None`; pass real readability-friendly HTML; assert the readability-extracted body wins (substring assertion on the article prose).
    - Add `test_falls_back_to_readability_when_trafilatura_raises`: monkeypatch `articles_mod.trafilatura.extract` to raise `RuntimeError("boom")`; same readability-friendly HTML; assert readability output wins; assert `collect` did not raise.
    - Add `test_falls_back_to_rss_summary_when_both_extractors_empty`: monkeypatch `articles_mod.trafilatura.extract` to return `None` and monkeypatch `articles_mod.Document` so its `.summary()` returns `""`; the entry's RSS `summary` field carries `<p>fallback summary text</p>`; assert `Article.html` contains `"fallback summary text"`.
    - Add `test_trafilatura_full_document_output_is_normalized_to_fragment`: monkeypatch `articles_mod.trafilatura.extract` to return the literal string `<html><body><p>main content</p></body></html>`; assert the produced `Article.html` contains `"main content"` and contains zero occurrences of `<html` or `<body` (case-insensitive).
    - Add `test_bbc_style_nextjs_html_extracts_multi_paragraph_body`: define an inline canned fixture with at least 4 levels of nested `<div>` wrappers, no `<article>` or `<main>` parent, and at least 5 sibling `<p class="sc-XXXXXXXX-N">` elements at the deepest level, each carrying ≥30 words of article-like prose. Assert `Article.html` from the trafilatura path contains text from at least three of the five paragraphs. Differentiator assertion: monkeypatch `articles_mod.trafilatura.extract` to return `None` and re-run the same fixture through readability alone; assert readability's `Article.html` character count is < 30% of trafilatura's on this fixture.
  - Observable completion: `pytest tests/test_articles.py -q` passes with the renamed test and all five new tests present; `pytest -q` (full suite) is still green; running the example config end-to-end against a canned BBC-shaped HTML feed (via the existing test_pipeline harness pattern) produces a chapter whose `<body>` content contains multiple `<p>` paragraphs.
  - _Requirements: 1.1, 1.2, 1.3, 2.1, 2.2, 2.3, 3.1, 3.2, 4.1, 4.2, 5.1, 5.2, 6.1, 6.2_
  - _Boundary: src/renewsable/articles.py, tests/test_articles.py_
  - _Depends: 1_

- [x] 3. Full-suite regression and smoke verification
  - Run `pytest -q` and confirm no test failed compared to the baseline before this branch (baseline at the merge-base with `main` was 239 passing).
  - Run `renewsable --help` and confirm the CLI loads cleanly.
  - Run `pip check` and confirm no broken requirements (especially: `trafilatura`, `lxml`, `lxml_html_clean`, `readability-lxml` co-resolve without conflicts).
  - Grep `src/` and `tests/` for any leftover references to `readability-lxml` as the *primary* extractor in docstrings or comments that the task-2 changes did not catch (e.g., the `articles.py` module docstring may need a one-line update if it currently says "extracts via readability"). The dependency itself stays — we are sweeping comments/docstrings only.
  - Observable completion: `pytest -q` exits 0 with ≥ 244 tests passing (baseline 239 + the 5 new tests from task 2); `renewsable --help` prints the CLI usage with exit 0; `pip check` reports clean; the only remaining `readability-lxml` references in `src/` and `tests/` describe its role as *secondary fallback* or as the existing pinned dependency rationale.
  - _Requirements: 5.1, 5.2, 6.1, 7.1, 7.2, 7.3_
  - _Boundary: cross-cutting cleanup_
  - _Depends: 2_
