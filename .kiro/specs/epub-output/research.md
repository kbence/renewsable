# Gap Analysis — `epub-output`

Generated: 2026-04-26. Brownfield analysis to inform the design phase. Information-only; no implementation decisions are made here.

## 1. Current State Investigation

### Domain layout (relevant subset)
- `src/renewsable/builder.py` (524 LOC) — owns the build pipeline: feed pre-fetch, robots.txt enforcement, retry/backoff, goosepaper subprocess invocation, PDF validation. Single class `Builder` with the seam pattern of module-level `subprocess`/`urllib_request`/`_time` aliases for monkeypatching.
- `src/renewsable/config.py` (499 LOC) — frozen dataclass `Config` with closed-set top-level keys. Holds `goosepaper_bin`, `font_size`, `device_profiles`, `subprocess_timeout_s`, retry/backoff knobs.
- `src/renewsable/profiles.py` (219 LOC) — `DeviceProfile` value object, registry (`rm2`, `paper_pro_move`), shallow-merge `resolve()`, and `render_css()` that emits `@page` size/margin/font-size CSS plus optional grayscale filters.
- `src/renewsable/cli.py` (396 LOC) — Click commands `build`, `upload`, `run`, `test-pipeline`. `build`/`run`/`test-pipeline` iterate `config.device_profiles`, calling `Builder(config).build(profile)` per profile. `_todays_pdf_path` defaults the bare `upload` to `renewsable-<YYYY-MM-DD>.pdf` (no profile suffix — already a single-file naming pattern, currently a known mismatch with multi-profile output).
- `src/renewsable/uploader.py` (391 LOC) — wraps `rmapi mkdir`/`rmapi put --force`. **File-type agnostic**: `Uploader.upload(pdf: Path, folder=...)` only treats the path as bytes; the variable name "pdf" is cosmetic. No PDF magic-byte check here.
- Tests: `tests/test_builder.py` (1032 LOC), `tests/test_cli.py` (693 LOC), `tests/test_config.py` (596 LOC), `tests/test_profiles.py` (266 LOC) all have PDF-, profile-CSS-, or multi-profile-coupled assertions.

### Conventions
- **Module-level seams for tests**: `subprocess`, `urllib_request`, `_time`, `datetime` are imported at module scope (with `# noqa: F401`) so tests monkeypatch one attribute. Any new subsystem (article fetcher, image fetcher, EPUB writer) should follow this.
- **Errors**: `RenewsableError` hierarchy (`BuildError`, `UploadError`, `ConfigError`, etc.) carries `message` + `remediation`. Builder always cleans up half-written artefacts before raising.
- **Frozen dataclass config + closed-set keys**: any new input is validated by `_TYPE_RULES` and `_apply_defaults`; unknown keys raise `ConfigError`.
- **Per-run `tempfile.TemporaryDirectory(prefix="renewsable-")`**: feeds, styles, intermediate config all land in `<tmpdir>` and disappear on exit.
- **Robots + retry/backoff**: `_robots_allows()` + `_fetch_with_retry()` are reusable; both keyed on `config.user_agent`, `config.feed_fetch_retries`, `config.feed_fetch_backoff_s`.

### Existing dependencies (`pyproject.toml`)
- `goosepaper` (git pin) — to be removed.
- `rmapy` — declared only because goosepaper imports it eagerly. Removable with goosepaper.
- `readability-lxml==0.8.1` — already present (transitive use by goosepaper). **Directly usable for article extraction post-goosepaper.**
- `lxml_html_clean` — already present (compatibility shim for readability-lxml). Stays.
- `click` — stays.
- No existing EPUB library, no `feedparser`, no `requests`/`httpx` (urllib stdlib only).

## 2. Requirements Feasibility — Requirement-to-Asset Map

