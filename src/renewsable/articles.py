"""Article collection: turn validated RSS stories into ``Article`` records.

Design reference: ``.kiro/specs/epub-output/design.md`` →
"Components and Interfaces" → "Content" → ``articles`` module, and the
"Stories Schema (closed set, validated by Config.load)" block.

Requirements covered:

- 3.1 — for each RSS entry, fetch the linked article URL and extract its
  main readable content.
- 3.2 — apply the same robots.txt check and retry/backoff policy used for
  RSS feed fetches (delegated to ``renewsable.http``).
- 3.3 — fall back to the RSS entry's own description/content on extraction
  failure.
- 3.4 — drop the entry when both extraction and RSS fallback are unusable;
  never raise per-entry; the build continues.

Dependency direction (design.md → "Architecture" → "Dependency direction"):
``articles`` imports from ``http``, ``errors``, ``config`` (types only).
It must never import from ``builder``, ``epub``, or ``cli``.

Test seams
----------
``_http`` and ``feedparser`` are imported as module-level attributes so
tests can ``monkeypatch.setattr(articles._http, "fetch_with_retry", fake)``
and ``monkeypatch.setattr(articles.feedparser, "parse", fake)``. This
mirrors the convention in ``renewsable.builder`` and ``renewsable.http``.
"""

from __future__ import annotations

import logging
import urllib.parse
from dataclasses import dataclass

import feedparser  # type: ignore[import-untyped]
import lxml.html
from readability import Document  # type: ignore[import-untyped]

try:  # lxml >= 5 split clean out into a separate package; fall back gracefully.
    from lxml.html.clean import Cleaner
except ImportError:  # pragma: no cover - exercised only on lxml>=5 without lxml_html_clean
    from lxml_html_clean import Cleaner  # type: ignore[import-not-found]

from . import http as _http


__all__ = ["Article", "collect"]


logger = logging.getLogger(__name__)


# Sanitizer configuration per design.md "Implementation Notes":
#   scripts=True, javascript=True, style=True, links=False (preserve <a>),
#   meta=True, page_structure=False, embedded=True, frames=True, forms=True.
# Images are preserved (Req 4.1).
_CLEANER = Cleaner(
    scripts=True,
    javascript=True,
    style=True,
    links=False,
    meta=True,
    page_structure=False,
    embedded=True,
    frames=True,
    forms=True,
)


@dataclass(frozen=True)
class Article:
    """A single ready-to-publish article.

    ``html`` is sanitized fragment HTML whose ``<img src>`` and ``<a href>``
    values are absolute http(s) URLs.
    """

    title: str
    html: str
    source_url: str


def collect(
    stories: list[dict],
    *,
    ua: str,
    retries: int,
    backoff_s: float,
    robots_cache: _http.RobotsCache,
) -> list[Article]:
    """Build the per-run list of ``Article`` records from validated stories.

    Per-entry exceptions never escape: every failure is logged at WARNING
    and the next entry continues. Only systemic errors (e.g., readability
    raising at import time) propagate.
    """
    out: list[Article] = []

    for story in stories:
        try:
            provider = story.get("provider")
            cfg = story.get("config") or {}
        except AttributeError:
            logger.warning("skipping malformed story entry: %r", story)
            continue

        if provider != "rss":
            # Per design, Config.load rejects non-rss providers; this branch
            # is the defensive belt-and-braces called out in the task body.
            logger.warning(
                "skipping story with unsupported provider %r (expected 'rss')",
                provider,
            )
            continue

        rss_path = cfg.get("rss_path")
        if not isinstance(rss_path, str) or not rss_path:
            logger.warning("skipping story with missing/invalid rss_path: %r", cfg)
            continue

        # Robots check on the feed URL itself.
        if not _http.robots_allows(rss_path, cache=robots_cache, ua=ua):
            logger.warning("robots.txt disallows feed %s; skipping source", rss_path)
            continue

        try:
            feed_bytes = _http.fetch_with_retry(
                rss_path, ua=ua, retries=retries, backoff_s=backoff_s
            )
        except Exception as exc:
            logger.warning("feed fetch failed for %s: %s", rss_path, exc)
            continue

        try:
            feed = feedparser.parse(feed_bytes)
        except Exception as exc:  # pragma: no cover - feedparser is very tolerant
            logger.warning("feed parse failed for %s: %s", rss_path, exc)
            continue

        # feedparser surfaces fatal parse problems via `bozo_exception`. A
        # non-fatal `bozo` flag (e.g., character-encoding warnings) is
        # routine and should not stop us.
        bozo_exc = getattr(feed, "bozo_exception", None)
        entries = list(getattr(feed, "entries", []) or [])
        if bozo_exc is not None and not entries:
            logger.warning("feed %s reported fatal parse error: %s", rss_path, bozo_exc)
            continue
        if not entries:
            logger.info("feed %s has no entries; nothing to collect", rss_path)
            continue

        limit_raw = cfg.get("limit")
        if isinstance(limit_raw, int) and limit_raw > 0:
            entries = entries[:limit_raw]

        for entry in entries:
            article = _build_article(
                entry,
                ua=ua,
                retries=retries,
                backoff_s=backoff_s,
                robots_cache=robots_cache,
            )
            if article is not None:
                out.append(article)

    return out


