"""End-to-end pipeline integration test for the EPUB output feature.

Spec coverage:

- requirements.md → 1.1, 1.2, 1.4, 3.5, 4.1, 4.2, 4.3, 4.4, 5.1, 5.2, 5.3,
  6.1, 6.2, 6.3, 6.4, 7.1, 7.2.
- design.md → "System Flows" sequence diagram for the full pipeline
  (RSS → articles → epub → upload).

The unit tests exercise each stage in isolation; this test composes the
real :class:`renewsable.builder.Builder`, the real
:func:`renewsable.articles.collect`, and the real
:func:`renewsable.epub.assemble` against mocked network seams only. Two
seams are mocked:

1. ``http.fetch_with_retry`` (used by ``articles`` for both RSS feed and
   article-page fetches), reached via ``articles_mod._http.fetch_with_retry``.
2. ``epub.urllib_request.urlopen`` (used by ``_fetch_image_with_mime`` for
   image downloads — image fetches do *not* go through ``fetch_with_retry``).

Robots checks are stubbed permissive and ``time.sleep`` is patched out so
retry loops do not slow the suite down.
"""

from __future__ import annotations

import datetime
import re
import urllib.error
import zipfile
from pathlib import Path

import pytest
from ebooklib import epub as ebooklib_epub

from renewsable import articles as articles_mod
from renewsable import builder as builder_mod
from renewsable import epub as epub_mod
from renewsable import http as http_mod  # noqa: F401  (kept for symmetry)
from renewsable.config import Config
from renewsable.errors import BuildError


# ---------------------------------------------------------------------------
# Canned payloads
# ---------------------------------------------------------------------------


FEED1_URL = "https://example.com/feed1.xml"
FEED2_URL = "https://other.com/feed2.xml"

ARTICLE_A_URL = "https://example.com/article-a"
ARTICLE_B_URL = "https://example.com/article-b"
ARTICLE_C_URL = "https://other.com/article-c"

REL_IMG_RESOLVED = "https://example.com/img/foo.png"
ABS_IMG = "https://cdn.example.com/abs.jpg"
BROKEN_IMG = "https://other.com/broken.png"


def _rss(items_xml: str) -> bytes:
    return (
        '<?xml version="1.0"?>'
        '<rss version="2.0"><channel>'
        "<title>Test Feed</title>"
        "<link>https://example.com</link>"
        "<description>Test</description>"
        f"{items_xml}"
        "</channel></rss>"
    ).encode("utf-8")


FEED1_XML = _rss(
    "<item><title>Article A</title>"
    f"<link>{ARTICLE_A_URL}</link>"
    "<description><![CDATA[<p>RSS desc A</p>]]></description></item>"
    "<item><title>Article B</title>"
    f"<link>{ARTICLE_B_URL}</link>"
    "<description><![CDATA[<p>RSS desc B</p>]]></description></item>"
)

FEED2_XML = _rss(
    "<item><title>Article C</title>"
    f"<link>{ARTICLE_C_URL}</link>"
    "<description><![CDATA[<p>RSS desc C</p>]]></description></item>"
)

# Empty-description variants for the failure test.
FEED1_XML_EMPTY = _rss(
    "<item><title>Article A</title>"
    f"<link>{ARTICLE_A_URL}</link>"
    "<description></description></item>"
    "<item><title>Article B</title>"
    f"<link>{ARTICLE_B_URL}</link>"
    "<description></description></item>"
)
FEED2_XML_EMPTY = _rss(
    "<item><title>Article C</title>"
    f"<link>{ARTICLE_C_URL}</link>"
    "<description></description></item>"
)


# Article HTML with a relative image and an absolute image. Body padded so
# the extractor chain (trafilatura primary, readability-lxml secondary)
# picks the <article> element rather than falling back to the RSS summary.
ARTICLE_A_HTML = (
    "<html><body><article>"
    "<h1>Article A</h1>"
    "<p>This is the article body, long enough to be considered the main "
    "content. It must exceed the readability threshold so it does not "
    "fallback. Lorem ipsum dolor sit amet consectetur adipiscing elit sed "
    "do eiusmod tempor incididunt ut labore et dolore magna aliqua.</p>"
    "<p>Second paragraph with more content to ensure readability picks "
    "this up. Ut enim ad minim veniam quis nostrud exercitation ullamco "
    "laboris nisi ut aliquip ex ea commodo consequat.</p>"
    '<p><img src="/img/foo.png" alt="foo"></p>'
    f'<p><img src="{ABS_IMG}" alt="abs"></p>'
    "</article></body></html>"
).encode("utf-8")


ARTICLE_C_HTML = (
    "<html><body><article>"
    "<h1>Article C</h1>"
    "<p>Article C body, long enough to clear readability thresholds. "
    "Lorem ipsum dolor sit amet consectetur adipiscing elit sed do "
    "eiusmod tempor incididunt ut labore et dolore magna aliqua.</p>"
    "<p>Second paragraph for Article C. Ut enim ad minim veniam quis "
    "nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo "
    "consequat.</p>"
    '<p><img src="/broken.png" alt="broken"></p>'
    "</article></body></html>"
).encode("utf-8")