| Req | Need | Existing asset | Gap |
|---|---|---|---|
| 1 EPUB sole output | An EPUB writer producing valid EPUB 3 | None | **Missing** — no EPUB lib present |
| 1.4 Validate EPUB or fail | Pattern: `Builder._validate_pdf` (builder.py:381) | Adapt → `_validate_epub` (zip + mimetype "application/epub+zip" + container.xml + opf parseability) | Constraint — must mirror cleanup-on-fail behavior |
| 2 Goosepaper removal | `_run_goosepaper`, `_write_goosepaper_config`, `goosepaper_bin`, `font_size`, `subprocess_timeout_s` | All present, all delete-targets | Constraint — coordinated removal across builder, config, CSS rendering, tests |
| 2.4 No goosepaper config keys | `style`, `font_size` keys in `Config` and `_write_goosepaper_config` (builder.py:264) | Present | **Missing** — must be removed from `_TYPE_RULES`, dataclass field, and goosepaper-config serializer |
| 3 Article fetch + extraction | `readability-lxml` already a dep; `_fetch_with_retry` reusable | Pattern fits — extend the same retry/backoff + UA + robots gate to article URLs | Unknown — what RSS-parse layer? `feedparser` not in deps; `xml.etree.ElementTree` works for RSS 2.0/Atom but is more code |
| 3.2 Robots + retry on article URLs | `_robots_allows()` + `_fetch_with_retry()` | Reusable as-is | Constraint — keep robots cache scoped per-build |
| 3.3 Fall back to RSS description | Need RSS entry's `description`/`content:encoded` | Currently goosepaper owns this; renewsable would now need to parse RSS itself | **Missing** — RSS parsing layer |
| 3.5 Fail if every story unusable | Existing pattern: `rss_total > 0 and rss_succeeded == 0 → BuildError` (builder.py:159) | Reusable shape | Constraint — extend to "no article produced any usable content" |
| 4 Image embedding | None | Need: HTML walker, image fetcher (reuse retry/UA), MIME-type detection, EPUB resource registration | **Missing** — entirely new subsystem |
| 4.3 Visible alt-text placeholder on image fetch failure | None | Need: HTML mutation that replaces `<img>` with a marked placeholder | **Missing** |
| 5 EPUB nav (TOC) | None | EPUB 3 nav.xhtml — handled natively by `ebooklib` if chosen | Research item — see §3 |
| 6 EPUB metadata | None | Trivially set by any EPUB writer | Missing — straightforward |
| 7 Single-output collapse | `cli.py` build/run/test-pipeline loop over `config.device_profiles` (builder.py:147 includes profile suffix in filename) | Both must change | Constraint — touches CLI and Builder; supersedes device-profiles spec Req 6 user-visible behavior |
| 7.2 Filename `renewsable-<YYYY-MM-DD>.epub` | `_todays_pdf_path` (cli.py:198) already shapes this name (minus extension) | Ironic alignment — already there for the `upload` default | Small change — extension swap |
| 7.3 Same-date overwrite | Existing behavior — `output_path.write_bytes(...)` overwrites | Reusable | None |
| 8 Upload | `Uploader.upload` is path-typed, not PDF-typed | Reusable as-is | Research item — confirm rmapi accepts `.epub` |
| 8.2 Replace EPUB on cloud | `rmapi put --force` already used | Reusable | Research item — does `--force` overwrite cross-extension on the same basename? |

### Complexity signals
- **Workflow**: pipeline orchestration (fetch → extract → embed → assemble → validate → upload) — same shape as today, but the assembly step changes from "shell out to goosepaper" to "build EPUB in-process".
- **External integrations**: HTTP fetch only (article URLs, image URLs). No new external services.
- **Algorithmic logic**: HTML walking + image rewriting; readability already gives main content.
- **Format spec**: EPUB 3 — well-documented, mature libraries available.

## 3. Implementation Approach Options

### Option A — Extend `Builder` in place; swap goosepaper for an in-process EPUB writer

**Shape**: Replace `_run_goosepaper` / `_write_goosepaper_config` / `_validate_pdf` with `_extract_articles`, `_embed_images`, `_assemble_epub`, `_validate_epub`. Keep the outer `build()` skeleton (per-run tempdir, robots cache, fetch-with-retry). Keep `_prepare_stories` for the RSS-fetch part; downstream of it, the article-extraction step takes over what goosepaper used to do.

**Files touched**: `builder.py` (major), `config.py` (drop `goosepaper_bin`, `font_size`, `subprocess_timeout_s`?), `profiles.py` (gut `render_css`, possibly trim `DeviceProfile` fields), `cli.py` (collapse profile loop), `pyproject.toml` (drop goosepaper, rmapy; add EPUB lib), most of the test suite.

**Trade-offs**:
- ✅ One clear seam swap; reuses every retry/robots/UA/tempdir mechanic already present.
- ✅ Module-level subprocess/urllib aliases stay relevant for the new fetchers.
- ✅ Keeps the `BuildError` cleanup contract intact.
- ❌ `Builder` grows; the file is already 524 LOC and may want a sub-module.
- ❌ Tightly couples article-extraction logic to the build orchestrator.