# ---------------------------------------------------------------------------
# Per-entry logic
# ---------------------------------------------------------------------------


def _build_article(
    entry: object,
    *,
    ua: str,
    retries: int,
    backoff_s: float,
    robots_cache: _http.RobotsCache,
) -> Article | None:
    """Produce a single ``Article`` from a feedparser entry.

    Returns ``None`` if the entry must be dropped. Never raises.
    """
    try:
        link = _entry_get(entry, "link")
        title = _entry_get(entry, "title") or "(untitled)"
        if not isinstance(link, str) or not link:
            logger.warning("dropping entry without link: title=%r", title)
            return None

        if not _http.robots_allows(link, cache=robots_cache, ua=ua):
            logger.warning("robots.txt disallows article %s; dropping", link)
            return None

        body_html = _extract_body(entry, link, ua=ua, retries=retries, backoff_s=backoff_s)
        if not body_html:
            logger.warning("no usable body for article %s; dropping", link)
            return None

        sanitized = _sanitize_and_resolve(body_html, link)
        if not sanitized:
            logger.warning("sanitized body is empty for %s; dropping", link)
            return None

        clean_title = (title or "").strip() or "(untitled)"
        return Article(title=clean_title, html=sanitized, source_url=link)
    except Exception as exc:  # belt-and-braces: per-entry never raises out
        logger.warning(
            "unexpected error processing entry %r: %s",
            _entry_get(entry, "link") or _entry_get(entry, "title"),
            exc,
        )
        return None


def _extract_body(
    entry: object,
    link: str,
    *,
    ua: str,
    retries: int,
    backoff_s: float,
) -> str:
    """Try the article URL via readability; fall back to RSS desc/content."""
    try:
        raw = _http.fetch_with_retry(link, ua=ua, retries=retries, backoff_s=backoff_s)
        html_text = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
        body = Document(html_text).summary()
        if body and _has_text(body):
            return body
        logger.info("readability returned empty body for %s; trying RSS fallback", link)
    except Exception as exc:
        logger.info("article fetch/extract failed for %s (%s); trying RSS fallback", link, exc)

    return _rss_fallback_html(entry)


def _rss_fallback_html(entry: object) -> str:
    """Pick the best available RSS-side body field."""
    # `summary_detail.value` is feedparser's normalized rendering; fall back
    # to `summary` then to `content[0].value`.
    sd = _entry_get(entry, "summary_detail")
    if isinstance(sd, dict):
        v = sd.get("value")
        if isinstance(v, str) and v.strip():
            return v
    summary = _entry_get(entry, "summary")
    if isinstance(summary, str) and summary.strip():
        return summary
    content = _entry_get(entry, "content")
    if isinstance(content, list) and content:
        first = content[0]
        v = first.get("value") if isinstance(first, dict) else getattr(first, "value", None)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def _entry_get(entry: object, key: str) -> object:
    """feedparser entries support attr- and item-access; tolerate either."""
    if isinstance(entry, dict):
        return entry.get(key)
    return getattr(entry, key, None)


# ---------------------------------------------------------------------------
# Sanitization + URL resolution
# ---------------------------------------------------------------------------


def _sanitize_and_resolve(body_html: str, base_url: str) -> str:
    """Clean ``body_html`` and rewrite img/a URLs to absolute http(s) only."""
    try:
        # `fragment_fromstring(..., create_parent=True)` lets us parse a
        # fragment without forcing a single root element.
        tree = lxml.html.fragment_fromstring(body_html, create_parent="div")
    except Exception as exc:
        logger.info("lxml could not parse body for %s: %s", base_url, exc)
        return ""

    try:
        _CLEANER(tree)
    except Exception as exc:  # pragma: no cover - cleaner is permissive
        logger.info("Cleaner failed for %s: %s", base_url, exc)
        return ""

    # Resolve <img src> and <a href>.
    for img in tree.iter("img"):
        src = img.get("src")
        if src is None:
            continue
        resolved = _resolve_url(base_url, src)
        if resolved is None:
            del img.attrib["src"]
        else:
            img.set("src", resolved)

    for a in tree.iter("a"):
        href = a.get("href")
        if href is None:
            continue
        resolved = _resolve_url(base_url, href)
        if resolved is None:
            del a.attrib["href"]
        else:
            a.set("href", resolved)

    serialized = lxml.html.tostring(tree, encoding="unicode")
    if not _has_text(serialized):
        return ""
    return serialized


def _resolve_url(base: str, value: str) -> str | None:
    """Return the absolute http(s) URL or ``None`` if the scheme is unsafe."""
    try:
        joined = urllib.parse.urljoin(base, value)
    except Exception:
        return None
    parsed = urllib.parse.urlparse(joined)
    if parsed.scheme not in ("http", "https"):
        return None
    if not parsed.netloc:
        return None
    return joined


def _has_text(html: str) -> bool:
    """True iff stripping tags leaves any non-whitespace text."""
    try:
        fragment = lxml.html.fragment_fromstring(html, create_parent="div")
    except Exception:
        return bool(html.strip())
    text = fragment.text_content() if hasattr(fragment, "text_content") else ""
    return bool(text and text.strip())
