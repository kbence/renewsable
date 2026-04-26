"""Tests for ``renewsable.epub`` — EPUB 3 assembly module.

Spec coverage:

- requirements.md → 1.1, 1.2, 4.1, 4.2, 4.3, 4.4, 5.1, 5.2, 5.3, 6.1, 6.2, 6.3, 6.4
- design.md → "Components and Interfaces" → "Output" → ``epub`` module
  (Responsibilities, Service Interface, Batch/Job Contract, Implementation Notes).
- design.md → "Logical Data Model" (EPUB internal layout).
- design.md → "Data Contracts & Integration" (metadata fields, image embedding
  contract, deterministic dc:identifier).

Monkeypatch convention: the module under test exposes
``urllib_request`` and ``ebooklib_epub`` as module-level aliases so tests
can ``monkeypatch.setattr(epub_mod.urllib_request, "urlopen", fake)`` /
``monkeypatch.setattr(epub_mod.ebooklib_epub, "write_epub", raising_fn)``.
"""

from __future__ import annotations

import datetime
import io
import urllib.error
import uuid
import zipfile
from pathlib import Path
from typing import Iterable

import pytest
from ebooklib import epub as ebooklib_epub

from renewsable import epub as epub_mod
from renewsable.articles import Article
from renewsable.errors import BuildError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


PNG_BYTES = b"\x89PNG\r\n\x1a\nFAKE"
JPG_BYTES = b"\xff\xd8\xff\xe0FAKE"


class FakeResponse:
    """Minimal urlopen() result implementing context-manager + read()."""

    def __init__(self, body: bytes, content_type: str = "image/png") -> None:
        self._body = body
        self.headers = {"Content-Type": content_type}

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _install_fake_urlopen(
    monkeypatch: pytest.MonkeyPatch,
    responses: dict[str, "FakeResponse | Exception"],
) -> list[str]:
    """Patch ``epub_mod.urllib_request.urlopen``; return call log."""
    calls: list[str] = []

    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        # ``req`` is a urllib.request.Request — pull its full URL.
        url = req.full_url if hasattr(req, "full_url") else str(req)
        calls.append(url)
        result = responses.get(url)
        if result is None:
            raise urllib.error.URLError(f"unexpected URL in test: {url}")
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(epub_mod.urllib_request, "urlopen", fake_urlopen)
    # No real sleeping in tests.
    monkeypatch.setattr(epub_mod._time, "sleep", lambda *_a, **_k: None)
    return calls


def _make_article(idx: int, html: str) -> Article:
    return Article(
        title=f"Story {idx}",
        html=html,
        source_url=f"https://example.com/article-{idx}",
    )


def _read_zip(path: Path) -> zipfile.ZipFile:
    return zipfile.ZipFile(path, "r")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_mimetype_is_first_zip_entry_stored_uncompressed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_urlopen(monkeypatch, {})
    out = tmp_path / "book.epub"
    epub_mod.assemble(
        [_make_article(1, "<p>hello</p>")],
        today=datetime.date(2026, 4, 26),
        output_path=out,
        ua="renewsable-test/1.0",
        retries=1,
        backoff_s=0.0,
    )
    with _read_zip(out) as zf:
        infos = zf.infolist()
        assert infos[0].filename == "mimetype"
        assert zf.read("mimetype") == b"application/epub+zip"
        assert infos[0].compress_type == zipfile.ZIP_STORED


def test_container_xml_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_urlopen(monkeypatch, {})
    out = tmp_path / "book.epub"
    epub_mod.assemble(
        [_make_article(1, "<p>hi</p>")],
        today=datetime.date(2026, 4, 26),
        output_path=out,
        ua="renewsable-test/1.0",
        retries=1,
        backoff_s=0.0,
    )
    with _read_zip(out) as zf:
        assert "META-INF/container.xml" in zf.namelist()


