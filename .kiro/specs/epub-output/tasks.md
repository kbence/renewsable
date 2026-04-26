# Implementation Plan

- [ ] 1. Foundation: dependencies and shared HTTP primitives

- [x] 1.1 Update project dependencies for the EPUB pipeline
  - Drop `goosepaper` and `rmapy` from `pyproject.toml` runtime dependencies, including the comment block that explained their pins.
  - Add `ebooklib` and `feedparser` as runtime dependencies pinned to a current stable release each.
  - Update the project `description` from "Daily news digest PDF pipeline …" to "Daily news digest EPUB pipeline …".
  - Observable completion: `pip install -e .` succeeds in a clean venv with no goosepaper/rmapy in the resolved dependency tree, and `python -c "import ebooklib, feedparser"` returns 0.
  - _Requirements: 2.2_

- [x] 1.2 (P) Extract shared HTTP and robots primitives into a dedicated module
  - Add a new `http` module exposing `fetch_with_retry(url, *, ua, retries, backoff_s, timeout_s=30.0)` and `robots_allows(url, *, cache, ua, timeout_s=30.0)` plus a `RobotsCache` type alias.
  - Lift the implementations from the existing `Builder._fetch_with_retry` and `Builder._robots_allows` verbatim, including the per-host cache key scheme and the fail-open semantics for unparseable or unreachable robots.txt.
  - Preserve module-level `urllib_request` and `_time` aliases so existing monkeypatch-based tests continue to work after relocation.
  - Add a new `tests/test_http.py` covering: retry-then-success, retry exhaustion raising the last exception, robots cache hit per host, fail-open on missing robots.txt, fail-open on non-http(s) schemes.
  - Observable completion: `pytest tests/test_http.py` passes; the new module is importable and has no dependency on `Builder` or `Config`.
  - _Requirements: 3.2_
  - _Boundary: http module_

- [ ] 2. Core: article extraction and EPUB assembly

