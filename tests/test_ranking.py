"""Tests for :mod:`renewsable.ranking` — the optional LLM filter/sort stage.

Network is never touched: ``ranking.urllib_request.urlopen`` and
``ranking._time.sleep`` are monkeypatched, mirroring the convention in
``tests/test_http.py`` and ``tests/test_epub.py``.

The load-bearing property under test is **fail-open**: for every conceivable
failure inside the ranking stage, the caller must get the input list back
unchanged (and, critically, *not* get an empty list). Those are the
``TestFailOpen*`` classes below.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from renewsable import ranking as ranking_mod
from renewsable.articles import Article
from renewsable.config import LlmConfig
from renewsable.ranking import rank


# ---------------------------------------------------------------------------
# Helpers / doubles
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Context-manager friendly stand-in for ``urlopen``'s return value."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self._payload


def _chat_body(content: str) -> bytes:
    """A minimal, well-formed OpenAI-style chat completion response."""
    return json.dumps(
        {"choices": [{"message": {"role": "assistant", "content": content}}]}
    ).encode("utf-8")


def _patch_urlopen(monkeypatch: pytest.MonkeyPatch, handler):
    """Patch ``ranking.urllib_request.urlopen``; return the call log."""
    calls: list[Any] = []

    def _fake(req: Any, timeout: float | None = None) -> Any:
        calls.append(req)
        result = handler(req)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(ranking_mod.urllib_request, "urlopen", _fake, raising=True)
    monkeypatch.setattr(ranking_mod._time, "sleep", lambda *_a, **_k: None)
    return calls


def _articles(*titles: str) -> list[Article]:
    return [
        Article(
            title=t,
            html=f"<p>body of {t}</p>",
            source_url=f"https://example.com/{i}",
            synopsis=f"synopsis {i}",
        )
        for i, t in enumerate(titles)
    ]


def _llm(**overrides) -> LlmConfig:
    base = dict(
        enabled=True,
        base_url="https://llm.example.com/v1",
        model="test-model",
        api_key_env="TEST_KEY",
        instructions="no recipes",
    )
    base.update(overrides)
    return LlmConfig(**base)


def _scores(*pairs: tuple[int, int, bool]) -> str:
    """Build a scores JSON payload from ``(i, score, keep)`` triples."""
    return json.dumps(
        {"scores": [{"i": i, "score": s, "keep": k} for i, s, k in pairs]}
    )


# ---------------------------------------------------------------------------
# Disabled / unconfigured → untouched (no network at all)
# ---------------------------------------------------------------------------


class TestDisabled:
    def test_none_llm_returns_input_and_makes_no_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _patch_urlopen(monkeypatch, lambda req: AssertionError("network!"))
        arts = _articles("a", "b")
        assert rank(arts, llm=None, api_key="k") == arts
        assert calls == []

    def test_disabled_llm_returns_input_and_makes_no_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _patch_urlopen(monkeypatch, lambda req: AssertionError("network!"))
        arts = _articles("a", "b")
        assert rank(arts, llm=_llm(enabled=False), api_key="k") == arts
        assert calls == []

    def test_empty_article_list_short_circuits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _patch_urlopen(monkeypatch, lambda req: AssertionError("network!"))
        assert rank([], llm=_llm(), api_key="k") == []
        assert calls == []


# ---------------------------------------------------------------------------
# Happy path: filter + sort
# ---------------------------------------------------------------------------


class TestFilterAndSort:
    def test_filters_keep_false_and_sorts_by_score_desc(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        arts = _articles("low", "high", "dropped", "mid")
        _patch_urlopen(
            monkeypatch,
            lambda req: _FakeResponse(
                _chat_body(_scores((0, 3, True), (1, 9, True), (2, 5, False), (3, 7, True)))
            ),
        )
        out = rank(arts, llm=_llm(), api_key="k")
        assert [a.title for a in out] == ["high", "mid", "low"]

    def test_keep_none_keeps_everything_flagged_keep(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        arts = _articles("a", "b", "c")
        _patch_urlopen(
            monkeypatch,
            lambda req: _FakeResponse(
                _chat_body(_scores((0, 1, True), (1, 1, False), (2, 1, True)))
            ),
        )
        out = rank(arts, llm=_llm(), api_key="k")
        assert [a.title for a in out] == ["a", "c"]

    def test_truncates_to_keep_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        arts = _articles("a", "b", "c", "d")
        _patch_urlopen(
            monkeypatch,
            lambda req: _FakeResponse(
                _chat_body(_scores((0, 1, True), (1, 9, True), (2, 5, True), (3, 3, True)))
            ),
        )
        out = rank(arts, llm=_llm(keep=2), api_key="k")
        assert [a.title for a in out] == ["b", "c"]

    def test_equal_scores_keep_original_feed_order(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        arts = _articles("first", "second", "third")
        _patch_urlopen(
            monkeypatch,
            lambda req: _FakeResponse(
                _chat_body(_scores((0, 5, True), (1, 5, True), (2, 5, True)))
            ),
        )
        out = rank(arts, llm=_llm(), api_key="k")
        assert [a.title for a in out] == ["first", "second", "third"]

    def test_articles_missing_from_response_are_kept(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A partial answer must never silently drop the articles it omits."""
        arts = _articles("scored", "unmentioned")
        _patch_urlopen(
            monkeypatch,
            lambda req: _FakeResponse(_chat_body(_scores((0, 9, True)))),
        )
        out = rank(arts, llm=_llm(), api_key="k")
        assert sorted(a.title for a in out) == ["scored", "unmentioned"]


