# Requirements Document

## Project Description (Input)

Tracks GitHub issue [#8](https://github.com/kbence/renewsable/issues/8). The renewsable operator currently sees BBC News articles render as one-paragraph stubs in the daily EPUB while other sources (telex.hu, NYT, Guardian, Economist) extract cleanly. The cause is `readability-lxml`'s heuristic failing on Next.js / React server-rendered pages: `Document(html).summary()` returns ~300 bytes (a single styled-component paragraph) on a 200+ KB BBC page, and that non-empty stub bypasses the RSS-summary fallback. This spec replaces `readability-lxml` with `trafilatura` as the primary article-content extractor in `src/renewsable/articles.py`, falling back to `readability-lxml` only when `trafilatura` returns nothing usable, and ultimately to the RSS entry's own summary/content as today.

## Requirements
<!-- Will be generated in /kiro-spec-requirements phase -->