- [x] 2.1 (P) Implement the article-collection module
  - Add an `articles` module exposing a frozen `Article` record (`title`, `html`, `source_url`) and a `collect(stories, *, ua, retries, backoff_s, robots_cache)` function.
  - For each `provider="rss"` story, fetch the feed via `http.fetch_with_retry`, parse with `feedparser`, and iterate entries (capping at the entry's `config.limit` when present).
  - For each entry, fetch the article URL via `http.fetch_with_retry` and extract the main body via `readability.Document(html).summary()`. On any failure, fall back to the entry's `summary`/`content`.
  - Sanitize the resulting HTML with `lxml.html.clean.Cleaner` (scripts/javascript/style/meta/embedded/frames/forms removed; `links=False` so `<a>` is preserved; images preserved).
  - After sanitization, walk the lxml tree and rewrite every `img/@src` and `a/@href` via `urllib.parse.urljoin(article.source_url, value)`. Drop attributes whose resolved scheme is not `http` or `https`.
  - Drop entries whose post-sanitization title or html is empty; never raise per-entry — only systemic failures propagate.
  - Add `tests/test_articles.py` covering: extraction happy path, RSS-description fallback when article fetch fails, drop-when-both-unusable, per-source `limit` cap honored, relative `<img src>` resolved to absolute, `data:` and `javascript:` URLs dropped.
  - Observable completion: `pytest tests/test_articles.py` passes; given canned RSS and article HTML, `collect` returns the expected list of `Article` instances whose HTML contains only absolute http(s) URLs.
  - _Requirements: 3.1, 3.2, 3.3, 3.4_
  - _Boundary: articles module_
  - _Depends: 1.1, 1.2_

- [x] 2.2 (P) Implement the EPUB assembly module
  - Add an `epub` module exposing `assemble(articles, *, today, output_path, ua, retries, backoff_s, image_timeout_s=15.0, image_max_bytes=10*1024*1024)`.
  - For each article, walk the HTML and for every `<img src>`: fetch the image via `http.fetch_with_retry`, determine MIME from the `Content-Type` header (URL extension as fallback), register an `EpubItem` under `EPUB/images/img-<sha256(url)[:12]>.<ext>`, rewrite `<img src>` to that internal path. On any image fetch failure or oversize, replace the `<img>` with `<span class="renewsable-missing-image" data-src="<url>">[image unavailable: <alt or url>]</span>` and continue. Image failures never raise.
  - Build the book with `ebooklib.epub.EpubBook()`: one `EpubHtml` chapter per article (uid `article-<NNN>`), spine `["nav", *chapters]`, `book.add_item(EpubNcx())`, `book.add_item(EpubNav())`.
  - Set `book.toc = [epub.Link(chapter.file_name, article.title, chapter.uid) for chapter, article in zip(chapters, articles)]` to realize Req 5.1, 5.2, 5.3 concretely.
  - Set metadata: `dc:title` = `f"Renewsable Daily — {today.isoformat()}"`, `dc:creator` = `"Renewsable"`, `dc:language` = `"en"`, `dc:date` = `today.isoformat()`, `dc:identifier` = a UUIDv5 derived deterministically from the date so re-runs produce the same identifier.
  - Write the file via `epub.write_epub(output_path, book)`. On any internal exception (other than per-image fetch failures), unlink any partial file at `output_path` and raise `BuildError`.
  - Add `tests/test_epub.py` covering: produced file's first ZIP entry is `mimetype` with content `application/epub+zip` (stored uncompressed); `META-INF/container.xml` present; nav entry per article with the article title; metadata fields set as specified; image rewrite produces internal path on success and placeholder span on failure; oversize image triggers placeholder; deterministic `dc:identifier` across two same-date runs.
  - Observable completion: `pytest tests/test_epub.py` passes; opening a produced EPUB with `ebooklib.epub.read_epub(path)` returns a book whose nav, spine, metadata, and embedded images match the inputs.
  - _Requirements: 1.1, 1.2, 4.1, 4.2, 4.3, 4.4, 5.1, 5.2, 5.3, 6.1, 6.2, 6.3, 6.4_
  - _Boundary: epub module_
  - _Depends: 1.1, 1.2_

- [ ] 3. Integration: trim Config, rewrite Builder, collapse CLI, delete profiles

- [ ] 3.1 Trim Config and add Stories Schema validation
  - Remove the dataclass fields `goosepaper_bin`, `font_size`, `subprocess_timeout_s`, `device_profiles` and the input-only keys `device_profile`/`device_profiles` from the closed-set check; remove `_normalise_device_profiles`, `_resolve_single`, `_check_no_duplicate_profiles`, and the `from .profiles import …` import.
  - Drop the matching entries from `_TYPE_RULES` and `_apply_defaults`. Update default `goosepaper_bin`/`subprocess_timeout_s`/`font_size` removal so `Config.load` no longer accepts these keys.
  - Add Stories Schema validation in `Config.validate`: for each `stories[i]`, require keys exactly `{"provider", "config"}`; require `provider == "rss"`; require `config.rss_path` to be a non-empty `http://` or `https://` string; allow optional `config.limit` (positive int); reject any other key in the entry or in `entry.config` with a `ConfigError` naming both the file path and the offending key, with a remediation pointing the operator at the schema.
  - Update `tests/test_config.py`: remove tests that asserted goosepaper-specific keys, font_size, subprocess_timeout_s, and device-profile inputs; add tests that confirm the closed-set rejection of those keys with a remediation message; add tests for the new Stories Schema (happy path with and without `limit`, rejection of unknown keys at both levels, rejection of non-http schemes, rejection of `provider != "rss"`).
  - Observable completion: `pytest tests/test_config.py` passes; an existing renewsable config that used to declare `goosepaper_bin` or `font_size` now fails `Config.load` with a `ConfigError` whose message names the offending key and points at the file path.
  - _Requirements: 2.4_
  - _Boundary: Config_

- [ ] 3.2 Rewrite Builder around the new pipeline
  - Change `Builder.build` to `build(today: date | None = None) -> Path` (drop the `profile` parameter and the `DeviceProfile` import).
  - Compute `output_path = config.output_dir / f"renewsable-{today.isoformat()}.epub"`.
  - Replace `_prepare_stories`, `_write_goosepaper_config`, `_run_goosepaper`, `_validate_pdf`, and the per-profile CSS write with: (a) initialize per-run robots cache, (b) call `articles.collect(...)`, (c) raise `BuildError` if zero articles produced (with a remediation pointing at the per-source logs), (d) call `epub.assemble(...)`, (e) call `_validate_epub(output_path)`.
  - Implement `_validate_epub`: open as `zipfile.ZipFile`, assert the first member name is `mimetype`, assert its bytes are exactly `b"application/epub+zip"`, assert `META-INF/container.xml` is in the namelist. On any failure, unlink the file and raise `BuildError` mirroring the existing PDF-cleanup pattern.
  - Remove the `subprocess` module-level alias and the goosepaper-specific module docstring sections; keep `urllib_request` and `_time` aliases (reused via `http`).
  - Rewrite `tests/test_builder.py`: delete every PDF-magic-byte and goosepaper-subprocess test; add tests for the new orchestration — happy path produces a valid EPUB at the expected path, zero-articles raises `BuildError`, `_validate_epub` deletes a malformed file before raising, same-date re-run overwrites the previous EPUB.
  - Observable completion: `pytest tests/test_builder.py` passes; `Builder(config).build()` returns a path that exists, ends in `.epub`, and round-trips through `ebooklib.epub.read_epub`.
  - _Requirements: 1.1, 1.4, 2.1, 3.5, 7.1, 7.2, 7.3_
  - _Boundary: Builder_
  - _Depends: 1.2, 2.1, 2.2, 3.1_

- [ ] 3.3 Collapse the CLI multi-profile loop
  - Remove the `for profile in config.device_profiles:` loops in `build`, `run`, and `test-pipeline`. Each command now invokes `Builder(config).build()` exactly once.
  - For `run` and `test-pipeline`, the upload call becomes `Uploader(config).upload(epub_path, folder=config.remarkable_folder)`.
  - Rename `_todays_pdf_path` to `_todays_epub_path` returning `<output_dir>/renewsable-<YYYY-MM-DD>.epub`. Update the bare `upload` command to use the new helper.
  - Replace every "PDF" mention in command docstrings, help text, and module docstring with "EPUB". Rewrite the exit-code-contract docstring section as needed (contract itself unchanged).
  - Update `tests/test_cli.py`: remove or rewrite tests that asserted multi-profile iteration (`test_build_iterates_profiles` and similar); add tests that confirm `build`/`run` invoke `Builder.build()` exactly once and that `run` passes `folder=config.remarkable_folder` to `Uploader.upload`; update `test_upload_default_path` to expect `.epub`.
  - Observable completion: `pytest tests/test_cli.py` passes; running `renewsable build` with a two-feed config produces exactly one line of stdout pointing at `renewsable-<today>.epub`.
  - _Requirements: 7.1, 8.1_
  - _Boundary: cli_
  - _Depends: 3.2_

- [ ] 3.4 Delete the device-profile module and its tests
  - Remove `src/renewsable/profiles.py` and `tests/test_profiles.py`.
  - Verify no other module still imports `DeviceProfile`, `BUILTIN_PROFILES`, `resolve`, or `render_css` (Config and Builder were updated in 3.1 and 3.2).
  - Observable completion: `grep -r "from renewsable.profiles\|import profiles\|DeviceProfile\|BUILTIN_PROFILES\|render_css" src/ tests/` returns no matches; `pytest` collection succeeds with no `ModuleNotFoundError`.
  - _Requirements: 2.3_
  - _Boundary: profiles module_
  - _Depends: 3.2, 3.3_

- [ ] 4. Validation: cross-component integration tests

- [ ] 4.1 End-to-end build integration test with mocked network
  - Add an integration test in `tests/test_builder.py` (or a new `tests/test_pipeline.py`) that monkeypatches `http.fetch_with_retry` to return canned RSS bytes, canned article HTML (with relative `<img src>` references), and canned image bytes, and runs `Builder(config).build(today=fixed_date)`.
  - Assert: the produced EPUB exists at `<output_dir>/renewsable-<fixed_date>.epub`; nav has one entry per article; metadata fields match Req 6.1–6.4; embedded images are present at internal paths; one of the images was canned to fail and is replaced by a `renewsable-missing-image` span; relative `<img src>` values were resolved to absolute before fetch.
  - Assert the failure path: with all canned articles unusable, `Builder.build` raises `BuildError` and no `.epub` file is left at `output_path`.
  - Observable completion: the integration test passes and exercises every requirement bucket (1, 3, 4, 5, 6, 7) in one run.
  - _Requirements: 1.1, 1.2, 1.4, 3.5, 4.1, 4.2, 4.3, 4.4, 5.1, 5.2, 5.3, 6.1, 6.2, 6.3, 6.4, 7.1, 7.2_
  - _Boundary: Builder, articles, epub, http_
  - _Depends: 3.2, 3.3, 3.4_

- [ ] 4.2 Full test-suite sweep and dead-code grep
  - Run `pytest -q` and confirm the entire suite is green with the goosepaper/PDF/profile tests deleted and the new EPUB tests in their place.
  - Grep for any remaining "goosepaper", "WeasyPrint", "PDF", `\.pdf\b`, "DeviceProfile", "BUILTIN_PROFILES", "rmapy" mentions in `src/`, `tests/`, and `pyproject.toml`. Each remaining mention must be either intentionally retained (e.g., a release note explicitly mentioning the migration) or removed.
  - Observable completion: `pytest -q` exits 0; the grep above returns no unintentional matches; `pip check` reports no broken dependencies.
  - _Requirements: 1.3, 2.1, 2.2, 2.3, 2.4, 8.1, 8.2, 8.3, 8.4_
  - _Boundary: cross-cutting cleanup_
  - _Depends: 4.1_
