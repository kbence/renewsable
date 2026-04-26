"""Unit tests for :mod:`renewsable.builder` (EPUB pipeline, task 3.2).

Design reference: ``.kiro/specs/epub-output/design.md`` →
"Components and Interfaces" → "Orchestration" → "Builder (modified)".

Requirements covered:
- 1.1 Produce one EPUB per day at ``<output_dir>/renewsable-<YYYY-MM-DD>.epub``.
- 1.4 Re-runs on the same date overwrite the previous file.
- 2.1 No goosepaper / WeasyPrint anywhere in the build pipeline.
- 3.5 Zero usable articles → ``BuildError``, no partial artefact.
- 7.1, 7.2, 7.3 EPUB structure validation: mimetype-first, uncompressed,
  ``META-INF/container.xml`` present.

Test seams
----------
``builder.articles_mod`` and ``builder.epub_mod`` are bound at module level
so tests can ``monkeypatch.setattr`` them in one place. The real
``epub.assemble`` is in-process pure Python; tests use it directly when
they want a real EPUB on disk and stub it when they only care about the
orchestration shape.
"""

from __future__ import annotations

import datetime
import zipfile
from pathlib import Path
from typing import Any

import pytest

from renewsable import builder as builder_mod
from renewsable.articles import Article
from renewsable.builder import Builder
from renewsable.config import Config
from renewsable.errors import BuildError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    tmp_path: Path,
    *,
    stories: list[dict[str, Any]] | None = None,
    user_agent: str = "renewsable-test/1.0",
    feed_fetch_retries: int = 3,
    feed_fetch_backoff_s: float = 0.01,
) -> Config:
    """Construct a valid Config for builder tests under the new schema."""
    return Config(
        schedule_time="05:30",
        output_dir=tmp_path / "out",
        remarkable_folder="/News",
        stories=stories
        if stories is not None
        else [{"provider": "rss", "config": {"rss_path": "https://example.com/rss"}}],
        user_agent=user_agent,
        feed_fetch_retries=feed_fetch_retries,
        feed_fetch_backoff_s=feed_fetch_backoff_s,
    )


def _sample_articles() -> list[Article]:
    """Two ready-to-publish articles with no images (so no fetches needed)."""
    return [
        Article(
            title="First headline",
            html="<div><p>First body paragraph.</p></div>",
            source_url="https://example.com/a",
        ),
        Article(
            title="Second headline",
            html="<div><p>Second body paragraph.</p></div>",
            source_url="https://example.com/b",
        ),
    ]


def _patch_collect(
    monkeypatch: pytest.MonkeyPatch, articles: list[Article]
) -> list[dict[str, Any]]:
    """Replace ``articles.collect`` with a stub returning ``articles``.

    Returns a list that captures the keyword args of each invocation.
    """
    calls: list[dict[str, Any]] = []

    def fake_collect(stories, **kwargs):
        calls.append({"stories": stories, **kwargs})
        return list(articles)

    monkeypatch.setattr(builder_mod.articles_mod, "collect", fake_collect)
    return calls


# ---------------------------------------------------------------------------
# Happy path: build with real epub.assemble (no images)
# ---------------------------------------------------------------------------


def test_build_happy_path_returns_path_to_valid_epub(tmp_path, monkeypatch):
    cfg = _make_config(tmp_path)
    _patch_collect(monkeypatch, _sample_articles())

    today = datetime.date(2026, 4, 19)
    out = Builder(cfg).build(today=today)

    assert out == cfg.output_dir / "renewsable-2026-04-19.epub"
    assert out.is_file()
    assert out.stat().st_size > 0
    # Round-trip: file is a valid EPUB ZIP with the expected structure.
    with zipfile.ZipFile(out, "r") as zf:
        names = zf.namelist()
        assert names[0] == "mimetype"
        assert zf.read("mimetype") == b"application/epub+zip"
        assert zf.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
        assert "META-INF/container.xml" in names


def test_build_passes_config_through_to_collect(tmp_path, monkeypatch):
    """``articles.collect`` is invoked with the values pulled from Config."""
    cfg = _make_config(
        tmp_path,
        user_agent="renewsable/0.9 (+test)",
        feed_fetch_retries=5,
        feed_fetch_backoff_s=0.25,
    )
    calls = _patch_collect(monkeypatch, _sample_articles())

    Builder(cfg).build(today=datetime.date(2026, 4, 19))

    assert len(calls) == 1
    call = calls[0]
    assert call["stories"] is cfg.stories
    assert call["ua"] == cfg.user_agent
    assert call["retries"] == cfg.feed_fetch_retries
    assert call["backoff_s"] == cfg.feed_fetch_backoff_s
    # The robots cache must be the same dict instance held on the Builder.
    assert call["robots_cache"] is not None


# ---------------------------------------------------------------------------
# Zero articles → BuildError, no file
# ---------------------------------------------------------------------------


def test_build_raises_when_no_articles_collected(tmp_path, monkeypatch):
    cfg = _make_config(tmp_path)
    _patch_collect(monkeypatch, [])

    # Also patch epub.assemble so a hypothetical bug invoking it would surface.
    def boom(*a, **kw):  # pragma: no cover - must not be called
        raise AssertionError("epub.assemble must not be called when articles==[]")

    monkeypatch.setattr(builder_mod.epub_mod, "assemble", boom)

    with pytest.raises(BuildError) as excinfo:
        Builder(cfg).build(today=datetime.date(2026, 4, 19))

    assert "no usable articles" in str(excinfo.value).lower()
    out = cfg.output_dir / "renewsable-2026-04-19.epub"
    assert not out.exists()


