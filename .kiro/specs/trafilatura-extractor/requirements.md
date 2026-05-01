# Requirements Document

## Introduction

Renewsable currently uses `readability-lxml` as the sole article-content extractor in `articles._extract_body`. On Next.js / React server-rendered news sites (BBC News in particular), the readability heuristic returns only a one-paragraph stub from a multi-page article, and because the stub is non-empty the existing RSS-summary fallback never triggers. The reader sees a single-sentence chapter for those sources while other feeds (telex.hu, NYT, Guardian, Economist) extract cleanly. This spec replaces the primary extractor with `trafilatura`, keeps `readability-lxml` as a secondary fallback, and preserves the RSS-summary fallback as the last resort, so that BBC and similarly-rendered articles produce full-body chapters in the daily EPUB. Tracks GitHub issue [#8](https://github.com/kbence/renewsable/issues/8).

## Boundary Context

- **In scope**:
  - Article-body extraction inside `articles._extract_body` (the only extractor call site).
  - Adding `trafilatura` as a runtime dependency.
  - Ordered fallback chain: trafilatura → readability-lxml → RSS summary/content.
  - Test coverage updates that lock in the new chain and the BBC-style extraction outcome.
- **Out of scope**:
  - Switching the RSS parser (`feedparser` stays).
  - Per-site bespoke extractors or feed-specific configuration.
  - User-configurable extractor selection (operator cannot opt out of trafilatura).
  - Changes to the post-extraction pipeline: sanitization (Cleaner + legacy-attr stripping), URL resolution, image embedding, EPUB assembly all remain unchanged.
  - Removing `readability-lxml` from the project (it stays as the secondary fallback).
- **Adjacent expectations**:
  - A new runtime dependency (`trafilatura`) is introduced. Operators upgrading from a previous install must run `pip install -e .` after pulling for the daily run to use the new extractor.
  - The `Article.html` invariants from the existing spec (absolute http(s) URLs only; `data:`/`javascript:` schemes dropped; legacy presentational attributes stripped per GH #1) continue to hold for content produced by the new extractor and by either fallback.

## Requirements

### Requirement 1: Trafilatura as the primary article extractor

**Objective:** As the operator, I want renewsable to use `trafilatura` as its first-choice article extractor, so that Next.js / React server-rendered news pages produce full multi-paragraph content in the daily EPUB.

#### Acceptance Criteria

1. When renewsable fetches an article URL successfully, the renewsable build shall pass the response HTML to `trafilatura` first to extract the article's main content.
2. When `trafilatura` returns content whose post-sanitization text is non-empty, the renewsable build shall use that content as the article body and shall not invoke any other extractor for that entry.
3. The renewsable build shall preserve images and inline links in the extracted content where the source page contains them, so that the existing image-embedding and link-resolution stages have material to work with.

### Requirement 2: Readability fallback when trafilatura yields nothing usable

**Objective:** As the operator, I want a secondary extractor to run when `trafilatura` produces no usable content, so that pages where `trafilatura` happens to misfire still get a content-extraction attempt before falling all the way back to the RSS summary.

#### Acceptance Criteria

1. If `trafilatura` raises an exception during extraction, then the renewsable build shall fall back to `readability-lxml` for that entry.
2. If `trafilatura` returns content whose post-sanitization text is empty or whitespace-only, then the renewsable build shall fall back to `readability-lxml` for that entry.
3. When `readability-lxml` returns content whose post-sanitization text is non-empty, the renewsable build shall use that content as the article body.

### Requirement 3: RSS-summary fallback when both extractors yield nothing usable

**Objective:** As the operator, I want the existing RSS-summary fallback to remain the final safety net, so that an entry whose linked page produces nothing extractable still appears in the EPUB rather than being silently dropped because of a structural quirk in the source page.

#### Acceptance Criteria

1. If both `trafilatura` and `readability-lxml` yield no usable content for an entry, then the renewsable build shall use the entry's RSS `summary`/`content` field as the article body, exactly as today.
2. If the RSS `summary`/`content` field is also empty or unusable, then the renewsable build shall drop that entry, exactly as today.

### Requirement 4: BBC News articles render with full-body content

**Objective:** As a reader, I want BBC News articles in the daily EPUB to contain the full article body, not a one-paragraph excerpt, so that the digest is readable end-to-end on the reMarkable.

#### Acceptance Criteria

1. When the renewsable build processes an article from the BBC News RSS feed (`feeds.bbci.co.uk`) whose linked page contains multi-paragraph article text, the resulting chapter in the produced EPUB shall contain that multi-paragraph article text rather than a single styled-component paragraph.
2. The renewsable build shall pass the existing per-entry resilience invariants: a BBC entry whose page is unreachable, malformed, or whose extractors all yield nothing shall not raise out of the build, and shall fall through to the RSS summary or be dropped per Requirements 2 and 3.

### Requirement 5: Non-regression on previously-working sources

**Objective:** As a reader, I want sources that already extracted well under `readability-lxml` (telex.hu, NYT, Guardian, Economist, Hacker News) to continue extracting at least as well after the swap, so that fixing BBC does not break the rest of the digest.

#### Acceptance Criteria

1. When the renewsable build processes an article whose linked page extracted to multi-paragraph content under the previous extractor, the resulting chapter in the produced EPUB shall continue to contain comparable multi-paragraph content.
2. The renewsable build shall preserve the title, source-URL, and Article.html structural invariants for every entry across all configured sources.

### Requirement 6: Existing per-entry resilience preserved

**Objective:** As the operator, I want the existing per-entry resilience guarantees to keep holding under the new extractor chain, so that one bad page never kills the build.

#### Acceptance Criteria

1. The renewsable build shall not raise out of `articles.collect` because an individual entry's `trafilatura` invocation, `readability-lxml` invocation, or RSS-fallback retrieval failed.
2. While processing a list of entries, when one entry's extraction chain fails entirely, the renewsable build shall continue with the next entry and shall log the failure at WARNING with the entry URL and the reason.

### Requirement 7: Article output invariants preserved

**Objective:** As the operator, I want the `Article.html` shape contract from the existing spec to keep holding under the new extractor, so that downstream stages (image embedding, link rendering, EPUB assembly) keep working without coordinated changes.

#### Acceptance Criteria

1. The renewsable build shall continue to produce `Article.html` whose `<img src>` and `<a href>` values are absolute `http`/`https` URLs, regardless of which extractor produced the content.
2. The renewsable build shall continue to drop `<img src>` and `<a href>` values whose resolved scheme is not `http` or `https` (e.g., `data:`, `javascript:`), regardless of which extractor produced the content.
3. The renewsable build shall continue to strip legacy HTML 4 presentational attributes (`align`, `valign`, `hspace`, `vspace`, `border`, `bgcolor`, `cellpadding`, `cellspacing`) from the sanitized output, regardless of which extractor produced the content.