# Minimal-but-real-enough image payloads. The Content-Type header drives
# epub's MIME decision; bytes only need to exist.
PNG_BYTES = b"\x89PNG\r\n\x1a\nFAKEPNG"
JPG_BYTES = b"\xff\xd8\xff\xe0FAKEJPG"


# ---------------------------------------------------------------------------
# Mocking infrastructure
# ---------------------------------------------------------------------------


class _FakeImageResponse:
    """urlopen() result shape used by ``epub._fetch_image_with_mime``."""

    def __init__(self, body: bytes, content_type: str) -> None:
        self._body = body
        self.headers = {"Content-Type": content_type}

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeImageResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _install_fetch_with_retry(
    monkeypatch: pytest.MonkeyPatch,
    responses: dict[str, "bytes | Exception"],
) -> list[str]:
    """Patch ``articles._http.fetch_with_retry`` to dispatch by URL."""
    calls: list[str] = []

    def fake_fetch(url, *, ua, retries, backoff_s, timeout_s=30.0):  # noqa: ARG001
        calls.append(url)
        result = responses.get(url)
        if result is None:
            raise urllib.error.URLError(f"unexpected URL in test: {url}")
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(articles_mod._http, "fetch_with_retry", fake_fetch)
    monkeypatch.setattr(
        articles_mod._http, "robots_allows", lambda *a, **kw: True
    )
    return calls


def _install_image_urlopen(
    monkeypatch: pytest.MonkeyPatch,
    responses: dict[str, "_FakeImageResponse | Exception"],
) -> list[str]:
    """Patch ``epub.urllib_request.urlopen`` to dispatch by URL."""
    calls: list[str] = []

    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        url = req.full_url if hasattr(req, "full_url") else str(req)
        calls.append(url)
        result = responses.get(url)
        if result is None:
            raise urllib.error.URLError(f"unexpected image URL in test: {url}")
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(epub_mod.urllib_request, "urlopen", fake_urlopen)
    monkeypatch.setattr(epub_mod._time, "sleep", lambda *_a, **_kw: None)
    return calls


def _make_config(tmp_path: Path) -> Config:
    return Config(
        schedule_time="05:30",
        output_dir=tmp_path.resolve(),
        remarkable_folder="/News",
        stories=[
            {"provider": "rss", "config": {"rss_path": FEED1_URL}},
            {"provider": "rss", "config": {"rss_path": FEED2_URL}},
        ],
        log_dir=tmp_path.resolve() / "logs",
        feed_fetch_retries=1,
        feed_fetch_backoff_s=0.1,
        upload_retries=1,
        upload_backoff_s=0.1,
    )


def _read_chapter_html_by_href(
    book: ebooklib_epub.EpubBook, href: str
) -> str:
    """Look up a chapter document by its file name and return decoded content."""
    item = book.get_item_with_href(href)
    if item is None:
        raise AssertionError(f"no item with href {href!r} in book")
    content = item.get_content()
    if isinstance(content, bytes):
        return content.decode("utf-8")
    return str(content)


# ---------------------------------------------------------------------------
# Test 1: full happy-path pipeline
# ---------------------------------------------------------------------------


