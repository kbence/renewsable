"""Tests for ``renewsable.articles`` — article collection from RSS stories.

Spec coverage:

- requirements.md → 3.1, 3.2, 3.3, 3.4
- design.md → "Components and Interfaces" → "Content" → ``articles`` module
  (Responsibilities, Service Interface, Pre/Postconditions/Invariants,
  Implementation Notes including URL resolution).
- design.md → "Stories Schema (closed set, validated by Config.load)".

Monkeypatch convention mirrors ``test_http.py`` / ``test_builder.py``: the
module under test imports ``http`` as ``_http``; tests patch
``articles_mod._http.fetch_with_retry`` / ``robots_allows`` and
``articles_mod.feedparser.parse``.
"""

from __future__ import annotations

from typing import Any

import pytest

from renewsable import articles as articles_mod
from renewsable.articles import Article, collect


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FeedEntry(dict):
    """feedparser entries support both attribute and item access."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


class _FakeFeed:
    def __init__(self, entries: list[dict], bozo: int = 0) -> None:
        self.entries = [_FeedEntry(e) for e in entries]
        self.bozo = bozo


def _story(rss_path: str, *, limit: int | None = None) -> dict:
    cfg: dict[str, Any] = {"rss_path": rss_path}
    if limit is not None:
        cfg["limit"] = limit
    return {"provider": "rss", "config": cfg}


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fetch_map: dict[str, Any],
    feed_map: dict[bytes, _FakeFeed],
    robots_map: dict[str, bool] | None = None,
) -> list[str]:
    """Replace `_http.fetch_with_retry`, `_http.robots_allows`, and
    `feedparser.parse` with deterministic stand-ins.

    `fetch_map` maps URL -> bytes-payload OR Exception (raised when called).
    `feed_map` maps the bytes from `fetch_map` -> `_FakeFeed`.
    `robots_map` maps URL -> bool (default: every URL allowed).
    Returns a list that records every URL passed to fetch_with_retry, in order.
    """
    fetch_log: list[str] = []

    def fake_fetch(url: str, *, ua: str, retries: int, backoff_s: float, timeout_s: float = 30.0) -> bytes:
        fetch_log.append(url)
        if url not in fetch_map:
            raise AssertionError(f"unexpected fetch: {url}")
        result = fetch_map[url]
        if isinstance(result, Exception):
            raise result
        return result

    def fake_robots(url: str, *, cache: Any, ua: str, timeout_s: float = 30.0) -> bool:
        if robots_map is None:
            return True
        return robots_map.get(url, True)

    def fake_parse(payload: bytes) -> _FakeFeed:
        if payload not in feed_map:
            raise AssertionError(f"unexpected feed payload: {payload!r}")
        return feed_map[payload]

    monkeypatch.setattr(articles_mod._http, "fetch_with_retry", fake_fetch)
    monkeypatch.setattr(articles_mod._http, "robots_allows", fake_robots)
    monkeypatch.setattr(articles_mod.feedparser, "parse", fake_parse)
    return fetch_log


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_happy_path_extracts_via_trafilatura(monkeypatch: pytest.MonkeyPatch) -> None:
    """Req 1.1, 1.2, 5.1: article URL fetched, main body extracted via trafilatura."""
    feed_bytes = b"<rss>fake</rss>"
    article_html = (
        "<html><head><title>Hello</title></head><body>"
        "<header>nav</header>"
        "<article><h1>Hello world</h1>"
        "<p>This is the actual story body that readability should keep "
        "because it is the longest contiguous block of prose on the page "
        "and dominates the candidate scoring algorithm used by readability-lxml.</p>"
        "<p>And another paragraph of real content to push the score up.</p>"
        "</article>"
        "<footer>foot</footer></body></html>"
    )
    rss_url = "https://example.com/feed.xml"
    art_url = "https://example.com/articles/hello"

    feed = _FakeFeed([{"title": "Hello", "link": art_url, "summary": "rss desc"}])
    _install_fakes(
        monkeypatch,
        fetch_map={rss_url: feed_bytes, art_url: article_html.encode()},
        feed_map={feed_bytes: feed},
    )

    out = collect(
        [_story(rss_url)],
        ua="ua",
        retries=1,
        backoff_s=0.01,
        robots_cache={},
    )

    assert len(out) == 1
    a = out[0]
    assert isinstance(a, Article)
    assert a.title == "Hello"
    assert a.source_url == art_url
    # Trafilatura-extracted prose appears in the html.
    assert "actual story body" in a.html
    # Boilerplate stripped.
    assert "<script" not in a.html.lower()


# ---------------------------------------------------------------------------
# Trafilatura fallback chain tests (Req 1, 2, 3, 4)
# ---------------------------------------------------------------------------


def _readability_friendly_html() -> str:
    """Real HTML that readability extracts cleanly so we can prove fall-through."""
    return (
        "<html><head><title>Hello</title></head><body>"
        "<header>nav</header>"
        "<article><h1>Hello world</h1>"
        "<p>This is the actual story body that readability should keep "
        "because it is the longest contiguous block of prose on the page "
        "and dominates the candidate scoring algorithm used by readability-lxml.</p>"
        "<p>And another paragraph of real content to push the score up so that "
        "readability has plenty of material to lock onto when scoring.</p>"
        "</article>"
        "<footer>foot</footer></body></html>"
    )


def test_falls_back_to_readability_when_trafilatura_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Req 2.2, 2.3: trafilatura empty -> readability wins."""
    feed_bytes = b"<rss/>"
    rss_url = "https://example.com/feed.xml"
    art_url = "https://example.com/article"
    feed = _FakeFeed([{"title": "T", "link": art_url, "summary": "rss desc"}])

    _install_fakes(
        monkeypatch,
        fetch_map={rss_url: feed_bytes, art_url: _readability_friendly_html().encode()},
        feed_map={feed_bytes: feed},
    )

    def fake_extract(*args: Any, **kwargs: Any) -> Any:
        return None

    monkeypatch.setattr(articles_mod.trafilatura, "extract", fake_extract)

    out = collect([_story(rss_url)], ua="ua", retries=1, backoff_s=0.01, robots_cache={})

    assert len(out) == 1
    assert "actual story body" in out[0].html


