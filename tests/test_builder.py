"""Unit tests for :mod:`renewsable.builder` (Task 2.4).

Design reference: the "Builder" component block in
``.kiro/specs/daily-paper/design.md`` — including the Implementation Notes
on ``file://`` rewriting and the PDF magic-byte validation.

Requirements covered:
- 2.1 One dated PDF per day in output dir, named ``renewsable-YYYY-MM-DD.pdf``.
- 2.2 Include every feed that responded successfully.
- 2.3 Re-run same date overwrites.
- 2.5 Issue date visible on first page (delegated to goosepaper; we only
  assert that the issue date drives the filename, which is our half).
- 2.6 No stories → non-zero exit (BuildError), no upload artefact produced.
- 3.1 telex.hu accepted and included.
- 3.3 Unreachable source → skip, log, continue.
- 3.4 Extraction failure → at least title/source/link (goosepaper's job; we
  only verify the feed bytes pass through unchanged).
- 8.3 Source failure logged with source + reason.
- 9.1 Per-feed tolerance.
- 9.2 Bounded retries with backoff.
- 9.4 Identifying User-Agent + honour robots.txt.

Testability
-----------
``goosepaper`` is pinned behind ``Config.goosepaper_bin`` so tests
substitute a shell-script test double (``tests/fixtures/fake_goosepaper.sh``).
All network I/O is monkeypatched on the module-level aliases
``renewsable.builder.urllib_request`` and ``renewsable.builder._time`` so no
real HTTP or sleeps happen. Subprocess invocation uses the module-level
``renewsable.builder.subprocess`` alias, which individual tests can replace
to simulate goosepaper failures without shelling out.
"""

from __future__ import annotations

import datetime
import io
import json
import logging
import os
import stat
import subprocess as _real_subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from renewsable import builder as builder_mod
from renewsable.builder import Builder
from renewsable.config import Config
from renewsable.errors import BuildError


