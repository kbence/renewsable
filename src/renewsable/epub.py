"""EPUB 3 assembly: turn a list of ``Article`` records into a valid EPUB file.

Design reference: ``.kiro/specs/epub-output/design.md`` →
"Components and Interfaces" → "Output" → ``epub`` module, plus
"Logical Data Model" and "Data Contracts & Integration".

Requirements covered:
- 1.1 — produce a single EPUB 3 file at the daily output path.
- 1.2 — internal layout: mimetype-first, container.xml, content.opf, nav.
- 4.1 / 4.2 / 4.3 / 4.4 — image handling: fetch, embed, dedup, placeholder
  on failure, oversize treated as failure, build never raises on image failure.
- 5.1 / 5.2 / 5.3 — nav contains one entry per article in input order with
  the article title; NCX and EpubNav are present.
- 6.1 / 6.2 / 6.3 / 6.4 — metadata: dc:title, dc:creator, dc:language,
  dc:date, deterministic dc:identifier (UUIDv5 from the issue date).

Test seams
----------
``urllib_request``, ``_time``, and ``ebooklib_epub`` are bound as module-level
attributes so tests can monkeypatch them in one place — same convention as
``renewsable.builder``, ``renewsable.http``, and ``renewsable.scheduler``.
"""

from __future__ import annotations

import hashlib
import html as _html
import logging
import mimetypes
import time as _time  # noqa: F401  (module-level alias kept for tests)
import urllib.parse
import urllib.request as urllib_request  # noqa: F401  (module-level alias kept for tests)
import uuid
from datetime import date as _date_t
from pathlib import Path

import lxml.etree
import lxml.html
from ebooklib import epub as ebooklib_epub  # noqa: F401  (alias kept for tests)

from .articles import Article
from .errors import BuildError


__all__ = ["assemble"]


logger = logging.getLogger(__name__)


# Per design: dc:title prefix uses an em dash, language is fixed "en",
# creator is the project name.
_TITLE_PREFIX = "Renewsable Daily — "
_CREATOR = "Renewsable"
_LANGUAGE = "en"

# UUID namespace seed for deterministic dc:identifier.
_UUID_SEED_PREFIX = "renewsable-daily-"

_XHTML_WRAPPER = (
    '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en">\n'
    "<head><title>{title}</title></head>\n"
    "<body><h1>{title}</h1>\n{body}\n</body>\n"
    "</html>\n"
)

# Image-fetch policy: a successful fetch must yield bytes whose effective
# MIME type is image/*. Anything else is treated as a fetch failure.
_IMAGE_MIME_PREFIX = "image/"


def assemble(
    articles: list[Article],
    *,
    today: _date_t,
    output_path: Path,
    ua: str,
    retries: int,
    backoff_s: float,
    image_timeout_s: float = 15.0,
    image_max_bytes: int = 10 * 1024 * 1024,
) -> None:
    """Build the day's EPUB file at ``output_path`` from ``articles``.

    Image-fetch failures never raise — the offending ``<img>`` is replaced
    with a ``<span class="renewsable-missing-image">`` placeholder. Any
    other failure (e.g., ebooklib write error) raises :class:`BuildError`
    after unlinking any partial file at ``output_path``.
    """
    try:
        book = ebooklib_epub.EpubBook()

        # Metadata (Req 6.1–6.4).
        book.set_title(f"{_TITLE_PREFIX}{today.isoformat()}")
        book.set_language(_LANGUAGE)
        book.add_author(_CREATOR)
        book.add_metadata("DC", "date", today.isoformat())
        deterministic_uuid = uuid.uuid5(
            uuid.NAMESPACE_URL, f"{_UUID_SEED_PREFIX}{today.isoformat()}"
        )
        book.set_identifier(f"urn:uuid:{deterministic_uuid}")

        # Per-URL dedup cache: url -> (file_name, EpubItem).
        image_cache: dict[str, tuple[str, ebooklib_epub.EpubItem]] = {}

        chapters: list[ebooklib_epub.EpubHtml] = []
        for idx, article in enumerate(articles, start=1):
            chapter_uid = f"article-{idx:03d}"
            chapter_filename = f"chapters/article-{idx:03d}.xhtml"
            rewritten_body = _rewrite_images(
                article.html,
                book=book,
                image_cache=image_cache,
                ua=ua,
                retries=retries,
                backoff_s=backoff_s,
                timeout_s=image_timeout_s,
                max_bytes=image_max_bytes,
            )
            chapter_html = _XHTML_WRAPPER.format(
                title=_html.escape(article.title, quote=True),
                body=rewritten_body,
            )
            chapter = ebooklib_epub.EpubHtml(
                title=article.title,
                file_name=chapter_filename,
                lang=_LANGUAGE,
                uid=chapter_uid,
            )
            chapter.content = chapter_html
            book.add_item(chapter)
            chapters.append(chapter)

        # Spine, TOC, nav (Req 5.1–5.3).
        book.toc = [
            ebooklib_epub.Link(chapter.file_name, article.title, chapter.id)
            for chapter, article in zip(chapters, articles)
        ]
        book.add_item(ebooklib_epub.EpubNcx())
        book.add_item(ebooklib_epub.EpubNav())
        book.spine = ["nav", *chapters]

        ebooklib_epub.write_epub(str(output_path), book)
    except BuildError:
        _safe_unlink(output_path)
        raise
    except Exception as exc:
        logger.error("EPUB assembly failed: %s", exc)
        _safe_unlink(output_path)
        raise BuildError(
            f"failed to assemble EPUB at {output_path}: {exc}",
            remediation="check the per-source logs above for the underlying cause",
        ) from exc


# ---------------------------------------------------------------------------
# Image rewriting
# ---------------------------------------------------------------------------


