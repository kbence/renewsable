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


def test_happy_path_extracts_readability_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """Req 3.1: article URL fetched, main body extracted via readability."""
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
    # Readability-extracted prose appears in the html.
    assert "actual story body" in a.html
    # Boilerplate stripped.
    assert "<script" not in a.html.lower()


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

    assert "https://cdn.example.com/x.png" in out[0].html


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
