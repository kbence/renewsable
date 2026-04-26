"""Daily-paper builder: collect articles and assemble a daily EPUB.

Design reference: ``.kiro/specs/epub-output/design.md`` →
"Components and Interfaces" → "Orchestration" → "Builder (modified)" and
the "System Flows" sequence diagram for the new pipeline.

The Builder owns three orchestration responsibilities:

1. Drive :func:`renewsable.articles.collect` to turn the validated
   ``stories`` list into a list of :class:`renewsable.articles.Article`.
2. Drive :func:`renewsable.epub.assemble` to write today's EPUB to
   ``<output_dir>/renewsable-<YYYY-MM-DD>.epub``.
3. Validate that the produced file is a real EPUB before returning the
   path; on any validation failure, unlink the bad file so a later
   Uploader cannot ship a corrupted artefact.

Per design, the Builder no longer fetches anything itself — feeds, articles,
and images all flow through :mod:`renewsable.http` (called by ``articles``
and ``epub``). The Builder still keeps a per-run robots cache to ensure a
single host's robots.txt is fetched once across both modules.

Test seams
----------
``articles`` and ``epub`` are imported as module-level attributes so tests
can ``monkeypatch.setattr(builder_mod.articles_mod, "collect", ...)`` and
``monkeypatch.setattr(builder_mod.epub_mod, "assemble", ...)``.
"""

from __future__ import annotations

import datetime as _dt
import logging
import zipfile
from pathlib import Path

from . import articles as articles_mod
from . import epub as epub_mod
from . import http as http_mod  # noqa: F401  (alias kept for tests)
from .config import Config
from .errors import BuildError


__all__ = ["Builder"]


logger = logging.getLogger(__name__)


class Builder:
    """Orchestrate a daily-paper build.

    Parameters
    ----------
    config:
        The loaded :class:`renewsable.config.Config`. The Builder reads
        only settings; it never mutates the config.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        # Per-run robots.txt cache. Re-initialised at the start of each
        # :meth:`build` call so a second build within the same process
        # re-checks each host. The cache is shared between the article-feed
        # fetches and the article-page fetches done inside ``articles``.
        self._robots_cache: dict = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(self, today: _dt.date | None = None) -> Path:
        """Produce today's EPUB and return its absolute path.

        ``today`` is overridable for testability; without it we take the
        local calendar date. The filename is always
        ``renewsable-<YYYY-MM-DD>.epub`` in ``config.output_dir``.

        Raises
        ------
        BuildError
            * ``articles.collect`` produced zero usable articles (every
              configured source either failed or yielded nothing usable).
            * ``epub.assemble`` raised internally (rewrapped by the epub
              module as ``BuildError`` already).
            * The produced file fails ``_validate_epub`` (missing, empty,
              not a valid EPUB ZIP, wrong mimetype, or missing
              ``META-INF/container.xml``).
        """
        cfg = self._config
        today = today or _dt.date.today()
        self._robots_cache = {}

        cfg.output_dir.mkdir(parents=True, exist_ok=True)
        output_path = cfg.output_dir / f"renewsable-{today.isoformat()}.epub"

        articles = articles_mod.collect(
            cfg.stories,
            ua=cfg.user_agent,
            retries=cfg.feed_fetch_retries,
            backoff_s=cfg.feed_fetch_backoff_s,
            robots_cache=self._robots_cache,
        )

        if not articles:
            # Req 3.5: every source either failed to fetch or contained no
            # usable entries. Per-source warnings have already been logged
            # by ``articles.collect``.
            raise BuildError(
                "no usable articles produced from any source",
                remediation=(
                    "check the per-source warnings above; every feed either "
                    "failed to fetch or contained no usable entries"
                ),
            )

        epub_mod.assemble(
            articles,
            today=today,
            output_path=output_path,
            ua=cfg.user_agent,
            retries=cfg.feed_fetch_retries,
            backoff_s=cfg.feed_fetch_backoff_s,
        )

        self._validate_epub(output_path)
        return output_path

    # ------------------------------------------------------------------
    # Stage: validate the produced EPUB
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_epub(path: Path) -> None:
        """Ensure ``path`` is a real EPUB.

        Per design's "Error Handling" / Error Strategy table for "EPUB
        validation failure": the artefact must be a ZIP whose first member
        is the literal uncompressed bytes ``application/epub+zip`` under
        the name ``mimetype`` and which contains ``META-INF/container.xml``.
        On any failure, the file is unlinked (best-effort) before raising
        :class:`BuildError` so a later Uploader cannot pick it up.
        """
        try:
            if not path.exists():
                raise BuildError(
                    f"EPUB assembly produced no file at {path}",
                    remediation="check the per-source logs above for the underlying cause",
                )
            if path.stat().st_size == 0:
                raise BuildError(
                    f"EPUB assembly produced an empty file at {path}",
                    remediation="check the per-source logs above for the underlying cause",
                )

            try:
                zf = zipfile.ZipFile(path, "r")
            except zipfile.BadZipFile as exc:
                raise BuildError(
                    f"produced file at {path} is not a valid ZIP archive: {exc}",
                    remediation="check the per-source logs above for the underlying cause",
                ) from exc

            with zf:
                names = zf.namelist()
                if not names or names[0] != "mimetype":
                    raise BuildError(
                        f"produced EPUB at {path} does not have 'mimetype' as its first entry",
                        remediation="check the per-source logs above for the underlying cause",
                    )
                info = zf.getinfo("mimetype")
                if info.compress_type != zipfile.ZIP_STORED:
                    raise BuildError(
                        f"produced EPUB at {path} stores 'mimetype' compressed; spec requires uncompressed",
                        remediation="check the per-source logs above for the underlying cause",
                    )
                if zf.read("mimetype") != b"application/epub+zip":
                    raise BuildError(
                        f"produced EPUB at {path} has wrong mimetype contents",
                        remediation="check the per-source logs above for the underlying cause",
                    )
                if "META-INF/container.xml" not in names:
                    raise BuildError(
                        f"produced EPUB at {path} is missing META-INF/container.xml",
                        remediation="check the per-source logs above for the underlying cause",
                    )
        except BuildError:
            # Best-effort cleanup so a later upload cannot ship a corrupted
            # artefact.
            try:
                path.unlink(missing_ok=True)
            except OSError:  # pragma: no cover - defensive
                pass
            raise