def test_falls_back_to_readability_when_trafilatura_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Req 2.1, 6.1: trafilatura raises -> readability wins; collect doesn't raise."""
    feed_bytes = b"<rss/>"
    rss_url = "https://example.com/feed.xml"
    art_url = "https://example.com/article"
    feed = _FakeFeed([{"title": "T", "link": art_url, "summary": "rss desc"}])

    _install_fakes(
        monkeypatch,
        fetch_map={rss_url: feed_bytes, art_url: _readability_friendly_html().encode()},
        feed_map={feed_bytes: feed},
    )

    def fake_extract(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(articles_mod.trafilatura, "extract", fake_extract)

    out = collect([_story(rss_url)], ua="ua", retries=1, backoff_s=0.01, robots_cache={})

    assert len(out) == 1
    assert "actual story body" in out[0].html


def test_falls_back_to_rss_summary_when_both_extractors_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Req 3.1, 6.1: trafilatura None + readability empty -> RSS summary wins."""
    feed_bytes = b"<rss/>"
    rss_url = "https://example.com/feed.xml"
    art_url = "https://example.com/article"
    feed = _FakeFeed(
        [
            {
                "title": "T",
                "link": art_url,
                "summary": "<p>fallback summary text</p>",
            }
        ]
    )
    _install_fakes(
        monkeypatch,
        fetch_map={rss_url: feed_bytes, art_url: _readability_friendly_html().encode()},
        feed_map={feed_bytes: feed},
    )

    monkeypatch.setattr(articles_mod.trafilatura, "extract", lambda *a, **kw: None)

    class _EmptyDoc:
        def __init__(self, html: str) -> None:
            self._html = html

        def summary(self) -> str:
            return ""

    monkeypatch.setattr(articles_mod, "Document", _EmptyDoc)

    out = collect([_story(rss_url)], ua="ua", retries=1, backoff_s=0.01, robots_cache={})

    assert len(out) == 1
    assert "fallback summary text" in out[0].html


def test_trafilatura_full_document_output_is_normalized_to_fragment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Locks design Issue-1 fix: full <html><body> output normalized to fragment."""
    feed_bytes = b"<rss/>"
    rss_url = "https://example.com/feed.xml"
    art_url = "https://example.com/article"
    feed = _FakeFeed([{"title": "T", "link": art_url, "summary": ""}])
    _install_fakes(
        monkeypatch,
        fetch_map={rss_url: feed_bytes, art_url: b"<html><body>orig</body></html>"},
        feed_map={feed_bytes: feed},
    )

    monkeypatch.setattr(
        articles_mod.trafilatura,
        "extract",
        lambda *a, **kw: "<html><body><p>main content</p></body></html>",
    )

    out = collect([_story(rss_url)], ua="ua", retries=1, backoff_s=0.01, robots_cache={})

    assert len(out) == 1
    html = out[0].html
    assert "main content" in html
    lower = html.lower()
    assert "<html" not in lower, f"expected no <html in {html!r}"
    assert "<body" not in lower, f"expected no <body in {html!r}"


def _bbc_style_nextjs_html() -> tuple[str, list[str]]:
    """Build a BBC/Next.js-shaped page with deep nesting and styled-component <p> siblings.

    No <article>/<main>; ≥4 levels of <div>; ≥5 sibling <p class="sc-XXXXXXXX-N">
    at the deepest level; each paragraph carries ≥30 words of article-like prose.
    """
    paragraphs = [
        (
            "The independent inquiry published its long-awaited findings on Tuesday morning, "
            "concluding that systemic failures across multiple agencies contributed directly "
            "to the outcome and recommending a sweeping overhaul of how interagency referrals "
            "are processed throughout the country going forward and into the next decade entirely."
        ),
        (
            "Local officials reacted to the report with a mixture of public contrition and "
            "private frustration, noting that several of the recommendations had already been "
            "raised internally years earlier but had stalled under successive funding cuts and "
            "staff reorganizations that left frontline teams understaffed and unable to coordinate."
        ),
        (
            "Campaigners welcomed the conclusions but warned that without binding deadlines and "
            "an independent monitoring body the same pattern of slow drift could repeat itself, "
            "pointing to two earlier reviews whose recommendations had been only partially "
            "implemented despite cross-party support and unanimous parliamentary backing at the time."
        ),
        (
            "Academics studying organizational behavior in public services said the findings "
            "echoed a familiar pattern in which clear individual diligence is undermined by "
            "fragmented data systems, inconsistent record-keeping standards, and a culture of "
            "vertical reporting that discourages the kind of horizontal communication needed here."
        ),
        (
            "The minister responsible told parliament that the government accepted the "
            "recommendations in principle and would publish a detailed implementation plan within "
            "twelve weeks, while the opposition pressed for a statutory timetable and a named "
            "senior official accountable for delivery against measurable milestones every quarter."
        ),
    ]
    # Each paragraph wrapped in its own 4-level <div> chain — this is the
    # shape that defeats readability's "single-largest candidate" heuristic
    # because no shared parent encloses all five paragraphs together. The
    # whole block lives inside another wrapper layer (`#__next`) so the
    # deepest <p> is at >=4 div ancestors.
    inner = "".join(
        f'<div class="sc-w-{i}-0"><div class="sc-w-{i}-1">'
        f'<div class="sc-w-{i}-2"><div class="sc-w-{i}-3">'
        f'<p class="sc-abcd1234-{i}">{text}</p>'
        "</div></div></div></div>"
        for i, text in enumerate(paragraphs)
    )
    page = (
        "<!DOCTYPE html><html><head><title>BBC-style</title></head><body>"
        '<div id="__next">' + inner + "</div>"
        "</body></html>"
    )
    return page, paragraphs


def test_bbc_style_nextjs_html_extracts_multi_paragraph_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Req 4.1: BBC/Next.js-shaped pages produce multi-paragraph bodies via trafilatura,
    and readability alone produces substantially less content (differentiator)."""
    feed_bytes = b"<rss/>"
    rss_url = "https://example.com/feed.xml"
    art_url = "https://www.bbc.com/news/world-12345678"
    page, paragraphs = _bbc_style_nextjs_html()

    # Sanity-check the fixture shape.
    import lxml.html as _lh

    parsed = _lh.fromstring(page)
    assert not parsed.xpath("//article"), "fixture must not contain <article>"
    assert not parsed.xpath("//main"), "fixture must not contain <main>"
    # ≥4 levels of nested <div>: any <div> with >=4 <div> ancestors.
    deep_divs = parsed.xpath("//div[count(ancestor::div) >= 4]")
    assert deep_divs, "fixture must have at least 4 levels of nested <div>"
    sibling_ps = parsed.xpath('//p[starts-with(@class, "sc-")]')
    assert len(sibling_ps) >= 5, f"need >=5 styled <p>, got {len(sibling_ps)}"
    for p in paragraphs:
        assert len(p.split()) >= 30

    # --- (a) trafilatura path ---
    feed_a = _FakeFeed([{"title": "T", "link": art_url, "summary": ""}])
    _install_fakes(
        monkeypatch,
        fetch_map={rss_url: feed_bytes, art_url: page.encode()},
        feed_map={feed_bytes: feed_a},
    )
    out_t = collect([_story(rss_url)], ua="ua", retries=1, backoff_s=0.01, robots_cache={})
    assert len(out_t) == 1, "trafilatura path must produce an article"
    traf_html = out_t[0].html

    # At least three of the five paragraphs' opening phrases must be present.
    opener_hits = sum(
        1 for p in paragraphs if p.split(",")[0][:40] in traf_html
    )
    assert opener_hits >= 3, (
        f"trafilatura output should contain text from >=3 paragraphs, "
        f"got {opener_hits}; html={traf_html[:500]!r}"
    )

    # --- (b) readability-only path: monkeypatch trafilatura.extract -> None ---
    feed_b = _FakeFeed([{"title": "T", "link": art_url, "summary": ""}])
    # Re-install fakes for the second collect() call (monkeypatches from the
    # first _install_fakes are still active, but we want a fresh feed object).
    _install_fakes(
        monkeypatch,
        fetch_map={rss_url: feed_bytes, art_url: page.encode()},
        feed_map={feed_bytes: feed_b},
    )
    monkeypatch.setattr(articles_mod.trafilatura, "extract", lambda *a, **kw: None)
    out_r = collect([_story(rss_url)], ua="ua", retries=1, backoff_s=0.01, robots_cache={})

    readability_chars = len(out_r[0].html) if out_r else 0
    trafilatura_chars = len(traf_html)
    assert trafilatura_chars > 0
    ratio = readability_chars / trafilatura_chars
    assert ratio < 0.30, (
        f"differentiator failed: readability produced {readability_chars} chars vs "
        f"trafilatura {trafilatura_chars} chars (ratio {ratio:.2f}); "
        f"this fixture should be a real Next.js-vs-readability differentiator"
    )


def test_rss_description_fallback_when_article_fetch_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Req 3.3: article fetch fails -> use RSS summary."""
    feed_bytes = b"<rss/>"
    rss_url = "https://example.com/feed.xml"
    art_url = "https://example.com/a"

    feed = _FakeFeed(
        [{"title": "T", "link": art_url, "summary": "<p>fallback summary text</p>"}]
    )
    _install_fakes(
        monkeypatch,
        fetch_map={rss_url: feed_bytes, art_url: ConnectionError("boom")},
        feed_map={feed_bytes: feed},
    )

    out = collect([_story(rss_url)], ua="ua", retries=1, backoff_s=0.01, robots_cache={})

    assert len(out) == 1
    assert "fallback summary text" in out[0].html


def test_drop_when_both_unusable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Req 3.4: article fetch fails AND no usable RSS desc -> drop entry; never raise."""
    feed_bytes = b"<rss/>"
    rss_url = "https://example.com/feed.xml"
    bad_url = "https://example.com/bad"
    good_url = "https://example.com/good"

    good_html = (
        "<html><body><article><p>"
        + ("Plenty of body text here that readability will gladly extract. " * 8)
        + "</p></article></body></html>"
    )

    feed = _FakeFeed(
        [
            {"title": "Bad", "link": bad_url, "summary": ""},
            {"title": "Good", "link": good_url, "summary": ""},
        ]
    )
    _install_fakes(
        monkeypatch,
        fetch_map={
            rss_url: feed_bytes,
            bad_url: ConnectionError("nope"),
            good_url: good_html.encode(),
        },
        feed_map={feed_bytes: feed},
    )

    out = collect([_story(rss_url)], ua="ua", retries=1, backoff_s=0.01, robots_cache={})

    assert len(out) == 1
    assert out[0].title == "Good"


def test_per_source_limit_caps_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    feed_bytes = b"<rss/>"
    rss_url = "https://example.com/feed.xml"
    urls = [f"https://example.com/a{i}" for i in range(5)]
    canned_html = (
        "<html><body><article><p>"
        + ("body " * 50)
        + "</p></article></body></html>"
    )
    feed = _FakeFeed(
        [{"title": f"E{i}", "link": u, "summary": "x"} for i, u in enumerate(urls)]
    )
    _install_fakes(
        monkeypatch,
        fetch_map={rss_url: feed_bytes, **{u: canned_html.encode() for u in urls}},
        feed_map={feed_bytes: feed},
    )

    out = collect(
        [_story(rss_url, limit=2)],
        ua="ua",
        retries=1,
        backoff_s=0.01,
        robots_cache={},
    )

    assert len(out) == 2
    assert [a.title for a in out] == ["E0", "E1"]


def test_relative_img_src_resolved_to_absolute(monkeypatch: pytest.MonkeyPatch) -> None:
    feed_bytes = b"<rss/>"
    rss_url = "https://example.com/feed.xml"
    art_url = "https://example.com/articles/hello"
    article_html = (
        "<html><body><article>"
        "<p>" + ("body text " * 30) + "</p>"
        '<img src="/img/foo.jpg" alt="f"/>'
        "</article></body></html>"
    )
    feed = _FakeFeed([{"title": "T", "link": art_url, "summary": ""}])
    _install_fakes(
        monkeypatch,
        fetch_map={rss_url: feed_bytes, art_url: article_html.encode()},
        feed_map={feed_bytes: feed},
    )

    out = collect([_story(rss_url)], ua="ua", retries=1, backoff_s=0.01, robots_cache={})

    assert len(out) == 1
    assert "https://example.com/img/foo.jpg" in out[0].html
    # The relative src must not survive.
    assert 'src="/img/foo.jpg"' not in out[0].html


def test_protocol_relative_img_src_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    feed_bytes = b"<rss/>"
    rss_url = "https://example.com/feed.xml"
    art_url = "https://example.com/article"
    article_html = (
        "<html><body><article><p>" + ("body " * 50) + "</p>"
        '<img src="//cdn.example.com/x.png"/>'
        "</article></body></html>"
    )
    feed = _FakeFeed([{"title": "T", "link": art_url, "summary": ""}])
    _install_fakes(
        monkeypatch,
        fetch_map={rss_url: feed_bytes, art_url: article_html.encode()},
        feed_map={feed_bytes: feed},
    )

    out = collect([_story(rss_url)], ua="ua", retries=1, backoff_s=0.01, robots_cache={})

    # Either scheme is acceptable: trafilatura resolves protocol-relative
    # URLs to ``http://`` internally (regardless of the page URL's scheme),
    # and the existing ``_resolve_url`` allows-list permits both http and
    # https. The resolution-correctness invariant is that the protocol-
    # relative form does not survive in the final output.
    html = out[0].html
    assert (
        "https://cdn.example.com/x.png" in html
        or "http://cdn.example.com/x.png" in html
    )
    assert 'src="//cdn.example.com/x.png"' not in html


def test_data_and_javascript_urls_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    feed_bytes = b"<rss/>"
    rss_url = "https://example.com/feed.xml"
    art_url = "https://example.com/article"
    article_html = (
        "<html><body><article><p>" + ("real body words " * 40) + "</p>"
        '<img src="data:image/png;base64,AAAA" alt="x"/>'
        '<a href="javascript:alert(1)">click</a>'
        '<a href="https://safe.example.com/page">safe</a>'
        "</article></body></html>"
    )
    feed = _FakeFeed([{"title": "T", "link": art_url, "summary": ""}])
    _install_fakes(
        monkeypatch,
        fetch_map={rss_url: feed_bytes, art_url: article_html.encode()},
        feed_map={feed_bytes: feed},
    )

    out = collect([_story(rss_url)], ua="ua", retries=1, backoff_s=0.01, robots_cache={})

    assert len(out) == 1
    html = out[0].html
    assert "data:" not in html
    assert "javascript:" not in html
    assert "https://safe.example.com/page" in html


def test_robots_disallowed_source_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    feed_bytes = b"<rss/>"
    blocked_rss = "https://blocked.example.com/feed.xml"
    ok_rss = "https://ok.example.com/feed.xml"
    art_url = "https://ok.example.com/a"
    canned_html = (
        "<html><body><article><p>" + ("body " * 50) + "</p></article></body></html>"
    )
    feed = _FakeFeed([{"title": "Ok", "link": art_url, "summary": ""}])

    fetch_log = _install_fakes(
        monkeypatch,
        fetch_map={ok_rss: feed_bytes, art_url: canned_html.encode()},
        feed_map={feed_bytes: feed},
        robots_map={blocked_rss: False, ok_rss: True, art_url: True},
    )

    out = collect(
        [_story(blocked_rss), _story(ok_rss)],
        ua="ua",
        retries=1,
        backoff_s=0.01,
        robots_cache={},
    )

    # Blocked source's RSS URL was never fetched.
    assert blocked_rss not in fetch_log
    assert len(out) == 1
    assert out[0].title == "Ok"


def test_robots_disallowed_article_dropped_others_continue(monkeypatch: pytest.MonkeyPatch) -> None:
    feed_bytes = b"<rss/>"
    rss_url = "https://example.com/feed.xml"
    blocked_art = "https://example.com/blocked"
    ok_art = "https://example.com/ok"
    canned_html = (
        "<html><body><article><p>" + ("body " * 50) + "</p></article></body></html>"
    )
    feed = _FakeFeed(
        [
            {"title": "Blocked", "link": blocked_art, "summary": ""},
            {"title": "OK", "link": ok_art, "summary": ""},
        ]
    )

    fetch_log = _install_fakes(
        monkeypatch,
        fetch_map={rss_url: feed_bytes, ok_art: canned_html.encode()},
        feed_map={feed_bytes: feed},
        robots_map={rss_url: True, blocked_art: False, ok_art: True},
    )

    out = collect([_story(rss_url)], ua="ua", retries=1, backoff_s=0.01, robots_cache={})

    assert blocked_art not in fetch_log
    assert len(out) == 1
    assert out[0].title == "OK"


def test_non_rss_provider_skipped_without_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    feed_bytes = b"<rss/>"
    rss_url = "https://example.com/feed.xml"
    art_url = "https://example.com/a"
    canned_html = (
        "<html><body><article><p>" + ("body " * 50) + "</p></article></body></html>"
    )
    feed = _FakeFeed([{"title": "T", "link": art_url, "summary": ""}])
    _install_fakes(
        monkeypatch,
        fetch_map={rss_url: feed_bytes, art_url: canned_html.encode()},
        feed_map={feed_bytes: feed},
    )

    out = collect(
        [
            {"provider": "twitter", "config": {"handle": "x"}},
            _story(rss_url),
        ],
        ua="ua",
        retries=1,
        backoff_s=0.01,
        robots_cache={},
    )

    assert len(out) == 1
    assert out[0].title == "T"


def test_feed_with_zero_entries_continues(monkeypatch: pytest.MonkeyPatch) -> None:
    """Req 3.4 spirit: empty feed produces no articles, no exception."""
    feed_bytes = b"<rss/>"
    rss_url = "https://example.com/feed.xml"
    feed = _FakeFeed([])
    _install_fakes(
        monkeypatch,
        fetch_map={rss_url: feed_bytes},
        feed_map={feed_bytes: feed},
    )
    out = collect([_story(rss_url)], ua="ua", retries=1, backoff_s=0.01, robots_cache={})
    assert out == []


def test_feed_fetch_exception_continues_with_next_source(monkeypatch: pytest.MonkeyPatch) -> None:
    feed_bytes = b"<rss/>"
    bad_rss = "https://bad.example.com/feed.xml"
    ok_rss = "https://ok.example.com/feed.xml"
    art_url = "https://ok.example.com/a"
    canned_html = (
        "<html><body><article><p>" + ("body " * 50) + "</p></article></body></html>"
    )
    feed = _FakeFeed([{"title": "OK", "link": art_url, "summary": ""}])
    _install_fakes(
        monkeypatch,
        fetch_map={
            bad_rss: ConnectionError("dns"),
            ok_rss: feed_bytes,
            art_url: canned_html.encode(),
        },
        feed_map={feed_bytes: feed},
    )
    out = collect(
        [_story(bad_rss), _story(ok_rss)],
        ua="ua",
        retries=1,
        backoff_s=0.01,
        robots_cache={},
    )
    assert len(out) == 1
    assert out[0].title == "OK"


def test_entry_without_link_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    feed_bytes = b"<rss/>"
    rss_url = "https://example.com/feed.xml"
    feed = _FakeFeed([{"title": "no link", "summary": "x"}])
    _install_fakes(
        monkeypatch,
        fetch_map={rss_url: feed_bytes},
        feed_map={feed_bytes: feed},
    )
    out = collect([_story(rss_url)], ua="ua", retries=1, backoff_s=0.01, robots_cache={})
    assert out == []


def test_legacy_presentational_attrs_stripped_from_img() -> None:
    """GH #1: legacy HTML 4 presentational attributes must not survive sanitization.

    Without stripping, EPUB readers honor ``align="right"`` / ``hspace`` /
    ``vspace`` as float+margin and the surrounding text wraps around the image,
    producing visible overlap on the reMarkable's reflowable column.
    """
    body = (
        '<p>some body text</p>'
        '<img src="https://example.com/x.png" align="right" '
        'hspace="5" vspace="5" border="1" alt="x"/>'
        '<table align="center" bgcolor="#fff" cellpadding="3" cellspacing="0">'
        '<tr><td valign="top">cell</td></tr></table>'
    )

    out = articles_mod._sanitize_and_resolve(body, "https://example.com/article")

    for attr in (
        "align=",
        "valign=",
        "hspace=",
        "vspace=",
        "border=",
        "bgcolor=",
        "cellpadding=",
        "cellspacing=",
    ):
        assert attr not in out, f"unexpected legacy attribute {attr!r} in {out!r}"
    # The <img> itself must still be there with its src.
    assert "https://example.com/x.png" in out


def test_img_width_and_height_preserved() -> None:
    """GH #1: width/height are intentionally outside the deny-list — they
    serve as aspect-ratio hints for the reader. Lock that boundary."""
    body = (
        '<p>some body text</p>'
        '<img src="https://example.com/x.png" width="320" height="240" alt="x"/>'
    )

    out = articles_mod._sanitize_and_resolve(body, "https://example.com/article")

    assert 'width="320"' in out
    assert 'height="240"' in out


# ---------------------------------------------------------------------------
# GH #11: duplicate title <h1> stripping
# ---------------------------------------------------------------------------


def _h1_count(html: str) -> int:
    """Count `<h1` opening-tag occurrences case-insensitively."""
    return html.lower().count("<h1")


def test_duplicate_title_h1_is_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    """GH #11: an <h1> in the article body whose text matches the entry title is removed."""
    feed_bytes = b"<rss/>"
    rss_url = "https://example.com/feed.xml"
    art_url = "https://example.com/article"
    title = "Same Title Across Both"
    article_html = (
        "<html><body><article>"
        f"<h1>{title}</h1>"
        + ("<p>Plenty of body content that readability and trafilatura will both pick up. " * 8)
        + "</p></article></body></html>"
    )

    feed = _FakeFeed([{"title": title, "link": art_url, "summary": "rss"}])
    _install_fakes(
        monkeypatch,
        fetch_map={rss_url: feed_bytes, art_url: article_html.encode()},
        feed_map={feed_bytes: feed},
    )

    out = collect([_story(rss_url)], ua="ua", retries=1, backoff_s=0.01, robots_cache={})

    assert len(out) == 1
    assert _h1_count(out[0].html) == 0, (
        f"expected zero <h1> in Article.html after dedup, got: {out[0].html[:300]!r}"
    )
    assert "body content" in out[0].html


def test_differing_h1_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    """GH #11: an <h1> whose text differs from the article title is preserved."""
    feed_bytes = b"<rss/>"
    rss_url = "https://example.com/feed.xml"
    art_url = "https://example.com/article"
    article_html = (
        "<html><body><article>"
        "<h1>A Different Headline From The Title</h1>"
        + ("<p>Plenty of body content. " * 8)
        + "</p></article></body></html>"
    )

    feed = _FakeFeed([{"title": "Original RSS Entry Title", "link": art_url, "summary": "x"}])
    _install_fakes(
        monkeypatch,
        fetch_map={rss_url: feed_bytes, art_url: article_html.encode()},
        feed_map={feed_bytes: feed},
    )

    out = collect([_story(rss_url)], ua="ua", retries=1, backoff_s=0.01, robots_cache={})

    assert len(out) == 1
    assert "A Different Headline From The Title" in out[0].html
    assert _h1_count(out[0].html) == 1


def test_h1_match_is_whitespace_and_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    """GH #11: trivial whitespace/case differences between title and <h1> still trigger the strip."""
    feed_bytes = b"<rss/>"
    rss_url = "https://example.com/feed.xml"
    art_url = "https://example.com/article"
    article_html = (
        "<html><body><article>"
        "<h1>  SAME   title   ACROSS\nBOTH  </h1>"
        + ("<p>body content. " * 8)
        + "</p></article></body></html>"
    )

    feed = _FakeFeed([{"title": "Same Title Across Both", "link": art_url, "summary": "x"}])
    _install_fakes(
        monkeypatch,
        fetch_map={rss_url: feed_bytes, art_url: article_html.encode()},
        feed_map={feed_bytes: feed},
    )

    out = collect([_story(rss_url)], ua="ua", retries=1, backoff_s=0.01, robots_cache={})

    assert len(out) == 1
    assert _h1_count(out[0].html) == 0


def test_no_h1_in_body_passes_through_unchanged() -> None:
    """GH #11: when the body has no <h1>, the helper is a no-op."""
    body = "<div><p>just body text, no heading</p></div>"
    out = articles_mod._strip_duplicate_title_h1(body, "Some Title")
    assert out == body


def test_strip_duplicate_h1_with_empty_title_is_noop() -> None:
    """Defensive: empty/None title must not strip anything."""
    body = "<div><h1>Some Heading</h1><p>body</p></div>"
    assert articles_mod._strip_duplicate_title_h1(body, "") == body
    assert articles_mod._strip_duplicate_title_h1(body, "   ") == body