# ---------------------------------------------------------------------------
# Default ``today`` resolution
# ---------------------------------------------------------------------------


def test_build_defaults_today_to_local_date(tmp_path, monkeypatch):
    cfg = _make_config(tmp_path)
    _patch_collect(monkeypatch, _sample_articles())

    fixed = datetime.date(2026, 7, 4)

    class _FrozenDate(datetime.date):
        @classmethod
        def today(cls):  # type: ignore[override]
            return fixed

    monkeypatch.setattr(builder_mod._dt, "date", _FrozenDate)

    out = Builder(cfg).build()
    assert out.name == f"renewsable-{fixed.isoformat()}.epub"


# ---------------------------------------------------------------------------
# Re-run with the same date overwrites
# ---------------------------------------------------------------------------


def test_build_twice_same_date_overwrites(tmp_path, monkeypatch):
    cfg = _make_config(tmp_path)
    _patch_collect(monkeypatch, _sample_articles())

    today = datetime.date(2026, 4, 19)
    builder = Builder(cfg)
    out1 = builder.build(today=today)
    assert out1.is_file()
    first_bytes = out1.read_bytes()

    # Force a perceptibly different EPUB on the second run by changing the
    # article set (a different number of chapters changes the EPUB bytes).
    _patch_collect(
        monkeypatch,
        [
            Article(
                title="Only headline",
                html="<div><p>only</p></div>",
                source_url="https://example.com/only",
            )
        ],
    )
    out2 = builder.build(today=today)
    assert out2 == out1

    second_bytes = out2.read_bytes()
    assert second_bytes != first_bytes, "second build must have overwritten the file"

    # Exactly one EPUB for that date exists.
    epubs = sorted(cfg.output_dir.glob("renewsable-*.epub"))
    assert epubs == [out1]


# ---------------------------------------------------------------------------
# _validate_epub direct tests
# ---------------------------------------------------------------------------


def _write_minimal_valid_epub(path: Path) -> None:
    """Hand-roll a minimal valid EPUB ZIP for validation-only tests."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        zf.writestr(info, b"application/epub+zip")
        zf.writestr(
            "META-INF/container.xml",
            b'<?xml version="1.0"?><container/>',
        )


def test_validate_epub_accepts_minimal_valid_epub(tmp_path):
    out = tmp_path / "ok.epub"
    _write_minimal_valid_epub(out)
    # Must not raise; must not delete the file.
    Builder._validate_epub(out)
    assert out.exists()


def test_validate_epub_rejects_missing_file(tmp_path):
    out = tmp_path / "missing.epub"
    with pytest.raises(BuildError):
        Builder._validate_epub(out)


def test_validate_epub_rejects_empty_file(tmp_path):
    out = tmp_path / "empty.epub"
    out.write_bytes(b"")
    with pytest.raises(BuildError):
        Builder._validate_epub(out)
    assert not out.exists()


def test_validate_epub_rejects_non_zip_and_unlinks(tmp_path):
    out = tmp_path / "notzip.epub"
    out.write_bytes(b"this is plain text, not a zip archive")
    with pytest.raises(BuildError):
        Builder._validate_epub(out)
    assert not out.exists()


def test_validate_epub_rejects_wrong_mimetype_and_unlinks(tmp_path):
    out = tmp_path / "bad-mime.epub"
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w") as zf:
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        zf.writestr(info, b"text/plain")
        zf.writestr("META-INF/container.xml", b"<container/>")

    with pytest.raises(BuildError):
        Builder._validate_epub(out)
    assert not out.exists()


def test_validate_epub_rejects_compressed_mimetype_and_unlinks(tmp_path):
    out = tmp_path / "compressed-mime.epub"
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Default DEFLATED on the entry violates the EPUB spec.
        zf.writestr("mimetype", b"application/epub+zip")
        zf.writestr("META-INF/container.xml", b"<container/>")

    with pytest.raises(BuildError):
        Builder._validate_epub(out)
    assert not out.exists()


def test_validate_epub_rejects_missing_container_and_unlinks(tmp_path):
    out = tmp_path / "no-container.epub"
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w") as zf:
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        zf.writestr(info, b"application/epub+zip")
        # No META-INF/container.xml.

    with pytest.raises(BuildError):
        Builder._validate_epub(out)
    assert not out.exists()


def test_validate_epub_rejects_mimetype_not_first_entry_and_unlinks(tmp_path):
    out = tmp_path / "mime-not-first.epub"
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("META-INF/container.xml", b"<container/>")
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        zf.writestr(info, b"application/epub+zip")

    with pytest.raises(BuildError):
        Builder._validate_epub(out)
    assert not out.exists()


# ---------------------------------------------------------------------------
# build() integrates validation: a stubbed assemble that writes garbage is
# caught by _validate_epub and the file is unlinked.
# ---------------------------------------------------------------------------


def test_build_unlinks_when_assemble_writes_invalid_epub(tmp_path, monkeypatch):
    cfg = _make_config(tmp_path)
    _patch_collect(monkeypatch, _sample_articles())

    def fake_assemble(articles, *, today, output_path, **kwargs):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"definitely not an epub")

    monkeypatch.setattr(builder_mod.epub_mod, "assemble", fake_assemble)

    with pytest.raises(BuildError):
        Builder(cfg).build(today=datetime.date(2026, 4, 19))

    out = cfg.output_dir / "renewsable-2026-04-19.epub"
    assert not out.exists()