def _rewrite_images(
    body_html: str,
    *,
    book: "ebooklib_epub.EpubBook",
    image_cache: dict[str, tuple[str, "ebooklib_epub.EpubItem"]],
    ua: str,
    retries: int,
    backoff_s: float,
    timeout_s: float,
    max_bytes: int,
) -> str:
    """Walk ``<img>`` elements; fetch + embed on success, placeholder on failure.

    Returns the rewritten serialized HTML fragment.
    """
    try:
        tree = lxml.html.fragment_fromstring(body_html, create_parent="div")
    except Exception as exc:
        logger.info("could not parse article body for image rewrite: %s", exc)
        # Fall back to the raw HTML; downstream EPUB writer can still wrap it.
        return body_html

    for img in list(tree.iter("img")):
        src = img.get("src")
        alt = img.get("alt") or ""
        if not isinstance(src, str) or not src:
            _replace_with_placeholder(img, src or "", alt)
            continue
        # Defense in depth: articles.collect resolves to absolute http(s),
        # but if any non-http(s) sneaks in, treat as failure.
        parsed = urllib.parse.urlparse(src)
        if parsed.scheme not in ("http", "https"):
            _replace_with_placeholder(img, src, alt)
            continue

        cached = image_cache.get(src)
        if cached is not None:
            file_name, _item = cached
            img.set("src", f"../{file_name}")
            continue

        result = _fetch_image_with_mime(
            src,
            ua=ua,
            retries=retries,
            backoff_s=backoff_s,
            timeout_s=timeout_s,
            max_bytes=max_bytes,
        )
        if result is None:
            _replace_with_placeholder(img, src, alt)
            continue

        body, mime = result
        ext = _ext_for(mime, src)
        digest = hashlib.sha256(src.encode("utf-8")).hexdigest()[:12]
        file_name = f"images/img-{digest}.{ext}"
        item = ebooklib_epub.EpubItem(
            uid=f"img-{digest}",
            file_name=file_name,
            media_type=mime,
            content=body,
        )
        book.add_item(item)
        image_cache[src] = (file_name, item)
        # Chapter lives at ``chapters/article-NNN.xhtml``; the relative path
        # from there to ``images/...`` is ``../images/...``.
        img.set("src", f"../{file_name}")

    return lxml.html.tostring(tree, encoding="unicode")


def _replace_with_placeholder(img_el, original_src: str, alt: str) -> None:
    """Replace ``img_el`` in-place with a ``<span class="renewsable-missing-image">``."""
    label = alt.strip() if alt and alt.strip() else original_src
    placeholder_xml = (
        '<span class="renewsable-missing-image" data-src="{src}">'
        "[image unavailable: {label}]"
        "</span>"
    ).format(
        src=_html.escape(original_src, quote=True),
        label=_html.escape(label, quote=False),
    )
    new_el = lxml.html.fragment_fromstring(placeholder_xml)
    parent = img_el.getparent()
    if parent is None:
        return
    # Preserve the original tail text (whitespace between siblings).
    new_el.tail = img_el.tail
    parent.replace(img_el, new_el)


def _ext_for(mime: str, url: str) -> str:
    """Pick a file extension from the MIME type, falling back to the URL."""
    guessed = mimetypes.guess_extension(mime.split(";")[0].strip())
    if guessed:
        return guessed.lstrip(".")
    # URL-extension fallback.
    path = urllib.parse.urlparse(url).path
    if "." in path:
        candidate = path.rsplit(".", 1)[-1].lower()
        if candidate.isalnum() and 0 < len(candidate) <= 5:
            return candidate
    return "bin"


# ---------------------------------------------------------------------------
# Image fetch with header inspection
# ---------------------------------------------------------------------------


def _fetch_image_with_mime(
    url: str,
    *,
    ua: str,
    retries: int,
    backoff_s: float,
    timeout_s: float,
    max_bytes: int,
) -> tuple[bytes, str] | None:
    """Fetch ``url`` returning ``(body, mime)`` on success or ``None`` on failure.

    "Failure" covers: any exception from urlopen across all retries, body
    that exceeds ``max_bytes``, a non-image effective MIME type. Retries
    follow the same exponential backoff schedule as ``http.fetch_with_retry``
    so that operators see a single, predictable network policy.
    """
    retries = max(1, retries)
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib_request.Request(url, headers={"User-Agent": ua})
            with urllib_request.urlopen(req, timeout=timeout_s) as resp:
                body = resp.read()
                if len(body) > max_bytes:
                    logger.info(
                        "image %s exceeds max bytes (%d > %d); treating as failure",
                        url,
                        len(body),
                        max_bytes,
                    )
                    return None
                content_type = ""
                headers = getattr(resp, "headers", None)
                if headers is not None:
                    raw = headers.get("Content-Type") if hasattr(headers, "get") else None
                    if isinstance(raw, str):
                        content_type = raw.split(";")[0].strip().lower()
                mime = content_type or _mime_from_url(url)
                if not mime.startswith(_IMAGE_MIME_PREFIX):
                    logger.info(
                        "image %s has non-image content-type %r; treating as failure",
                        url,
                        mime,
                    )
                    return None
                return body, mime
        except Exception as exc:
            last_exc = exc
            logger.info(
                "image fetch attempt %d/%d failed for %s: %s",
                attempt + 1,
                retries,
                url,
                exc,
            )
            if attempt + 1 < retries:
                _time.sleep(backoff_s * (2**attempt))
    logger.info("image fetch exhausted retries for %s: %s", url, last_exc)
    return None


def _mime_from_url(url: str) -> str:
    """Fallback MIME guess from URL path extension."""
    guessed, _ = mimetypes.guess_type(url)
    return (guessed or "").lower()


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("could not unlink partial file %s: %s", path, exc)