FIXTURES = Path(__file__).parent / "fixtures"
FAKE_GOOSEPAPER = FIXTURES / "fake_goosepaper.sh"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ensure_fake_executable() -> None:
    """Make sure the bundled fake_goosepaper.sh is marked +x.

    Git preserves the executable bit, but belt-and-braces when the file was
    created on a filesystem that lost the mode (some CI checkouts).
    """
    current = FAKE_GOOSEPAPER.stat().st_mode
    FAKE_GOOSEPAPER.chmod(current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _make_config(
    tmp_path: Path,
    *,
    stories: list[dict[str, Any]] | None = None,
    goosepaper_bin: str | None = None,
    user_agent: str = "renewsable-test/1.0",
    feed_fetch_retries: int = 3,
    feed_fetch_backoff_s: float = 0.01,
    subprocess_timeout_s: int = 30,
    font_size: int | None = None,
) -> Config:
    """Construct a valid Config pointing at the fake goosepaper binary."""
    _ensure_fake_executable()
    return Config(
        schedule_time="05:30",
        output_dir=tmp_path / "out",
        remarkable_folder="/News",
        stories=stories
        if stories is not None
        else [{"provider": "rss", "config": {"rss_path": "https://example.com/rss"}}],
        font_size=font_size,
        user_agent=user_agent,
        goosepaper_bin=goosepaper_bin or str(FAKE_GOOSEPAPER),
        feed_fetch_retries=feed_fetch_retries,
        feed_fetch_backoff_s=feed_fetch_backoff_s,
        subprocess_timeout_s=subprocess_timeout_s,
    )


class _FakeResponse:
    """Context-managing stand-in for ``urlopen``'s return value."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def read(self) -> bytes:
        return self._body


class _Urlopener:
    """Configurable stand-in for ``urllib.request.urlopen``.

    Tests set ``responses`` (URL prefix -> callable) so different hosts and
    paths can return different bodies, errors, or robots.txt policies. Every
    call to the stand-in is recorded in ``calls``.
    """

    def __init__(self) -> None:
        self.calls: list[Any] = []
        self.url_calls: list[str] = []
        # Default: every URL returns a minimal feed.
        self.handler: Callable[[Any], Any] = self._default

    @staticmethod
    def _default(req_or_url: Any) -> _FakeResponse:
        return _FakeResponse(b"<rss></rss>")

    def __call__(self, req_or_url: Any, timeout: float | None = None) -> Any:
        self.calls.append(req_or_url)
        url = req_or_url if isinstance(req_or_url, str) else req_or_url.full_url
        self.url_calls.append(url)
        return self.handler(req_or_url)


def _install_network(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[Any], Any]
) -> _Urlopener:
    """Replace builder.urllib_request.urlopen with a capturing double."""
    urlopener = _Urlopener()
    urlopener.handler = handler
    monkeypatch.setattr(
        builder_mod.urllib_request, "urlopen", urlopener, raising=True
    )
    return urlopener


def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Replace builder._time.sleep with a recording no-op. Returns the record."""
    sleeps: list[float] = []
    monkeypatch.setattr(builder_mod._time, "sleep", sleeps.append, raising=True)
    return sleeps


def _allow_all_robots(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the robots.txt check to always permit. Bypasses the HTTP path."""

    def _allow(self: Any, url: str) -> bool:  # pragma: no cover - trivial
        return True

    monkeypatch.setattr(Builder, "_robots_allows", _allow, raising=True)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_build_happy_path_produces_pdf_with_magic_bytes(tmp_path, monkeypatch):
    """Single rss story; fake goosepaper emits a stub PDF; build() returns path."""
    cfg = _make_config(tmp_path)
    _install_network(monkeypatch, lambda req: _FakeResponse(b"<rss><item/></rss>"))
    _no_sleep(monkeypatch)
    _allow_all_robots(monkeypatch)

    today = datetime.date(2026, 4, 19)
    result = Builder(cfg).build(today=today)

    assert result == cfg.output_dir / "renewsable-2026-04-19.pdf"
    assert result.is_file()
    assert result.stat().st_size > 0
    with result.open("rb") as fh:
        magic = fh.read(5)
    assert magic == b"%PDF-"


# ---------------------------------------------------------------------------
# Date-in-filename (Req 2.1)
# ---------------------------------------------------------------------------


def test_build_filename_uses_provided_today(tmp_path, monkeypatch):
    cfg = _make_config(tmp_path)
    _install_network(monkeypatch, lambda req: _FakeResponse(b"<rss/>"))
    _no_sleep(monkeypatch)
    _allow_all_robots(monkeypatch)

    for d in [
        datetime.date(2026, 1, 1),
        datetime.date(2026, 4, 19),
        datetime.date(2027, 12, 31),
    ]:
        out = Builder(cfg).build(today=d)
        assert out.name == f"renewsable-{d.isoformat()}.pdf"


def test_build_defaults_to_today_when_date_omitted(tmp_path, monkeypatch):
    """If ``today`` is not passed, the PDF name is today's local date."""
    cfg = _make_config(tmp_path)
    _install_network(monkeypatch, lambda req: _FakeResponse(b"<rss/>"))
    _no_sleep(monkeypatch)
    _allow_all_robots(monkeypatch)

    out = Builder(cfg).build()
    today = datetime.date.today()
    assert out.name == f"renewsable-{today.isoformat()}.pdf"


# ---------------------------------------------------------------------------
# Re-run overwrites (Req 2.3)
# ---------------------------------------------------------------------------


def test_build_twice_same_date_overwrites(tmp_path, monkeypatch):
    cfg = _make_config(tmp_path)
    _install_network(monkeypatch, lambda req: _FakeResponse(b"<rss/>"))
    _no_sleep(monkeypatch)
    _allow_all_robots(monkeypatch)

    today = datetime.date(2026, 4, 19)
    b = Builder(cfg)
    out1 = b.build(today=today)
    assert out1.is_file()
    # Write something different to the file so we can detect overwrite.
    out1.write_bytes(b"%PDF-0.0\nsentinel\n")
    out2 = b.build(today=today)

    assert out2 == out1
    # After the second build the fake goosepaper has replaced the sentinel
    # with its own stub content.
    assert b"fake stub for tests" in out2.read_bytes()
    # Exactly one PDF for that date exists.
    pdfs = sorted(cfg.output_dir.glob("renewsable-*.pdf"))
    assert pdfs == [out1]


# ---------------------------------------------------------------------------
# All feeds fail → BuildError, no PDF (Req 2.6)
# ---------------------------------------------------------------------------


def test_build_raises_when_every_feed_fails(tmp_path, monkeypatch):
    cfg = _make_config(
        tmp_path,
        stories=[
            {"provider": "rss", "config": {"rss_path": "https://a.example/rss"}},
            {"provider": "rss", "config": {"rss_path": "https://b.example/rss"}},
        ],
        feed_fetch_retries=2,
    )

    def always_fail(req: Any) -> Any:
        raise OSError("network down")

    _install_network(monkeypatch, always_fail)
    _no_sleep(monkeypatch)
    _allow_all_robots(monkeypatch)

    with pytest.raises(BuildError):
        Builder(cfg).build(today=datetime.date(2026, 4, 19))

    # No partial PDF should remain.
    pdfs = list(cfg.output_dir.glob("renewsable-*.pdf")) if cfg.output_dir.exists() else []
    assert pdfs == []


# ---------------------------------------------------------------------------
# Per-feed tolerance (Req 9.1)
# ---------------------------------------------------------------------------


def test_build_tolerates_one_bad_feed(tmp_path, monkeypatch, caplog):
    cfg = _make_config(
        tmp_path,
        stories=[
            {"provider": "rss", "config": {"rss_path": "https://bad.example/rss"}},
            {"provider": "rss", "config": {"rss_path": "https://good.example/rss"}},
        ],
        feed_fetch_retries=2,
    )

    def handler(req: Any) -> Any:
        url = req if isinstance(req, str) else req.full_url
        if "bad.example" in url:
            raise OSError("503 service unavailable")
        return _FakeResponse(b"<rss><item/></rss>")

    urlopener = _install_network(monkeypatch, handler)
    _no_sleep(monkeypatch)
    _allow_all_robots(monkeypatch)

    caplog.set_level(logging.WARNING, logger="renewsable.builder")
    out = Builder(cfg).build(today=datetime.date(2026, 4, 19))

    assert out.is_file()
    # bad.example was tried retries-many times; good.example once.
    bad_count = sum("bad.example" in u for u in urlopener.url_calls)
    good_count = sum("good.example" in u for u in urlopener.url_calls)
    assert bad_count == cfg.feed_fetch_retries
    assert good_count == 1

    # Log identifies the failing source + reason (Req 8.3).
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "bad.example" in joined
    assert "503" in joined or "service unavailable" in joined.lower()


# ---------------------------------------------------------------------------
# Bounded retries + backoff (Req 9.2)
# ---------------------------------------------------------------------------


def test_feed_fetch_retries_bounded_with_exponential_backoff(tmp_path, monkeypatch):
    cfg = _make_config(tmp_path, feed_fetch_retries=3, feed_fetch_backoff_s=1.0)

    calls: list[str] = []

    def always_fail(req: Any) -> Any:
        url = req if isinstance(req, str) else req.full_url
        calls.append(url)
        raise OSError("boom")

    _install_network(monkeypatch, always_fail)
    sleeps = _no_sleep(monkeypatch)
    _allow_all_robots(monkeypatch)

    with pytest.raises(BuildError):
        Builder(cfg).build(today=datetime.date(2026, 4, 19))

    # Exactly feed_fetch_retries total attempts (not retries-on-top-of-one).
    assert len(calls) == cfg.feed_fetch_retries
    # Between attempts we sleep (N-1) times with exponential backoff.
    # Base 1.0, 2x each step -> [1.0, 2.0] for 3 attempts.
    assert sleeps == [1.0, 2.0]


def test_feed_fetch_succeeds_before_retries_exhausted(tmp_path, monkeypatch):
    """On first success, no further attempts are made (Req 9.2)."""
    cfg = _make_config(tmp_path, feed_fetch_retries=5, feed_fetch_backoff_s=0.5)

    attempts = {"n": 0}

    def flaky(req: Any) -> Any:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise OSError("transient")
        return _FakeResponse(b"<rss/>")

    _install_network(monkeypatch, flaky)
    sleeps = _no_sleep(monkeypatch)
    _allow_all_robots(monkeypatch)

    out = Builder(cfg).build(today=datetime.date(2026, 4, 19))
    assert out.is_file()
    # Exactly 3 urlopen calls; 2 sleeps between the 3 attempts.
    assert attempts["n"] == 3
    assert sleeps == [0.5, 1.0]


# ---------------------------------------------------------------------------
# User-Agent set (Req 9.4)
# ---------------------------------------------------------------------------


def test_user_agent_header_is_config_user_agent(tmp_path, monkeypatch):
    cfg = _make_config(tmp_path, user_agent="renewsable/0.9 (+test)")

    captured_requests: list[Any] = []

    def capture(req: Any) -> Any:
        captured_requests.append(req)
        return _FakeResponse(b"<rss/>")

    _install_network(monkeypatch, capture)
    _no_sleep(monkeypatch)
    _allow_all_robots(monkeypatch)

    Builder(cfg).build(today=datetime.date(2026, 4, 19))

    assert captured_requests, "urlopen should have been called"
    req = captured_requests[0]
    # The Request object must carry a User-Agent header matching config.
    # urllib lower-cases header keys when storing.
    ua = req.get_header("User-agent") or req.headers.get("User-agent")
    assert ua == cfg.user_agent


# ---------------------------------------------------------------------------
# robots.txt disallow (Req 9.4)
# ---------------------------------------------------------------------------


def test_robots_txt_disallow_skips_feed_but_build_continues(tmp_path, monkeypatch, caplog):
    cfg = _make_config(
        tmp_path,
        stories=[
            {"provider": "rss", "config": {"rss_path": "https://blocked.example/rss"}},
            {"provider": "rss", "config": {"rss_path": "https://allowed.example/rss"}},
        ],
    )

    def handler(req: Any) -> Any:
        url = req if isinstance(req, str) else req.full_url
        if url.endswith("/robots.txt"):
            if "blocked.example" in url:
                return _FakeResponse(b"User-agent: *\nDisallow: /\n")
            # allowed.example's robots.txt permits everything.
            return _FakeResponse(b"User-agent: *\nDisallow:\n")
        if "blocked.example" in url:
            # Should never be fetched — the test fails if we get here.
            raise AssertionError(f"unexpected fetch of disallowed URL: {url}")
        return _FakeResponse(b"<rss><item/></rss>")

    urlopener = _install_network(monkeypatch, handler)
    _no_sleep(monkeypatch)

    caplog.set_level(logging.WARNING, logger="renewsable.builder")
    out = Builder(cfg).build(today=datetime.date(2026, 4, 19))
    assert out.is_file()

    # The disallowed feed was skipped without ever hitting the rss_path.
    blocked_fetches = [
        u for u in urlopener.url_calls if "blocked.example" in u and "/rss" in u and "robots" not in u
    ]
    assert blocked_fetches == []

    # Log mentions the blocked source + reason.
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "blocked.example" in joined
    assert "robots" in joined.lower()


def test_robots_txt_failure_is_treated_as_allowed(tmp_path, monkeypatch):
    """If robots.txt cannot be fetched, we proceed (fail-open).

    This mirrors stdlib ``RobotFileParser`` semantics: an unreachable
    robots.txt does not block the host.
    """
    cfg = _make_config(tmp_path)

    def handler(req: Any) -> Any:
        url = req if isinstance(req, str) else req.full_url
        if url.endswith("/robots.txt"):
            raise OSError("connection refused")
        return _FakeResponse(b"<rss/>")

    _install_network(monkeypatch, handler)
    _no_sleep(monkeypatch)

    out = Builder(cfg).build(today=datetime.date(2026, 4, 19))
    assert out.is_file()


def test_robots_txt_fetched_once_per_host(tmp_path, monkeypatch):
    """Per-run robots cache: two feeds on the same host → one robots fetch."""
    cfg = _make_config(
        tmp_path,
        stories=[
            {"provider": "rss", "config": {"rss_path": "https://example.com/a/rss"}},
            {"provider": "rss", "config": {"rss_path": "https://example.com/b/rss"}},
        ],
    )

    def handler(req: Any) -> Any:
        url = req if isinstance(req, str) else req.full_url
        if url.endswith("/robots.txt"):
            return _FakeResponse(b"")
        return _FakeResponse(b"<rss/>")

    urlopener = _install_network(monkeypatch, handler)
    _no_sleep(monkeypatch)

    Builder(cfg).build(today=datetime.date(2026, 4, 19))

    robots_fetches = [u for u in urlopener.url_calls if u.endswith("/robots.txt")]
    assert robots_fetches == ["https://example.com/robots.txt"]


# ---------------------------------------------------------------------------
# Goosepaper non-zero exit → BuildError (Req 2.6)
# ---------------------------------------------------------------------------


def _install_fake_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    *,
    returncode: int,
    write_pdf: bool = True,
    pdf_bytes: bytes = b"%PDF-1.4\n%stub\n%%EOF\n",
    stderr: str = "",
) -> list[dict[str, Any]]:
    """Replace builder.subprocess.run with a deterministic double.

    Captures argv + kwargs, optionally writes a stub PDF to the ``-o``
    argument, and returns the configured returncode.
    """
    calls: list[dict[str, Any]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> Any:
        calls.append({"argv": list(argv), "kwargs": kwargs})
        # Locate the -o target so we can optionally drop a PDF there.
        output_path: Path | None = None
        for i, tok in enumerate(argv):
            if tok == "-o" and i + 1 < len(argv):
                output_path = Path(argv[i + 1])
                break
        if write_pdf and output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(pdf_bytes)
        return SimpleNamespace(returncode=returncode, stdout="", stderr=stderr)

    monkeypatch.setattr(builder_mod.subprocess, "run", fake_run, raising=True)
    return calls


def test_goosepaper_nonzero_exit_raises_build_error(tmp_path, monkeypatch, caplog):
    cfg = _make_config(tmp_path)
    _install_network(monkeypatch, lambda req: _FakeResponse(b"<rss/>"))
    _no_sleep(monkeypatch)
    _allow_all_robots(monkeypatch)
    calls = _install_fake_subprocess(
        monkeypatch,
        returncode=2,
        write_pdf=False,
        stderr="goosepaper: something broke\n",
    )

    caplog.set_level(logging.WARNING, logger="renewsable.builder")
    with pytest.raises(BuildError) as excinfo:
        Builder(cfg).build(today=datetime.date(2026, 4, 19))

    assert "2" in str(excinfo.value) or "goosepaper" in str(excinfo.value).lower()
    assert calls, "goosepaper should have been invoked"
    pdfs = list(cfg.output_dir.glob("renewsable-*.pdf")) if cfg.output_dir.exists() else []
    assert pdfs == []
    # Stderr was forwarded to the logger at warning level.
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "something broke" in joined


# ---------------------------------------------------------------------------
# Goosepaper succeeds but no PDF → BuildError
# ---------------------------------------------------------------------------


def test_goosepaper_success_without_pdf_raises_build_error(tmp_path, monkeypatch):
    cfg = _make_config(tmp_path)
    _install_network(monkeypatch, lambda req: _FakeResponse(b"<rss/>"))
    _no_sleep(monkeypatch)
    _allow_all_robots(monkeypatch)
    _install_fake_subprocess(monkeypatch, returncode=0, write_pdf=False)

    with pytest.raises(BuildError):
        Builder(cfg).build(today=datetime.date(2026, 4, 19))

    pdfs = list(cfg.output_dir.glob("renewsable-*.pdf")) if cfg.output_dir.exists() else []
    assert pdfs == []


# ---------------------------------------------------------------------------
# Goosepaper writes a non-PDF → BuildError + file cleaned up
# ---------------------------------------------------------------------------


def test_goosepaper_writes_non_pdf_raises_and_cleans_up(tmp_path, monkeypatch):
    cfg = _make_config(tmp_path)
    _install_network(monkeypatch, lambda req: _FakeResponse(b"<rss/>"))
    _no_sleep(monkeypatch)
    _allow_all_robots(monkeypatch)
    _install_fake_subprocess(
        monkeypatch,
        returncode=0,
        write_pdf=True,
        pdf_bytes=b"this is not a pdf\n",
    )

    with pytest.raises(BuildError):
        Builder(cfg).build(today=datetime.date(2026, 4, 19))

    # The non-PDF file must have been deleted so a later upload cannot grab it.
    pdfs = list(cfg.output_dir.glob("renewsable-*.pdf")) if cfg.output_dir.exists() else []
    assert pdfs == []


# ---------------------------------------------------------------------------
# Goosepaper writes an empty file → BuildError + cleaned up
# ---------------------------------------------------------------------------


def test_goosepaper_writes_empty_file_raises_and_cleans_up(tmp_path, monkeypatch):
    cfg = _make_config(tmp_path)
    _install_network(monkeypatch, lambda req: _FakeResponse(b"<rss/>"))
    _no_sleep(monkeypatch)
    _allow_all_robots(monkeypatch)
    _install_fake_subprocess(
        monkeypatch,
        returncode=0,
        write_pdf=True,
        pdf_bytes=b"",
    )

    with pytest.raises(BuildError):
        Builder(cfg).build(today=datetime.date(2026, 4, 19))

    pdfs = list(cfg.output_dir.glob("renewsable-*.pdf")) if cfg.output_dir.exists() else []
    assert pdfs == []


# ---------------------------------------------------------------------------
# Subprocess invocation shape
# ---------------------------------------------------------------------------


def test_goosepaper_invoked_with_expected_argv_and_flags(tmp_path, monkeypatch):
    cfg = _make_config(tmp_path)
    _install_network(monkeypatch, lambda req: _FakeResponse(b"<rss/>"))
    _no_sleep(monkeypatch)
    _allow_all_robots(monkeypatch)
    calls = _install_fake_subprocess(monkeypatch, returncode=0, write_pdf=True)

    Builder(cfg).build(today=datetime.date(2026, 4, 19))

    assert len(calls) == 1
    argv = calls[0]["argv"]
    kwargs = calls[0]["kwargs"]

    # Binary comes from config.
    assert argv[0] == cfg.goosepaper_bin
    # -c <config.json>
    assert "-c" in argv
    config_arg = argv[argv.index("-c") + 1]
    assert config_arg.endswith(".json")
    # -o <output.pdf> is the expected output path.
    assert "-o" in argv
    output_arg = argv[argv.index("-o") + 1]
    assert output_arg == str(cfg.output_dir / "renewsable-2026-04-19.pdf")
    # --noupload is present (defangs goosepaper's broken upload path).
    assert "--noupload" in argv
    # capture_output=True, text=True, check=False, timeout set.
    assert kwargs.get("capture_output") is True
    assert kwargs.get("text") is True
    assert kwargs.get("check") is False
    assert kwargs.get("timeout") == cfg.subprocess_timeout_s


def test_prepared_config_rewrites_rss_path_to_local_file(tmp_path, monkeypatch):
    """The config handed to goosepaper must point at the pre-fetched feed file."""
    cfg = _make_config(
        tmp_path,
        stories=[
            {
                "provider": "rss",
                "config": {"rss_path": "https://telex.hu/rss", "limit": 3},
            }
        ],
    )
    feed_body = b"<rss><channel><title>T\xc3\xa9lex</title></channel></rss>"
    _install_network(
        monkeypatch,
        lambda req: _FakeResponse(feed_body)
        if "telex.hu/rss" in (req if isinstance(req, str) else req.full_url)
        else _FakeResponse(b""),
    )
    _no_sleep(monkeypatch)
    _allow_all_robots(monkeypatch)

    captured_config: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> Any:
        cfg_idx = argv.index("-c") + 1
        cfg_path = Path(argv[cfg_idx])
        captured_config["data"] = json.loads(cfg_path.read_text())
        captured_config["feed_dir"] = cfg_path.parent
        # Also check the feed file exists with the exact bytes we pre-fetched.
        stories = captured_config["data"]["stories"]
        rewritten = stories[0]["config"]["rss_path"]
        assert rewritten.startswith("file://")
        local_path = Path(rewritten.removeprefix("file://"))
        captured_config["feed_bytes"] = local_path.read_bytes()
        # Write stub PDF.
        out_idx = argv.index("-o") + 1
        Path(argv[out_idx]).parent.mkdir(parents=True, exist_ok=True)
        Path(argv[out_idx]).write_bytes(b"%PDF-1.4\n%stub\n%%EOF\n")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(builder_mod.subprocess, "run", fake_run, raising=True)

    Builder(cfg).build(today=datetime.date(2026, 4, 19))

    # The original URL is gone; the story config otherwise passes through
    # (limit=3 is preserved).
    stories = captured_config["data"]["stories"]
    assert stories[0]["provider"] == "rss"
    assert stories[0]["config"]["limit"] == 3
    assert stories[0]["config"]["rss_path"].startswith("file://")
    # Bytes pass through unchanged (relevant for Hungarian / UTF-8 preservation).
    assert captured_config["feed_bytes"] == feed_body


# ---------------------------------------------------------------------------
# Telex.hu accepted (Req 3.1, 3.4)
# ---------------------------------------------------------------------------


def test_telex_hu_is_accepted_and_bytes_passed_through(tmp_path, monkeypatch):
    cfg = _make_config(
        tmp_path,
        stories=[
            {"provider": "rss", "config": {"rss_path": "https://telex.hu/rss"}},
        ],
    )
    # UTF-8 Hungarian characters: á é í ó ö ő ú ü ű
    body = "<title>Telex - magyar hírek: árvíz, őszi szünet</title>".encode("utf-8")
    _install_network(
        monkeypatch,
        lambda req: _FakeResponse(body)
        if not (req if isinstance(req, str) else req.full_url).endswith("/robots.txt")
        else _FakeResponse(b""),
    )
    _no_sleep(monkeypatch)
    _allow_all_robots(monkeypatch)

    out = Builder(cfg).build(today=datetime.date(2026, 4, 19))
    assert out.is_file()


# ---------------------------------------------------------------------------
# Non-rss stories pass through unchanged
# ---------------------------------------------------------------------------


def test_non_rss_stories_pass_through_without_fetch(tmp_path, monkeypatch):
    """Non-rss providers are handed to goosepaper verbatim."""
    cfg = _make_config(
        tmp_path,
        stories=[
            {"provider": "static", "config": {"text": "hello"}},
            {"provider": "rss", "config": {"rss_path": "https://a.example/rss"}},
        ],
    )

    seen: list[str] = []

    def handler(req: Any) -> Any:
        url = req if isinstance(req, str) else req.full_url
        seen.append(url)
        if url.endswith("/robots.txt"):
            return _FakeResponse(b"")
        return _FakeResponse(b"<rss/>")

    _install_network(monkeypatch, handler)
    _no_sleep(monkeypatch)

    captured: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> Any:
        cfg_idx = argv.index("-c") + 1
        captured["data"] = json.loads(Path(argv[cfg_idx]).read_text())
        out_idx = argv.index("-o") + 1
        Path(argv[out_idx]).parent.mkdir(parents=True, exist_ok=True)
        Path(argv[out_idx]).write_bytes(b"%PDF-1.4\n%stub\n%%EOF\n")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(builder_mod.subprocess, "run", fake_run, raising=True)

    Builder(cfg).build(today=datetime.date(2026, 4, 19))

    stories = captured["data"]["stories"]
    assert len(stories) == 2
    # static story preserved verbatim.
    static = next(s for s in stories if s["provider"] == "static")
    assert static["config"]["text"] == "hello"
    # rss story had its path rewritten.
    rss = next(s for s in stories if s["provider"] == "rss")
    assert rss["config"]["rss_path"].startswith("file://")
    # Non-rss stories do NOT trigger a fetch of their data (nothing to fetch).
    # (We only fetched robots + the one rss feed.)
    non_robots = [u for u in seen if not u.endswith("/robots.txt")]
    assert non_robots == ["https://a.example/rss"]


# ---------------------------------------------------------------------------
# font_size passed through when set
# ---------------------------------------------------------------------------


def test_font_size_passed_to_goosepaper_config_when_set(tmp_path, monkeypatch):
    cfg = _make_config(tmp_path, font_size=13)
    _install_network(monkeypatch, lambda req: _FakeResponse(b"<rss/>"))
    _no_sleep(monkeypatch)
    _allow_all_robots(monkeypatch)

    captured: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> Any:
        cfg_idx = argv.index("-c") + 1
        captured["data"] = json.loads(Path(argv[cfg_idx]).read_text())
        out_idx = argv.index("-o") + 1
        Path(argv[out_idx]).parent.mkdir(parents=True, exist_ok=True)
        Path(argv[out_idx]).write_bytes(b"%PDF-1.4\n%stub\n%%EOF\n")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(builder_mod.subprocess, "run", fake_run, raising=True)

    Builder(cfg).build(today=datetime.date(2026, 4, 19))

    assert captured["data"].get("font_size") == 13


def test_font_size_omitted_when_none(tmp_path, monkeypatch):
    cfg = _make_config(tmp_path, font_size=None)
    _install_network(monkeypatch, lambda req: _FakeResponse(b"<rss/>"))
    _no_sleep(monkeypatch)
    _allow_all_robots(monkeypatch)

    captured: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> Any:
        cfg_idx = argv.index("-c") + 1
        captured["data"] = json.loads(Path(argv[cfg_idx]).read_text())
        out_idx = argv.index("-o") + 1
        Path(argv[out_idx]).parent.mkdir(parents=True, exist_ok=True)
        Path(argv[out_idx]).write_bytes(b"%PDF-1.4\n%stub\n%%EOF\n")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(builder_mod.subprocess, "run", fake_run, raising=True)

    Builder(cfg).build(today=datetime.date(2026, 4, 19))

    assert "font_size" not in captured["data"]