def test_round_trip_nav_has_one_entry_per_article(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_urlopen(monkeypatch, {})
    arts = [
        _make_article(1, "<p>one</p>"),
        _make_article(2, "<p>two</p>"),
        _make_article(3, "<p>three</p>"),
    ]
    out = tmp_path / "book.epub"
    epub_mod.assemble(
        arts,
        today=datetime.date(2026, 4, 26),
        output_path=out,
        ua="renewsable-test/1.0",
        retries=1,
        backoff_s=0.0,
    )

    book = ebooklib_epub.read_epub(str(out))
    # toc is a list of Link objects (or tuples for sections); ours is flat.
    titles = [link.title for link in book.toc]
    assert titles == ["Story 1", "Story 2", "Story 3"]


def test_metadata_fields_set_correctly_and_identifier_deterministic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_urlopen(monkeypatch, {})
    today = datetime.date(2026, 4, 26)
    arts = [_make_article(1, "<p>hi</p>")]
    out_a = tmp_path / "a.epub"
    out_b = tmp_path / "b.epub"
    for out in (out_a, out_b):
        epub_mod.assemble(
            arts,
            today=today,
            output_path=out,
            ua="renewsable-test/1.0",
            retries=1,
            backoff_s=0.0,
        )

    book_a = ebooklib_epub.read_epub(str(out_a))
    book_b = ebooklib_epub.read_epub(str(out_b))

    # dc:title
    title_meta = book_a.get_metadata("DC", "title")
    assert title_meta and title_meta[0][0] == "Renewsable Daily — 2026-04-26"
    # dc:creator
    creator_meta = book_a.get_metadata("DC", "creator")
    assert creator_meta and creator_meta[0][0] == "Renewsable"
    # dc:language
    lang_meta = book_a.get_metadata("DC", "language")
    assert lang_meta and lang_meta[0][0] == "en"
    # dc:date
    date_meta = book_a.get_metadata("DC", "date")
    assert date_meta and date_meta[0][0] == "2026-04-26"
    # dc:identifier — deterministic across runs for same date.
    expected_uuid = uuid.uuid5(uuid.NAMESPACE_URL, f"renewsable-daily-{today.isoformat()}")
    expected_id = f"urn:uuid:{expected_uuid}"
    id_a = book_a.get_metadata("DC", "identifier")
    id_b = book_b.get_metadata("DC", "identifier")
    assert id_a and id_a[0][0] == expected_id
    assert id_b and id_b[0][0] == expected_id


def test_image_embedded_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    img_url = "https://img.example.com/cat.png"
    _install_fake_urlopen(
        monkeypatch,
        {img_url: FakeResponse(PNG_BYTES, content_type="image/png")},
    )
    art = Article(
        title="With image",
        html=f'<p>before</p><img src="{img_url}" alt="a cat"/><p>after</p>',
        source_url="https://example.com/a1",
    )
    out = tmp_path / "book.epub"
    epub_mod.assemble(
        [art],
        today=datetime.date(2026, 4, 26),
        output_path=out,
        ua="renewsable-test/1.0",
        retries=1,
        backoff_s=0.0,
    )
    with _read_zip(out) as zf:
        names = zf.namelist()
        # An images/img-<sha12>.png file should exist somewhere in the archive
        # (ebooklib places items under EPUB/ but we only need to check by suffix).
        image_names = [n for n in names if n.endswith(".png") and "images/img-" in n]
        assert image_names, f"expected image entry; namelist={names!r}"

    # Re-open via ebooklib and verify the chapter HTML references the
    # internal image path (not the original URL).
    book = ebooklib_epub.read_epub(str(out))
    html_items = [
        it for it in book.get_items() if it.get_type() == 9  # ITEM_DOCUMENT
    ]
    chapter_html_blob = b"".join(it.get_content() for it in html_items)
    assert img_url.encode() not in chapter_html_blob
    assert b"images/img-" in chapter_html_blob


def test_image_placeholder_on_fetch_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    img_url = "https://img.example.com/broken.png"
    _install_fake_urlopen(
        monkeypatch,
        {img_url: urllib.error.URLError("boom")},
    )
    art = Article(
        title="With broken image",
        html=f'<p>before</p><img src="{img_url}" alt="a cat"/><p>after</p>',
        source_url="https://example.com/a1",
    )
    out = tmp_path / "book.epub"
    epub_mod.assemble(
        [art],
        today=datetime.date(2026, 4, 26),
        output_path=out,
        ua="renewsable-test/1.0",
        retries=1,
        backoff_s=0.0,
    )
    book = ebooklib_epub.read_epub(str(out))
    html_items = [it for it in book.get_items() if it.get_type() == 9]
    blob = b"".join(it.get_content() for it in html_items)
    assert b"renewsable-missing-image" in blob
    assert f'data-src="{img_url}"'.encode() in blob
    assert b"image unavailable" in blob


def test_oversize_image_triggers_placeholder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    img_url = "https://img.example.com/big.png"
    big_body = b"x" * 5000
    _install_fake_urlopen(
        monkeypatch,
        {img_url: FakeResponse(big_body, content_type="image/png")},
    )
    art = Article(
        title="Oversize",
        html=f'<img src="{img_url}" alt="big"/>',
        source_url="https://example.com/a1",
    )
    out = tmp_path / "book.epub"
    epub_mod.assemble(
        [art],
        today=datetime.date(2026, 4, 26),
        output_path=out,
        ua="renewsable-test/1.0",
        retries=1,
        backoff_s=0.0,
        image_max_bytes=100,
    )
    with _read_zip(out) as zf:
        names = zf.namelist()
        assert not [n for n in names if "images/img-" in n], (
            f"oversize image must not be embedded; namelist={names!r}"
        )
    book = ebooklib_epub.read_epub(str(out))
    html_items = [it for it in book.get_items() if it.get_type() == 9]
    blob = b"".join(it.get_content() for it in html_items)
    assert b"renewsable-missing-image" in blob


def test_non_image_content_type_triggers_placeholder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    img_url = "https://img.example.com/sneaky"
    _install_fake_urlopen(
        monkeypatch,
        {img_url: FakeResponse(b"<html>nope</html>", content_type="text/html")},
    )
    art = Article(
        title="Sneaky",
        html=f'<img src="{img_url}" alt="x"/>',
        source_url="https://example.com/a1",
    )
    out = tmp_path / "book.epub"
    epub_mod.assemble(
        [art],
        today=datetime.date(2026, 4, 26),
        output_path=out,
        ua="renewsable-test/1.0",
        retries=1,
        backoff_s=0.0,
    )
    book = ebooklib_epub.read_epub(str(out))
    html_items = [it for it in book.get_items() if it.get_type() == 9]
    blob = b"".join(it.get_content() for it in html_items)
    assert b"renewsable-missing-image" in blob
    with _read_zip(out) as zf:
        assert not [n for n in zf.namelist() if "images/img-" in n]


def test_image_dedup_across_articles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    img_url = "https://img.example.com/shared.png"
    calls = _install_fake_urlopen(
        monkeypatch,
        {img_url: FakeResponse(PNG_BYTES, content_type="image/png")},
    )
    arts = [
        Article(
            title="One",
            html=f'<img src="{img_url}" alt="x"/>',
            source_url="https://example.com/a1",
        ),
        Article(
            title="Two",
            html=f'<img src="{img_url}" alt="y"/>',
            source_url="https://example.com/a2",
        ),
    ]
    out = tmp_path / "book.epub"
    epub_mod.assemble(
        arts,
        today=datetime.date(2026, 4, 26),
        output_path=out,
        ua="renewsable-test/1.0",
        retries=1,
        backoff_s=0.0,
    )
    # Image fetched only once.
    assert calls.count(img_url) == 1
    with _read_zip(out) as zf:
        image_names = [n for n in zf.namelist() if "images/img-" in n]
        assert len(image_names) == 1, f"expected one image item, got {image_names!r}"


def test_stylesheet_registered_with_heading_alignment_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_urlopen(monkeypatch, {})
    out = tmp_path / "book.epub"
    epub_mod.assemble(
        [_make_article(1, "<p>hi</p>"), _make_article(2, "<p>two</p>")],
        today=datetime.date(2026, 4, 26),
        output_path=out,
        ua="renewsable-test/1.0",
        retries=1,
        backoff_s=0.0,
    )
    book = ebooklib_epub.read_epub(str(out))
    css_items = [it for it in book.get_items() if it.media_type == "text/css"]
    assert len(css_items) == 1, f"expected one css item, got {css_items!r}"
    css_text = css_items[0].get_content().decode("utf-8")
    assert "h1" in css_text
    assert "text-align" in css_text
    assert "left" in css_text

    with _read_zip(out) as zf:
        chapter_names = [
            n for n in zf.namelist() if n.endswith(".xhtml") and "chapters/" in n
        ]
        assert chapter_names, "expected at least one chapter in zip"
        for name in chapter_names:
            content = zf.read(name).decode("utf-8")
            assert 'rel="stylesheet"' in content
            assert "../styles.css" in content


def test_internal_failure_unlinks_partial_and_raises_build_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_urlopen(monkeypatch, {})
    out = tmp_path / "book.epub"

    def boom(*_args, **_kwargs):
        # Simulate ebooklib having written a partial file before crashing.
        out.write_bytes(b"partial")
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(epub_mod.ebooklib_epub, "write_epub", boom)

    with pytest.raises(BuildError):
        epub_mod.assemble(
            [_make_article(1, "<p>hi</p>")],
            today=datetime.date(2026, 4, 26),
            output_path=out,
            ua="renewsable-test/1.0",
            retries=1,
            backoff_s=0.0,
        )
    assert not out.exists()