def test_full_pipeline_happy_path_produces_valid_epub_with_all_features(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Compose the real Builder + articles + epub against mocked network.

    Covers requirements 1.1, 1.2, 3.3, 4.1, 4.2, 4.3, 4.4, 5.1, 5.2, 5.3,
    6.1, 6.2, 6.3, 6.4, 7.1, 7.2 in their composed form.
    """
    today = datetime.date(2026, 4, 26)
    config = _make_config(tmp_path)

    _install_fetch_with_retry(
        monkeypatch,
        {
            FEED1_URL: FEED1_XML,
            FEED2_URL: FEED2_XML,
            ARTICLE_A_URL: ARTICLE_A_HTML,
            # Article B: page fetch fails -> RSS-description fallback (Req 3.3).
            ARTICLE_B_URL: urllib.error.URLError("simulated B fetch failure"),
            ARTICLE_C_URL: ARTICLE_C_HTML,
        },
    )

    _install_image_urlopen(
        monkeypatch,
        {
            REL_IMG_RESOLVED: _FakeImageResponse(PNG_BYTES, "image/png"),
            ABS_IMG: _FakeImageResponse(JPG_BYTES, "image/jpeg"),
            # Article C image fails -> placeholder (Req 4.3, 4.4).
            BROKEN_IMG: urllib.error.URLError("simulated image failure"),
        },
    )

    epub_path = builder_mod.Builder(config).build(today=today)

    # Filename: no profile suffix. (Req 7.2)
    assert epub_path == config.output_dir / "renewsable-2026-04-26.epub"
    # File exists on disk. (Req 1.1, 7.1)
    assert epub_path.exists()

    # Open and inspect with ebooklib. (Req 1.2)
    book = ebooklib_epub.read_epub(str(epub_path))

    # ---- Metadata (Req 6.1, 6.2, 6.3, 6.4) ----
    title_md = book.get_metadata("http://purl.org/dc/elements/1.1/", "title")
    assert title_md and title_md[0][0] == "Renewsable Daily — 2026-04-26"

    creator_md = book.get_metadata("http://purl.org/dc/elements/1.1/", "creator")
    assert creator_md and creator_md[0][0] == "Renewsable"

    lang_md = book.get_metadata("http://purl.org/dc/elements/1.1/", "language")
    assert lang_md and lang_md[0][0] == "en"

    date_md = book.get_metadata("http://purl.org/dc/elements/1.1/", "date")
    assert date_md and date_md[0][0] == "2026-04-26"

    # ---- TOC: exactly 3 entries in source order. (Req 5.1, 5.2, 5.3) ----
    toc_titles = [link.title for link in book.toc]
    assert toc_titles == ["Article A", "Article B", "Article C"]
    toc_hrefs = [link.href for link in book.toc]
    assert toc_hrefs == [
        "chapters/article-001.xhtml",
        "chapters/article-002.xhtml",
        "chapters/article-003.xhtml",
    ]

    # ---- Article A: relative image resolved + embedded; absolute embedded;
    # original URLs not present in chapter HTML. (Req 4.1, 4.2) ----
    chapter_a = _read_chapter_html_by_href(book, "chapters/article-001.xhtml")
    embedded_pat = re.compile(r"images/img-[0-9a-f]{12}\.(png|jpg|jpeg)")
    matches_a = embedded_pat.findall(chapter_a)
    # Two embedded image refs (PNG for the resolved relative, JPG/JPEG for abs).
    assert len(matches_a) == 2, f"expected 2 embedded image refs, got: {matches_a}"
    assert "png" in matches_a
    assert any(m in ("jpg", "jpeg") for m in matches_a)
    # Original network URLs must not survive in the chapter HTML.
    assert ABS_IMG not in chapter_a
    assert REL_IMG_RESOLVED not in chapter_a
    # The literal relative form must have been resolved before fetch.
    assert "/img/foo.png" not in chapter_a

    # ---- Article B: RSS-description fallback content present. (Req 3.3) ----
    chapter_b = _read_chapter_html_by_href(book, "chapters/article-002.xhtml")
    assert "RSS desc B" in chapter_b

    # ---- Article C: image-fetch failure produced placeholder. (Req 4.3, 4.4) ----
    chapter_c = _read_chapter_html_by_href(book, "chapters/article-003.xhtml")
    assert 'class="renewsable-missing-image"' in chapter_c
    assert f'data-src="{BROKEN_IMG}"' in chapter_c

    # ---- ZIP namelist: embedded images present, broken image absent. ----
    with zipfile.ZipFile(epub_path, "r") as zf:
        names = zf.namelist()
    image_names = [
        n for n in names
        if re.fullmatch(r"(?:EPUB/)?images/img-[0-9a-f]{12}\.(?:png|jpg|jpeg)", n)
    ]
    assert len(image_names) == 2, f"expected 2 image files, got: {image_names}"
    # No file for the broken image (it was replaced by a placeholder span).
    assert not any("broken" in n for n in names)


# ---------------------------------------------------------------------------
# Test 2: pipeline raises and leaves no partial file when nothing is usable
# ---------------------------------------------------------------------------


def test_pipeline_raises_build_error_when_all_articles_unusable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every article fetch fails AND every RSS fallback is empty -> BuildError.

    Covers requirements 1.4, 3.5: no usable articles surfaces as a
    ``BuildError`` and no partial EPUB is written.
    """
    today = datetime.date(2026, 4, 26)
    config = _make_config(tmp_path)

    _install_fetch_with_retry(
        monkeypatch,
        {
            FEED1_URL: FEED1_XML_EMPTY,
            FEED2_URL: FEED2_XML_EMPTY,
            ARTICLE_A_URL: urllib.error.URLError("A failure"),
            ARTICLE_B_URL: urllib.error.URLError("B failure"),
            ARTICLE_C_URL: urllib.error.URLError("C failure"),
        },
    )
    # Image fetches should never be attempted in this test, but install an
    # empty mock so any accidental call raises a clear error rather than
    # touching the real network.
    _install_image_urlopen(monkeypatch, {})

    with pytest.raises(BuildError):
        builder_mod.Builder(config).build(today=today)

    # No partial EPUB on disk. (Req 1.4, 3.5)
    expected = config.output_dir / "renewsable-2026-04-26.epub"
    assert not expected.exists()