### Option B — Introduce a separate `epub` (or `assemble`) module; `Builder` orchestrates

**Shape**: New file `src/renewsable/epub.py` (or `assemble.py`) owning EPUB assembly: take a list of `Article` records (title, html, images-resolved) plus metadata, return bytes/Path. New `src/renewsable/articles.py` for article-fetch + extraction. `Builder` becomes a thin orchestrator: fetch RSS → fetch + extract articles → assemble EPUB → validate → return path.

**Files touched**: builder.py (rewritten as a smaller orchestrator), new `articles.py`, new `epub.py`, plus the same config/cli/test sweeps as Option A.

**Trade-offs**:
- ✅ Cleaner separation; unit tests can target each subsystem in isolation.
- ✅ Reduces builder.py size; aligns with the "module-level seam" pattern (each new module gets its own seams for tests).
- ✅ Easier to swap an EPUB lib later if needed.
- ❌ More files, more interface surface to design upfront.
- ❌ Risk of over-decomposing for a single-format output — borderline premature abstraction.

### Option C — Hybrid: extract article-fetch into its own module, keep EPUB assembly inside `Builder`

**Shape**: `articles.py` (fetch + readability extraction + RSS-description fallback + image-URL collection). `Builder` handles EPUB assembly inline (it's mostly metadata + spine + nav with one library call) and validation. `profiles.py` and goosepaper-related config/CLI shrink as in A/B.

**Trade-offs**:
- ✅ Article-fetch is the substantive new logic and benefits from isolation; EPUB assembly with a mature library is essentially configuration and not worth its own module.
- ✅ Smaller refactor than B; lower file-count growth.
- ❌ `Builder` still grows somewhat.

## 4. Research Needed (defer to design)

1. **EPUB library choice** — primary candidates:
   - `ebooklib` (most popular, EPUB 2 + 3, native nav, mature) — research version stability and Pi-friendly install (pure Python? lxml-only?).
   - `pypub` — simpler API, EPUB 3, smaller footprint.
   - Hand-rolled zip + Jinja templates — full control, no new dep, but EPUB 3 nav + spine + manifest is non-trivial to get right.
2. **rmapi + EPUB on reMarkable** — confirm:
   - `rmapi put --force file.epub /News/` accepts `.epub` and the device renders it.
   - Whether `--force` overwrites cross-extension (e.g., yesterday's `.pdf` and today's `.epub` collision) or both coexist. Likely matters for the migration window even though Req 1.3 forbids producing PDF going forward.
3. **RSS parsing** — choose a path:
   - Add `feedparser` (well-known, handles RSS/Atom edge cases) — new dep.
   - Use stdlib `xml.etree.ElementTree` — no new dep, more code, must handle RSS 2.0 + Atom + namespaces.
   - Goosepaper internally relied on `feedparser` for the `file://` rewrite trick to work; verify that approach still applies (or becomes unnecessary if we parse XML in-process).
4. **Article extraction edge cases** — `readability-lxml==0.8.1` quirks (the dep pin in `pyproject.toml` documents a known regression in 0.8.4.1); validate against a few real feeds.
5. **Image fetch policy** — clarify against requirements:
   - Size limits / timeouts (Pi has bounded RAM and network).
   - Whether to preserve original format vs. transcode (e.g., WebP → JPEG for older readers); Req 4 doesn't mandate either.
   - Robots.txt for image hosts: is the per-image robots cost acceptable, or treat images as out-of-scope for robots? (Spec Req 3.2 mentions robots only for article URLs.)
6. **Profile cleanup scope** — Requirements §Boundary leaves cleanup to design. Options:
   - Remove `device_profiles` config entirely (breaking change).
   - Keep `device_profiles` for forward-compat but make all fields no-ops (not recommended; misleading).
   - Keep only `color` and `remarkable_folder` from `DeviceProfile`, drop the rest. Single-output forces "first profile wins" or "merge into top-level keys". Decision shape, not a research item.

## 5. Effort & Risk

- **Effort: M (3–7 days)**. Justification: one swap point in `Builder` plus three new responsibilities (article fetch, image embed, EPUB assemble) using a mature library; substantial test rewrite; coordinated dependency cleanup. Not L because no new external integrations and no architectural shift.
- **Risk: Medium**. Justification: known-format work with mature libraries reduces unknowns, but the test suite is large and PDF-coupled, the device-profiles spec is being partially superseded (cross-spec coordination), and rmapi/reMarkable EPUB upload is not yet verified in this codebase.

## 6. Recommendations for Design Phase

- **Preferred approach**: Option C (hybrid) — extract `articles.py` for fetch+extract+fallback, keep EPUB assembly in `Builder`. Rationale: article extraction is the meaningful new behavior worth isolating; EPUB assembly with `ebooklib` is largely configuration. Revisit if EPUB assembly grows beyond ~100 LOC.
- **Key decisions for design**:
  1. EPUB library (likely `ebooklib`); confirm Pi install path.
  2. RSS parser (likely `feedparser`); justifies a new runtime dep.
  3. `device_profiles` cleanup shape (full remove vs. trim to `remarkable_folder`-only).
  4. Image fetch policy: timeout, size cap, MIME allowlist, behavior on duplicates within an article.
  5. EPUB validation depth in `_validate_epub` (mimetype only, or container + opf parse).
- **Carry-forward research items**: §4 list above. Each should be settled before tasks are written.
- **Cross-spec coordination**: this spec supersedes `device-profiles` Req 6 (multi-output) and Req 7 (per-profile reMarkable folder, given single-output collapse). Design should annotate the supersession explicitly so future readers do not re-derive multi-output semantics.

---

## Design Synthesis Outcomes (2026-04-26)

Decisions taken during `/kiro-spec-design` synthesis:

### Generalization
- `_fetch_with_retry` and `_robots_allows` apply to three sites (RSS feed, article URL, image URL). Generalized into a standalone `http` module with the same signatures rather than threading three separate fetchers; the interface is uniform across sites without forcing an unnecessary abstraction.

### Build vs. Adopt
- **EPUB assembly**: adopt `ebooklib`. Mature, EPUB 3 + nav + NCX, pure-Python, available on PyPI and trivially pip-installable on the Pi. Building EPUB 3 by hand is non-trivial (correct mimetype-first ZIP, container.xml, content.opf manifest/spine, nav.xhtml, identifier handling) and offers no benefit here.
- **RSS parsing**: adopt `feedparser`. Standard answer in Python; handles RSS 2.0, Atom, encoding quirks, and the namespace zoo. Stdlib `xml.etree.ElementTree` would require re-implementing what feedparser already gets right.
- **Article extraction**: adopt the already-pinned `readability-lxml==0.8.1`. Pin reason already documented in `pyproject.toml`; no point swapping during this spec.
- **Build (not adopt)**: the EPUB validator (`_validate_epub`). Spec-level checks (mimetype first, container.xml present) are a few lines of stdlib `zipfile` and tightly coupled to the existing `BuildError` cleanup pattern. A heavier validator (e.g., `epubcheck`) is a JVM tool — wrong fit for the Pi runtime.

### Simplification
- **Drop `profiles.py` entirely** rather than trimming it. With single-output collapse and reflowable EPUB, every `DeviceProfile` field is either obsolete (page dimensions, margin, font size, color filter) or replaceable by an existing top-level config key (`remarkable_folder`). Keeping a one-field profile abstraction would be misleading.
- **Drop the file:// rewrite trick**. With `feedparser` parsing in-process from bytes, there is no need to write feed XML to a tempdir and rewrite story config. Removes the one piece of `Builder._prepare_stories` complexity that existed solely for goosepaper.
- **Drop `subprocess_timeout_s`**. It bounded the goosepaper subprocess; with goosepaper gone the only subprocess left is `rmapi`, which already has its own timeout via `Uploader`.
- **No new "ImageEmbedder" module**. Image fetch + rewrite is a single loop inside `epub.assemble`; promoting it to a sibling module would be premature decomposition for ~50 lines of code.

### Open Questions Carried Forward (none blocking)
- Image MIME type detection: design uses `Content-Type` response header with URL-extension fallback. If real-world feeds reveal frequent missing/wrong Content-Type, a `python-magic`-style sniff could be added later — out of scope here.
- ebooklib version pin: design defers to "latest stable" until tasks are written; pin will be set when `pyproject.toml` is edited.
- rmapi `.epub` upload: reMarkable supports EPUB natively and rmapi has shipped EPUB upload support for years; design assumes `Uploader` works unchanged. If the first end-to-end run reveals otherwise, fall back is small (likely a Content-Type or naming hint to rmapi).
