"""Shared HTTP and robots primitives for renewsable.

Design reference: ``.kiro/specs/epub-output/design.md`` →
"Components and Interfaces" → "Networking" → ``http`` module.

Requirements covered:
- 3.2 — renewsable shall apply the same robots.txt check and retry/backoff
  policy already used for RSS feed fetches. This module is the *single*
  source of those primitives so that the future article-fetch and image-
  fetch call sites pick up identical behavior.

Behavior is lifted verbatim from ``Builder._fetch_with_retry`` and
``Builder._robots_allows`` / ``_load_robots`` — the existing RSS pre-fetch
path will continue using its own copies until task 3.2 of the epub-output
spec swings it onto these helpers.

Subprocess / time / network seams
---------------------------------
``urllib_request`` and ``_time`` are bound as module-level attributes so
tests can monkeypatch them in one place
(``monkeypatch.setattr(http.urllib_request, "urlopen", ...)`` /
``monkeypatch.setattr(http._time, "sleep", ...)``). That mirrors the
pattern used in ``renewsable.builder`` and ``renewsable.scheduler``.
"""

from __future__ import annotations

import logging
import time as _time  # noqa: F401  (module-level alias kept for tests)
import urllib.parse
import urllib.request as urllib_request  # noqa: F401  (module-level alias kept for tests)
import urllib.robotparser


__all__ = ["RobotsCache", "fetch_with_retry", "robots_allows"]


logger = logging.getLogger(__name__)


RobotsCache = dict[str, urllib.robotparser.RobotFileParser | None]


def fetch_with_retry(
    url: str,
    *,
    ua: str,
    retries: int,
    backoff_s: float,
    timeout_s: float = 30.0,
) -> bytes:
    """GET ``url`` with up to ``retries`` attempts.

    Returns the body as bytes on first success. Raises the *last*
    exception after exhausting retries. Between attempts we sleep
    ``backoff_s * 2**i`` seconds (``i`` starts at 0), so a default of
    1.0s gives waits of 1s, 2s, 4s, … — the "small, bounded" schedule
    Req 9.2 of the daily-paper spec calls for, reused here for Req 3.2
    of epub-output.
    """
    retries = max(1, retries)
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib_request.Request(url, headers={"User-Agent": ua})
            with urllib_request.urlopen(req, timeout=timeout_s) as resp:
                return resp.read()
        except Exception as exc:
            last_exc = exc
            logger.info(
                "feed fetch attempt %d/%d failed for %s: %s",
                attempt + 1,
                retries,
                url,
                exc,
            )
            if attempt + 1 < retries:
                _time.sleep(backoff_s * (2**attempt))
    # Retries exhausted.
    assert last_exc is not None  # loop ran at least once
    raise last_exc


def robots_allows(
    url: str,
    *,
    cache: RobotsCache,
    ua: str,
    timeout_s: float = 30.0,
) -> bool:
    """Return True iff ``robots.txt`` at ``url``'s host allows ``ua``.

    The cache key is ``<scheme>://<host>``. A missing / unreachable
    robots.txt is treated as fully permissive — matches stdlib
    :class:`urllib.robotparser.RobotFileParser` semantics and the
    common-web convention. When a host's robots.txt is unparseable or
    explicitly disallows ``*``, we drop every URL on that host.
    """
    parsed = urllib.parse.urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        # Relative or otherwise malformed URL — let the fetch layer
        # raise the real error; no point asking robots about it.
        return True
    # ``file://`` URLs are not part of the robots.txt ecosystem.
    if parsed.scheme not in ("http", "https"):
        return True

    key = f"{parsed.scheme}://{parsed.netloc}"
    if key not in cache:
        cache[key] = _load_robots(key, ua=ua, timeout_s=timeout_s)

    parser = cache[key]
    if parser is None:
        # Failed to fetch robots.txt — fail-open.
        return True
    return parser.can_fetch(ua, url)


def _load_robots(
    origin: str,
    *,
    ua: str,
    timeout_s: float,
) -> urllib.robotparser.RobotFileParser | None:
    """Fetch and parse ``<origin>/robots.txt`` in a single attempt.

    We deliberately do **not** retry the robots fetch: the cost of a
    transient failure on robots.txt is a single run where a site
    happens to be allowed; the cost of retrying is multiplying every
    Pi-side run by the robots-fetch latency of each configured host.

    Returns ``None`` on any error (network, non-200, decoding,
    parsing). ``None`` is interpreted as "fail-open" by the caller.
    """
    robots_url = f"{origin}/robots.txt"
    try:
        req = urllib_request.Request(robots_url, headers={"User-Agent": ua})
        with urllib_request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read()
    except Exception as exc:
        logger.debug("could not fetch %s (%s); fail-open", robots_url, exc)
        return None

    parser = urllib.robotparser.RobotFileParser()
    try:
        text = raw.decode("utf-8", errors="replace")
        parser.parse(text.splitlines())
    except Exception as exc:  # pragma: no cover - parser is very tolerant
        logger.debug("could not parse %s (%s); fail-open", robots_url, exc)
        return None
    return parser