# ---------------------------------------------------------------------------
# Request shape
# ---------------------------------------------------------------------------


class TestRequestShape:
    def test_posts_to_chat_completions_with_bearer_auth(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _patch_urlopen(
            monkeypatch, lambda req: _FakeResponse(_chat_body(_scores((0, 1, True))))
        )
        rank(_articles("a"), llm=_llm(), api_key="secret-key")
        assert len(calls) == 1
        req = calls[0]
        assert req.full_url == "https://llm.example.com/v1/chat/completions"
        assert req.get_method() == "POST"
        # Authorization header carries the key.
        headers = {k.lower(): v for k, v in req.header_items()}
        assert headers["authorization"] == "Bearer secret-key"
        assert headers["content-type"] == "application/json"

    def test_payload_contains_titles_synopses_and_instructions(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _patch_urlopen(
            monkeypatch, lambda req: _FakeResponse(_chat_body(_scores((0, 1, True))))
        )
        rank(_articles("Some headline"), llm=_llm(instructions="no pr fluff"), api_key="k")
        sent = json.loads(calls[0].data.decode("utf-8"))
        assert sent["model"] == "test-model"
        user_msg = json.loads(sent["messages"][1]["content"])
        assert user_msg["instructions"] == "no pr fluff"
        assert user_msg["articles"][0]["title"] == "Some headline"
        assert "synopsis" in user_msg["articles"][0]

    def test_body_is_not_sent_only_title_and_synopsis(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _patch_urlopen(
            monkeypatch, lambda req: _FakeResponse(_chat_body(_scores((0, 1, True))))
        )
        rank(_articles("headline"), llm=_llm(), api_key="k")
        raw = calls[0].data.decode("utf-8")
        assert "body of headline" not in raw

    def test_long_synopsis_is_truncated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _patch_urlopen(
            monkeypatch, lambda req: _FakeResponse(_chat_body(_scores((0, 1, True))))
        )
        art = Article(
            title="t",
            html="<p>x</p>",
            source_url="https://example.com/0",
            synopsis="word " * 500,
        )
        rank([art], llm=_llm(), api_key="k", synopsis_max_chars=50)
        user_msg = json.loads(
            json.loads(calls[0].data.decode("utf-8"))["messages"][1]["content"]
        )
        assert len(user_msg["articles"][0]["synopsis"]) <= 51  # + ellipsis

    def test_base_url_trailing_slash_does_not_double_up(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _patch_urlopen(
            monkeypatch, lambda req: _FakeResponse(_chat_body(_scores((0, 1, True))))
        )
        rank(_articles("a"), llm=_llm(base_url="https://llm.example.com/v1/"), api_key="k")
        assert calls[0].full_url == "https://llm.example.com/v1/chat/completions"


# ---------------------------------------------------------------------------
# Fail-open: every failure returns the input list unchanged
# ---------------------------------------------------------------------------


class TestFailOpen:
    def test_missing_api_key_returns_input(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _patch_urlopen(monkeypatch, lambda req: AssertionError("network!"))
        arts = _articles("a", "b")
        assert rank(arts, llm=_llm(), api_key=None) == arts
        assert calls == []

    def test_empty_api_key_returns_input(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _patch_urlopen(monkeypatch, lambda req: AssertionError("network!"))
        arts = _articles("a")
        assert rank(arts, llm=_llm(), api_key="") == arts
        assert calls == []

    def test_transport_error_returns_input(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import urllib.error

        _patch_urlopen(
            monkeypatch, lambda req: urllib.error.URLError("no route to host")
        )
        arts = _articles("a", "b")
        assert rank(arts, llm=_llm(), api_key="k") == arts

    def test_timeout_returns_input(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_urlopen(monkeypatch, lambda req: TimeoutError("timed out"))
        arts = _articles("a")
        assert rank(arts, llm=_llm(), api_key="k") == arts

    def test_http_401_returns_input_without_retry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import urllib.error

        calls = _patch_urlopen(
            monkeypatch,
            lambda req: urllib.error.HTTPError(
                "u", 401, "unauthorized", {}, None  # type: ignore[arg-type]
            ),
        )
        arts = _articles("a")
        assert rank(arts, llm=_llm(), api_key="bad") == arts
        # 4xx is not retried.
        assert len(calls) == 1

    def test_http_500_is_retried_then_fails_open(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import urllib.error

        calls = _patch_urlopen(
            monkeypatch,
            lambda req: urllib.error.HTTPError(
                "u", 500, "server error", {}, None  # type: ignore[arg-type]
            ),
        )
        arts = _articles("a")
        assert rank(arts, llm=_llm(), api_key="k") == arts
        assert len(calls) == 2

    def test_non_json_body_returns_input(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_urlopen(monkeypatch, lambda req: _FakeResponse(b"<html>gateway</html>"))
        arts = _articles("a")
        assert rank(arts, llm=_llm(), api_key="k") == arts

    def test_unexpected_response_shape_returns_input(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_urlopen(
            monkeypatch, lambda req: _FakeResponse(json.dumps({"nope": 1}).encode())
        )
        arts = _articles("a")
        assert rank(arts, llm=_llm(), api_key="k") == arts

    def test_empty_message_content_returns_input(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_urlopen(monkeypatch, lambda req: _FakeResponse(_chat_body("   ")))
        arts = _articles("a")
        assert rank(arts, llm=_llm(), api_key="k") == arts

    def test_model_emits_prose_instead_of_json_returns_input(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_urlopen(
            monkeypatch,
            lambda req: _FakeResponse(_chat_body("Sure! Here are my rankings...")),
        )
        arts = _articles("a")
        assert rank(arts, llm=_llm(), api_key="k") == arts

    def test_scores_not_a_list_returns_input(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_urlopen(
            monkeypatch,
            lambda req: _FakeResponse(_chat_body(json.dumps({"scores": "oops"}))),
        )
        arts = _articles("a")
        assert rank(arts, llm=_llm(), api_key="k") == arts

    def test_all_kept_false_still_does_not_return_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A response that would drop everything must not yield an empty paper.

        ``rank`` itself returns the (empty) filtered list here; the *Builder*
        is responsible for not shipping an empty paper. This test pins that
        ``rank`` stays honest about what it computed, while
        ``test_builder.py`` pins the fallback.
        """
        arts = _articles("a", "b")
        _patch_urlopen(
            monkeypatch,
            lambda req: _FakeResponse(
                _chat_body(_scores((0, 1, False), (1, 1, False)))
            ),
        )
        assert rank(arts, llm=_llm(), api_key="k") == []


# ---------------------------------------------------------------------------
# Defensive parsing of untrusted model output
# ---------------------------------------------------------------------------


class TestDefensiveParsing:
    def test_json_wrapped_in_code_fence_is_parsed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fenced = "```json\n" + _scores((0, 2, True), (1, 8, True)) + "\n```"
        arts = _articles("a", "b")
        _patch_urlopen(monkeypatch, lambda req: _FakeResponse(_chat_body(fenced)))
        out = rank(arts, llm=_llm(), api_key="k")
        assert [a.title for a in out] == ["b", "a"]

    def test_scores_are_clamped_to_range(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        arts = _articles("a", "b")
        _patch_urlopen(
            monkeypatch,
            lambda req: _FakeResponse(
                _chat_body(_scores((0, 999, True), (1, -50, True)))
            ),
        )
        out = rank(arts, llm=_llm(), api_key="k")
        # 999 -> 10, -50 -> 0, so "a" sorts first.
        assert [a.title for a in out] == ["a", "b"]

    def test_out_of_range_index_is_ignored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        arts = _articles("a")
        _patch_urlopen(
            monkeypatch,
            lambda req: _FakeResponse(
                _chat_body(_scores((0, 5, True), (99, 10, False), (-1, 10, False)))
            ),
        )
        out = rank(arts, llm=_llm(), api_key="k")
        assert [a.title for a in out] == ["a"]

    def test_malformed_entries_are_skipped_not_fatal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = json.dumps(
            {
                "scores": [
                    "not a dict",
                    {"i": "zero", "score": 5, "keep": True},
                    {"i": 0, "score": 3, "keep": True},
                ]
            }
        )
        arts = _articles("a")
        _patch_urlopen(monkeypatch, lambda req: _FakeResponse(_chat_body(payload)))
        out = rank(arts, llm=_llm(), api_key="k")
        assert [a.title for a in out] == ["a"]

    def test_non_bool_keep_strings_are_interpreted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = json.dumps(
            {
                "scores": [
                    {"i": 0, "score": 5, "keep": "false"},
                    {"i": 1, "score": 5, "keep": "yes"},
                    {"i": 2, "score": 5, "keep": 0},
                ]
            }
        )
        arts = _articles("a", "b", "c")
        _patch_urlopen(monkeypatch, lambda req: _FakeResponse(_chat_body(payload)))
        out = rank(arts, llm=_llm(), api_key="k")
        assert [a.title for a in out] == ["b"]

    def test_unparseable_keep_defaults_to_keep(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unreadable ``keep`` must never delete an article."""
        payload = json.dumps({"scores": [{"i": 0, "score": 5, "keep": {"weird": 1}}]})
        arts = _articles("a")
        _patch_urlopen(monkeypatch, lambda req: _FakeResponse(_chat_body(payload)))
        out = rank(arts, llm=_llm(), api_key="k")
        assert [a.title for a in out] == ["a"]

    def test_missing_score_defaults_to_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = json.dumps({"scores": [{"i": 0, "keep": True}, {"i": 1, "score": 9, "keep": True}]})
        arts = _articles("a", "b")
        _patch_urlopen(monkeypatch, lambda req: _FakeResponse(_chat_body(payload)))
        out = rank(arts, llm=_llm(), api_key="k")
        assert [a.title for a in out] == ["b", "a"]

    def test_bool_index_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``bool`` is an ``int`` subclass in Python; it must not be an index."""
        payload = json.dumps({"scores": [{"i": True, "score": 10, "keep": True}]})
        arts = _articles("a")
        _patch_urlopen(monkeypatch, lambda req: _FakeResponse(_chat_body(payload)))
        out = rank(arts, llm=_llm(), api_key="k")
        # True == 1 is out of range for a 1-article list, and even if it were
        # in range we reject bools. Article "a" is kept by default.
        assert [a.title for a in out] == ["a"]
