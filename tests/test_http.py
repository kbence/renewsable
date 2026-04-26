"""Tests for ``renewsable.http`` — shared HTTP and robots primitives.

Spec coverage:

- requirements.md → 3.2 ("renewsable shall apply the same robots.txt check
  and retry/backoff policy already used for RSS feed fetches"). The
  behavior under test here is the *primitive* layer that future article-
  and image-fetch call sites will share with the RSS pre-fetch.
- design.md → "Networking" → ``http`` module (Service Interface,
  Preconditions/Postconditions/Invariants, Implementation Notes).

The monkeypatch style mirrors ``test_builder.py`` — we patch
``http.urllib_request.urlopen`` and ``http._time.sleep`` so we can exercise
the real retry loop without doing actual network IO.
"""

from __future__ import annotations

from typing import Any, Callable

import pytest

from renewsable import http as http_mod
from renewsable.http import RobotsCache, fetch_with_retry, robots_allows


# ---------------------------------------------------------------------------
# Test doubles (mirrors test_builder.py)
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Context-manager friendly stand-in for ``urllib.request.urlopen``'s
    return value. ``read()`` yields ``payload`` bytes once."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


def _patch_urlopen(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[Any], Any]
) -> list[Any]:
    """Replace ``http.urllib_request.urlopen`` and capture call args."""
    calls: list[Any] = []

    def _fake(req_or_url: Any, timeout: float | None = None) -> Any:
        calls.append(req_or_url)
        return handler(req_or_url)

    monkeypatch.setattr(http_mod.urllib_request, "urlopen", _fake, raising=True)
    return calls


def _patch_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Replace ``http._time.sleep`` with a recording no-op."""
    sleeps: list[float] = []
    monkeypatch.setattr(http_mod._time, "sleep", sleeps.append, raising=True)
    return sleeps


# ---------------------------------------------------------------------------
# fetch_with_retry
# ---------------------------------------------------------------------------


def test_fetch_with_retry_returns_body_on_first_attempt(monkeypatch):
    sleeps = _patch_sleep(monkeypatch)
    calls = _patch_urlopen(monkeypatch, lambda req: _FakeResponse(b"hello"))

    body = fetch_with_retry(
        "https://example.com/feed",
        ua="renewsable/test",
        retries=3,
        backoff_s=1.0,
    )

    assert body == b"hello"
    assert len(calls) == 1
    # No retries → no sleep.
    assert sleeps == []


def test_fetch_with_retry_recovers_after_transient_error(monkeypatch):
    sleeps = _patch_sleep(monkeypatch)

    state = {"n": 0}

    def _handler(req: Any) -> Any:
        state["n"] += 1
        if state["n"] == 1:
            raise OSError("boom")
        return _FakeResponse(b"recovered")

    _patch_urlopen(monkeypatch, _handler)

    body = fetch_with_retry(
        "https://example.com/feed",
        ua="renewsable/test",
        retries=3,
        backoff_s=2.0,
    )

    assert body == b"recovered"
    assert state["n"] == 2
    # One sleep (between attempts 0 and 1) at base * 2**0 == 2.0s.
    assert sleeps == [2.0]


def test_fetch_with_retry_exhaustion_raises_last_exception(monkeypatch):
    _patch_sleep(monkeypatch)

    errors = [OSError("first"), OSError("second"), OSError("third")]
    state = {"i": 0}

    def _handler(req: Any) -> Any:
        exc = errors[state["i"]]
        state["i"] += 1
        raise exc

    _patch_urlopen(monkeypatch, _handler)

    with pytest.raises(OSError) as excinfo:
        fetch_with_retry(
            "https://example.com/feed",
            ua="renewsable/test",
            retries=3,
            backoff_s=1.0,
        )

    assert str(excinfo.value) == "third"
    assert state["i"] == 3


def test_fetch_with_retry_does_not_sleep_after_final_attempt(monkeypatch):
    sleeps = _patch_sleep(monkeypatch)

    def _always_fail(req: Any) -> Any:
        raise OSError("nope")

    _patch_urlopen(monkeypatch, _always_fail)

    with pytest.raises(OSError):
        fetch_with_retry(
            "https://example.com/feed",
            ua="renewsable/test",
            retries=3,
            backoff_s=1.0,
        )

    # 3 attempts → 2 inter-attempt sleeps (no sleep after final attempt):
    # base * 2**0, base * 2**1.
    assert sleeps == [1.0, 2.0]


# ---------------------------------------------------------------------------
# robots_allows
# ---------------------------------------------------------------------------


_PERMISSIVE_ROBOTS = b"User-agent: *\nAllow: /\n"
_BLOCK_ALL_ROBOTS = b"User-agent: *\nDisallow: /\n"


def test_robots_allows_caches_per_host(monkeypatch):
    fetched: list[str] = []

    def _handler(req: Any) -> Any:
        url = req if isinstance(req, str) else req.full_url
        fetched.append(url)
        return _FakeResponse(_PERMISSIVE_ROBOTS)

    _patch_urlopen(monkeypatch, _handler)

    cache: RobotsCache = {}
    assert robots_allows(
        "https://example.com/a", cache=cache, ua="renewsable/test"
    )
    assert robots_allows(
        "https://example.com/b", cache=cache, ua="renewsable/test"
    )

    # Cached after the first call → only one robots.txt fetch.
    assert fetched == ["https://example.com/robots.txt"]
    assert "https://example.com" in cache


def test_robots_allows_fail_open_on_missing_robots(monkeypatch):
    def _handler(req: Any) -> Any:
        raise OSError("404 not found")

    _patch_urlopen(monkeypatch, _handler)

    cache: RobotsCache = {}
    assert (
        robots_allows("https://example.com/a", cache=cache, ua="renewsable/test")
        is True
    )
    # Cache entry should be ``None`` (fail-open marker).
    assert cache["https://example.com"] is None


def test_robots_allows_fail_open_on_non_http_scheme(monkeypatch):
    # Should never touch the network for ``file://``.
    calls = _patch_urlopen(monkeypatch, lambda req: _FakeResponse(b""))

    cache: RobotsCache = {}
    assert (
        robots_allows("file:///tmp/feed.xml", cache=cache, ua="renewsable/test")
        is True
    )
    assert calls == []


def test_robots_allows_returns_false_when_disallowed(monkeypatch):
    def _handler(req: Any) -> Any:
        return _FakeResponse(_BLOCK_ALL_ROBOTS)

    _patch_urlopen(monkeypatch, _handler)

    cache: RobotsCache = {}
    assert (
        robots_allows("https://example.com/a", cache=cache, ua="renewsable/test")
        is False
    )
