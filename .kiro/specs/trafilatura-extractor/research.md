# Gap Analysis — `trafilatura-extractor`

Generated 2026-05-01. Brownfield analysis to inform the design phase. Information-only; no implementation decisions made here.

## 1. Current State Investigation

### Single swap point
- `src/renewsable/articles.py:240-259` — `_extract_body(entry, link, *, ua, retries, backoff_s) -> str`. The only article-extraction call site:
  ```python
  raw = _http.fetch_with_retry(link, ua=ua, retries=retries, backoff_s=backoff_s)
  html_text = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
  body = Document(html_text).summary()
  if body and _has_text(body):
      return body
  logger.info("readability returned empty body for %s; trying RSS fallback", link)
  ...
  return _rss_fallback_html(entry)
  ```
- Imports at top of `articles.py:38`: `from readability import Document  # type: ignore[import-untyped]`.
- `_has_text` (`articles.py:356-363`) uses `lxml.html.fragment_fromstring` and `text_content()` to determine "non-empty". This is the exact "usable content" check we will keep across both extractors.

### Surrounding pipeline (must be preserved)
- `_build_article` (`articles.py:196-237`): per-entry orchestrator wraps `_extract_body` in try/except so per-entry failures never raise. Logs at WARNING.
- `_sanitize_and_resolve` (`articles.py:295-339`): runs Cleaner + strips `_LEGACY_PRESENTATIONAL_ATTRS` (GH #1 fix at lines 71-88, applied at 311-313) + resolves `<img src>` and `<a href>` to absolute http(s) via urljoin (lines 316-334) + drops non-http(s) schemes. This stage runs **after** `_extract_body` — invariant chain therefore holds regardless of which extractor produced the body.

### Tests and fixtures
- `tests/test_articles.py` (492 LOC). Existing tests do not mock the extractor — they pass real article-shaped HTML through real `Document(...).summary()` and assert substring presence in `Article.html`. No extractor-specific mocks. Trafilatura should handle the same simple `<article><p>…</p></article>` test HTMLs identically.
- The happy-path test (`test_happy_path_extracts_readability_body` at line 102) is named after readability-lxml — should be renamed once trafilatura is primary, but the body is library-agnostic.
- The "drop when both unusable" test (line 166) feeds a `ConnectionError` to the article URL fetch; that path bypasses the extractor entirely, so it stays valid.
- No canned BBC-shaped Next.js fixture exists yet. A new test for Req 4 will need one.

### Existing dependencies (`pyproject.toml`)
- `readability-lxml==0.8.1` — pinned because 0.8.4.1 regressed the encoding path.
- `lxml_html_clean` — already declared (transitive shim).
- `feedparser>=6.0`, `ebooklib>=0.18`, `click>=8.1` — unrelated, untouched.
- `trafilatura` is **NOT** currently installed (`.venv/bin/pip show trafilatura` → `Package(s) not found: trafilatura`).

## 2. External Dependency Research — `trafilatura`

Verified via PyPI / docs / GitHub release notes (research subagent, 2026-05-01):

| Aspect | Finding |
|---|---|
| Latest stable | **2.0.0** (Dec 2024) |
| Top-level call | `trafilatura.extract(html, output_format='html', include_images=True, include_links=True, url=link, ...)` |
| Failure return | **`None`** — never raises by default. Clean fallback signal. |
| License | Apache 2.0 (≥ 1.8.0). Compatible with our MIT. |
| Python support | 3.8+, explicitly tested on 3.11/3.12/3.13. |
| `lxml>=5` compatibility | Native — no separate workaround needed. trafilatura already handles the `lxml.html.clean` split. |
| Runtime deps | `lxml>=5.3.0`, `urllib3>=1.26,<3`, `certifi`, `charset_normalizer>=3.4`, `courlan>=1.3.2`, `htmldate>=1.9.2`, `justext>=3.0.1`. All lightweight, no native ML, all pure-Python or already-transitive. |
| Heavy / platform-specific | None at the required tier. (Optional `[all]` extra adds brotli, zstandard, cchardet, py3langid, pycurl — we do **not** need any of them.) |
| Performance | 10–100 ms per article on modern hardware; sub-10 ms with `fast=True`. Comfortably within Pi-class budget for ~30 articles per build. |
| Accuracy | Benchmark F1 ≈ 0.914 (vs readability-lxml ≈ 0.826). The library's own internal cascade also uses readability-lxml + justext as fallbacks, so trafilatura is effectively a superset. |
| Known gotchas | None blocking. `output_format='html'` preserves links and images with `include_links=True, include_images=True`. The `url=` kwarg helps resolve relative paths internally — useful as defense-in-depth even though our `_sanitize_and_resolve` already does urljoin. |

## 3. Requirement-to-Asset Map

| Req | Need | Existing asset | Gap |
|---|---|---|---|
| 1 trafilatura primary | Call trafilatura.extract; non-empty result short-circuits the chain | `_extract_body` is the single swap point; `_has_text` is the existing "usable" check | **Missing** — `trafilatura` not yet a dependency |
| 1.3 Preserve images/links | `include_images=True, include_links=True, output_format='html'` | trafilatura supports it natively | None — call-site config |
| 2 readability fallback | Existing `Document(...).summary()` path stays as secondary | Already in `_extract_body`; readability-lxml stays pinned | None — restructure call order only |
| 3 RSS-summary fallback | `_rss_fallback_html` at `articles.py:262-280` | Reusable as-is | None |
| 4 BBC full-body output | trafilatura's output for Next.js / React pages | trafilatura has the right heuristic | Needs a new test fixture and assertion |
| 5 Non-regression on working sources | Existing tests already lock substring assertions on real HTML | Tests run with the real extractor | Re-run suite under trafilatura; check assertions hold; rename misleading test names |
| 6 Per-entry resilience | `_build_article` try/except is unchanged | Reusable | None |
| 7 Output invariants | `_sanitize_and_resolve` runs after `_extract_body` regardless of extractor | Reusable as-is | None |

### Complexity signals
- **External integrations**: one new library, no service interactions. No network, no auth.
- **Algorithmic logic**: zero new logic; just call routing.
- **Workflow changes**: zero — same orchestrator, same per-entry contract.
- **Migration**: a `pip install -e .` is required after pull; otherwise day-one change.

## 4. Implementation Approach Options

### Option A — Drop-in primary swap (recommended)

**Shape**: In `articles.py`:
1. Add `import trafilatura  # type: ignore[import-untyped]` at module top alongside the readability import. Keep both for the fallback path.
2. Modify `_extract_body` to call `trafilatura.extract(html_text, output_format='html', include_images=True, include_links=True, url=link)` first; if result is `None` or `_has_text(result)` is false, fall through to the existing `Document(html_text).summary()` path; if that is also empty, fall through to `_rss_fallback_html(entry)` as today.
3. Wrap the trafilatura call in try/except. Per Req 2.1, exceptions from trafilatura must fall through to readability. This matches the existing readability try/except already in place.
4. Add `trafilatura>=2.0.0` to `pyproject.toml` runtime deps.
5. Update `tests/test_articles.py`: rename `test_happy_path_extracts_readability_body` → `test_happy_path_extracts_via_trafilatura`; add `test_falls_back_to_readability_when_trafilatura_returns_none`; add `test_bbc_style_nextjs_html_extracts_multi_paragraph_body` with a canned Next.js-shaped fixture.

**Trade-offs**:
- ✅ Single-function change. Smallest possible footprint matching the design intent.
- ✅ Both fallbacks remain. The reader and operator never see "stub" extractions silently.
- ✅ Reuses the existing `_has_text` "usable" check on both extractors — single definition of "useful content".
- ❌ Two extractors to maintain in deps. (Acceptable: trafilatura already pulls a similar dependency surface, and readability is small.)

### Option B — Replace readability entirely; only trafilatura → RSS

**Shape**: Remove the readability import and the `Document` fallback; chain becomes trafilatura → RSS only.

**Trade-offs**:
- ✅ Smaller dep set — `readability-lxml` and its 0.8.1 pin go away.
- ❌ Removes a safety net: if trafilatura ever silently misfires (returns `None` on a page that readability would have handled), we drop straight to a one-line RSS summary. Net regression risk for sources that work today.
- ❌ Contradicts the requirements (Req 2 explicitly mandates readability as fallback when trafilatura is unsuccessful).

### Option C — Configurable extractor

**Shape**: A `Config.extractor` field selects between `"trafilatura"`, `"readability"`, or `"auto"`.

**Trade-offs**:
- ❌ Out of requirements scope (explicitly excluded in Boundary). Speculative configurability for a personal project. Skip.

## 5. Effort & Risk

- **Effort: S (1-2 days)**. Single-function swap, one new dep, ~3 new/renamed tests. No architectural change. Existing pipeline absorbs the new extractor with zero downstream coordination.
- **Risk: Low**. trafilatura is mature (2.0.0 stable, MIT-compatible), uses the same `lxml` family the project already runs on, and our outer try/except + readability fallback + RSS fallback chain is robust to any edge cases. The only operator-visible disruption is the `pip install -e .` step on next deploy — already documented in the README's deployment workflow.

## 6. Recommendations for Design Phase

- **Preferred approach**: Option A.
- **Key decisions to settle in design**:
  1. Module-level seam for monkeypatching: `from . import trafilatura as _trafilatura` won't work (name shadowing). Likely shape: keep `import trafilatura` at module scope and let tests `monkeypatch.setattr(articles_mod.trafilatura, "extract", fake_extract)`. Confirm this is the convention.
  2. Whether to pass `url=link` to `trafilatura.extract`. Recommendation: yes — defense-in-depth even though our urljoin pass also runs later.
  3. Whether to use `fast=True`. Recommendation: no — accuracy matters more than the few hundred ms saved across a daily build.
  4. Trafilatura version pin. Recommendation: `trafilatura>=2.0.0,<3` (next-major guard).
  5. Test fixture for Req 4: hand-craft a minimal Next.js-shaped HTML (one `<p class="sc-...">` lead + multiple deeper styled-component paragraphs in nested divs) sufficient to demonstrate the multi-paragraph extraction. Avoid shipping a real BBC HTML capture for license reasons.
- **Carry-forward research items**: none blocking. The library is well-understood; remaining decisions are call-site ergonomics, listed above.
- **Cross-spec coordination**: none. This change lives entirely inside the `articles` module and `pyproject.toml`. No other spec needs revalidation.

---

## Design Synthesis Outcomes (2026-05-01)

Decisions taken during `/kiro-spec-design` synthesis:

### Generalization
- Single call site, single function — nothing to generalize across components. The `_has_text` helper is already the single "usable content" predicate; both extractor outputs go through it. No new abstraction needed.

### Build vs. Adopt
- **Adopt `trafilatura>=2.0.0,<3`**: actively maintained, MIT-compatible (Apache 2.0), drop-in API, returns `None` on failure (no exception path to design around), already lxml>=5 native.
- **Retain `readability-lxml==0.8.1`**: the existing pin is kept solely as the secondary fallback. Removing it would force a binary "trafilatura or RSS" choice — Req 2 explicitly mandates the intermediate fallback.

### Simplification
- **No new helper module**: The change is small enough to live inside `articles.py`. A `_run_trafilatura` private helper is optional; inline try/except is acceptable if it doesn't make `_extract_body` hard to read.
- **No configurability**: Operator cannot select an extractor. Speculative; explicitly out of scope.
- **Keep one definition of "usable"**: `_has_text` applies to both extractors. Single threshold, single bug class to reason about.
- **Inline the BBC test fixture**: a hand-crafted Next.js-shaped HTML literal in the test file. Avoids licensing concerns from real BBC HTML and keeps the fixture self-contained.

### Open Questions Carried Forward
- None blocking. All five Phase-2 design decisions from the gap analysis have been settled in this design (version pin, call signature, `fast=False`, `url=link`, and inline test fixture).
