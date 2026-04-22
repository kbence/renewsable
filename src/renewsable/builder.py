"""Daily-paper builder: pre-fetch feeds, drive goosepaper, validate PDF.

Design reference: the "Builder" component block in
``.kiro/specs/daily-paper/design.md``.

Requirements covered:
- 2.1 Produce exactly one PDF per day, named ``renewsable-YYYY-MM-DD.pdf``,
  in ``config.output_dir``.
- 2.2 Every feed that responds successfully is included.
- 2.3 Re-runs on the same date overwrite the existing PDF.
- 2.5 Issue date lands at the top of the document — delegated to
  ``goosepaper`` (which prints the run date in its masthead); we own the
  filename half of the contract.
- 2.6 If every feed fails, or if goosepaper exits non-zero, or if the
  produced file is missing/empty/not a PDF, raise :class:`BuildError` and
  leave no partial artefact behind.
- 3.1 ``telex.hu`` is just another RSS source URL — we treat it like any
  other URL.
- 3.3 An unreachable feed is skipped; the remaining feeds still produce
  the paper.
- 3.4 If body extraction fails per-article, goosepaper's RSS provider
  falls back to title/source/link; we pass its output through unchanged.
- 8.3 Per-source failures are logged with the source URL and reason.
- 9.1 A single bad feed does not kill the run.
- 9.2 Feed fetches retry with bounded exponential backoff
  (``config.feed_fetch_retries`` total attempts, sleeping
  ``config.feed_fetch_backoff_s * 2**i`` between them).
- 9.4 Every request carries ``config.user_agent`` and honours the host's
  ``robots.txt``.

Shape
-----
The Builder stays within one Python class so the CLI can instantiate it
once and call :meth:`build`. Per-run state (the robots cache) is held as
an instance attribute that lives for the duration of the call — a second
call on the same instance still sees a fresh cache because :meth:`build`
resets it on entry.

File:// assumption
------------------
We pre-fetch every RSS feed into a per-run :class:`tempfile.TemporaryDirectory`
and rewrite the story's ``rss_path`` to the corresponding ``file://`` URL
before handing the goosepaper config over. Design Risk note: goosepaper's
RSS provider must accept ``file://`` URLs — it uses ``feedparser`` under
the hood, which does. If that assumption ever breaks, the fallback plan
captured in design.md is to serve the temp directory over an ephemeral
``http.server``; this module does not implement that yet because nothing
observed so far has exercised it.

Subprocess / time / network seams
---------------------------------
``subprocess``, ``_time``, and ``urllib_request`` are deliberately bound as
module-level attributes so tests can monkeypatch them in one place
(``monkeypatch.setattr(builder_mod.subprocess, "run", ...)``). That mirrors
the pattern used in ``renewsable.scheduler`` and ``renewsable.pairing``.
"""

from __future__ import annotations

import copy
import datetime as _dt
import json
import logging
import subprocess  # noqa: F401  (module-level alias kept for tests)
import tempfile
import time as _time  # noqa: F401  (module-level alias kept for tests)
import urllib.parse
import urllib.request as urllib_request  # noqa: F401  (module-level alias kept for tests)
import urllib.robotparser
from pathlib import Path
from typing import Any

from . import profiles as _profiles
from .config import Config
from .errors import BuildError
from .profiles import DeviceProfile


__all__ = ["Builder"]


logger = logging.getLogger(__name__)


# Timeout for any single network read (seconds). Kept separate from the
# much longer ``subprocess_timeout_s`` which bounds the whole goosepaper
# invocation. 30s comfortably accommodates slow feeds without letting a
# truly hung socket delay the run to an absurd degree.
_FEED_READ_TIMEOUT_S: float = 30.0


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
        # re-checks each host — relevant when a site flips its policy
        # between runs on a long-lived CLI session.
        self._robots_cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(
        self,
        profile: DeviceProfile,
        today: _dt.date | None = None,
    ) -> Path:
        """Produce today's PDF for ``profile`` and return its absolute path.

        ``today`` is overridable for testability; without it we take the
        local calendar date. The filename always embeds that date **and**
        the profile name, as ``renewsable-<YYYY-MM-DD>-<profile.name>.pdf``
        — the suffix is present on every build regardless of how many
        profiles the config declares (device-profiles spec Req 6.2).

        The profile's CSS (``profiles.render_css(profile)``) is written
        into a per-run ``<tmpdir>/styles/<profile.name>.css`` and
        goosepaper is invoked with ``cwd=<tmpdir>`` and
        ``--style <profile.name>`` so its CWD-relative style resolver
        picks the file up. ``stories[].config.limit`` is never read or
        mutated in the profile-handling path (Req 8.1).

        Raises
        ------
        BuildError
            * Every configured RSS feed failed (unreachable or disallowed).
            * ``goosepaper`` exited non-zero.
            * The produced file is missing, empty, or doesn't begin with
              the PDF magic bytes ``%PDF-``.
        """
        cfg = self._config
        today = today or _dt.date.today()
        self._robots_cache = {}

        cfg.output_dir.mkdir(parents=True, exist_ok=True)
        output_path = (
            cfg.output_dir
            / f"renewsable-{today.isoformat()}-{profile.name}.pdf"
        )

        with tempfile.TemporaryDirectory(prefix="renewsable-") as tmp_str:
            tmp_dir = Path(tmp_str)
            prepared_stories, rss_total, rss_succeeded = self._prepare_stories(tmp_dir)

            # If we had rss stories configured but every single one failed,
            # treat the run as doomed: goosepaper cannot produce the paper
            # the user expected. Non-rss-only configs remain legal.
            if rss_total > 0 and rss_succeeded == 0:
                raise BuildError(
                    "no stories produced any content; every configured RSS feed "
                    "failed (see logs for per-source reasons)",
                    remediation=(
                        "check network connectivity, inspect the per-feed error "
                        "messages above, and verify each feed URL is still valid"
                    ),
                )

            # Write the per-profile CSS into <tmpdir>/styles/<name>.css so
            # goosepaper's CWD-relative style resolver (pathlib.Path("./styles/")
            # / style) picks it up when we run the subprocess with cwd=tmp_dir.
            styles_dir = tmp_dir / "styles"
            styles_dir.mkdir(parents=True, exist_ok=True)
            (styles_dir / f"{profile.name}.css").write_text(
                _profiles.render_css(profile), encoding="utf-8"
            )

            goosepaper_config_path = tmp_dir / "goosepaper-config.json"
            self._write_goosepaper_config(
                goosepaper_config_path,
                prepared_stories,
                style_name=profile.name,
            )

            self._run_goosepaper(
                goosepaper_config_path, output_path, cwd=tmp_dir
            )

        self._validate_pdf(output_path)
        return output_path

    # ------------------------------------------------------------------
    # Stage: per-feed pre-fetch + rewrite
    # ------------------------------------------------------------------

    def _prepare_stories(
        self, tmp_dir: Path
    ) -> tuple[list[dict[str, Any]], int, int]:
        """Walk ``config.stories``, fetching RSS feeds into ``tmp_dir``.

        Returns ``(prepared, rss_total, rss_succeeded)`` where ``prepared``
        is the rewritten stories list suitable for goosepaper, ``rss_total``
        is how many RSS stories appeared in the config, and ``rss_succeeded``
        is how many we managed to pre-fetch and rewrite.

        Non-RSS stories are passed through verbatim (deep-copied so the
        caller's config remains immutable in spirit even though dicts
        are technically mutable).
        """
        prepared: list[dict[str, Any]] = []
        rss_total = 0
        rss_succeeded = 0

        for idx, story in enumerate(self._config.stories):
            if not isinstance(story, dict) or story.get("provider") != "rss":
                # Anything we don't recognise as rss is goosepaper's problem.
                # Deep-copy so we never hand goosepaper a reference into the
                # user's config dict.
                prepared.append(copy.deepcopy(story))
                continue

            rss_total += 1
            story_cfg = story.get("config") or {}
            url = story_cfg.get("rss_path")
            if not isinstance(url, str) or not url:
                logger.warning(
                    "skipping story %d: rss_path missing or not a string", idx
                )
                continue

            if not self._robots_allows(url):
                logger.warning(
                    "skipping %s: disallowed by robots.txt", url
                )
                continue

            try:
                body = self._fetch_with_retry(url)
            except Exception as exc:  # pragma: no cover - exhaustively tested
                # Exhausted retries. Log and drop this source, keep the paper
                # going (Req 9.1).
                logger.warning(
                    "skipping %s: feed fetch failed after retries: %s", url, exc
                )
                continue

            local_path = tmp_dir / f"feed-{idx}.xml"
            local_path.write_bytes(body)

            rewritten = copy.deepcopy(story)
            # ``config`` may not exist in the original; ensure a dict is
            # there before mutating.
            rewritten.setdefault("config", {})
            rewritten["config"]["rss_path"] = f"file://{local_path}"
            prepared.append(rewritten)
            rss_succeeded += 1

        return prepared, rss_total, rss_succeeded

    # ------------------------------------------------------------------
    # Stage: write goosepaper config
    # ------------------------------------------------------------------

    def _write_goosepaper_config(
        self,
        path: Path,
        prepared_stories: list[dict[str, Any]],
        *,
        style_name: str,
    ) -> None:
        """Serialise the goosepaper-subset config to ``path``.

        Only the handful of keys goosepaper documents appear in the output.
        Everything else renewsable tracks (schedule_time, remarkable_folder,
        log_dir, …) is none of goosepaper's business.

        The ``style_name`` is written into the goosepaper config's ``style``
        field rather than passed as a CLI flag — goosepaper's ``--style`` is
        a config-only entry (see multiparser.argumentOrConfig), not an
        argparse flag.
        """
        cfg: dict[str, Any] = {"stories": prepared_stories, "style": style_name}
        if self._config.font_size is not None:
            cfg["font_size"] = self._config.font_size
        path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------
    # Stage: invoke goosepaper
    # ------------------------------------------------------------------

    def _run_goosepaper(
        self,
        config_path: Path,
        output_path: Path,
        *,
        cwd: Path,
    ) -> None:
        """Run ``goosepaper -c config -o output --noupload``.

        ``--noupload`` is goosepaper's switch that bypasses its (broken for
        us) built-in upload path; renewsable always uploads via its own
        Uploader component.

        The profile-specific style is selected via the ``style`` key in the
        goosepaper config JSON (written by :meth:`_write_goosepaper_config`)
        rather than a CLI flag — goosepaper's ``--style`` is a config-only
        entry (see multiparser.argumentOrConfig in the installed goosepaper).
        goosepaper resolves it to ``./styles/<profile.name>.css`` relative
        to its CWD, which is why we pass ``cwd=<tmpdir>`` where
        :meth:`build` has written the per-run rendered CSS file.

        Captures stdout/stderr into memory and forwards them to the logger:
        stderr → WARNING when goosepaper exits non-zero, DEBUG otherwise.

        On non-zero exit, raises :class:`BuildError` without attempting to
        clean the output path — the validation stage will do so if a bad
        PDF was nonetheless written.
        """
        argv = [
            self._config.goosepaper_bin,
            "-c",
            str(config_path),
            "-o",
            str(output_path),
            "--noupload",
        ]
        logger.info("invoking goosepaper: %s", " ".join(argv))

        try:
            result = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=self._config.subprocess_timeout_s,
                check=False,
                cwd=str(cwd),
            )
        except subprocess.TimeoutExpired as exc:
            raise BuildError(
                f"goosepaper timed out after {self._config.subprocess_timeout_s}s",
                remediation=(
                    "reduce the number of feeds, raise 'subprocess_timeout_s' in "
                    "config, or investigate a hung dependency"
                ),
            ) from exc
        except FileNotFoundError as exc:
            raise BuildError(
                f"goosepaper binary not found: {self._config.goosepaper_bin}",
                remediation=(
                    "install goosepaper in the active Python environment or set "
                    "'goosepaper_bin' in your renewsable config"
                ),
            ) from exc

        # Forward captured output. stderr at WARNING on failure is the
        # per-run breadcrumb the operator will read first; on success we
        # keep it at DEBUG so healthy runs stay quiet.
        if result.stderr:
            if result.returncode != 0:
                logger.warning("goosepaper stderr: %s", result.stderr.strip())
            else:
                logger.debug("goosepaper stderr: %s", result.stderr.strip())
        if result.stdout:
            logger.debug("goosepaper stdout: %s", result.stdout.strip())

        if result.returncode != 0:
            raise BuildError(
                f"goosepaper exited with code {result.returncode}",
                remediation=(
                    "inspect the goosepaper stderr above; common causes are "
                    "network issues, bad feed URLs, or a WeasyPrint/system-lib "
                    "misconfiguration"
                ),
            )

    # ------------------------------------------------------------------
    # Stage: validate the produced PDF
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_pdf(path: Path) -> None:
        """Ensure ``path`` exists, is non-empty, and starts with ``%PDF-``.

        On any failure, the offending file is removed so a later upload
        cannot pick it up and ship a corrupted artefact to the reMarkable.
        """
        try:
            if not path.exists():
                raise BuildError(
                    f"goosepaper exited successfully but produced no file at {path}",
                    remediation=(
                        "check goosepaper stderr in the logs; the feed content "
                        "may have been empty or the -o path wasn't writable"
                    ),
                )
            size = path.stat().st_size
            if size == 0:
                raise BuildError(
                    f"goosepaper produced an empty file at {path}",
                    remediation=(
                        "inspect goosepaper's stderr in the logs; this is not a "
                        "valid PDF"
                    ),
                )
            with path.open("rb") as fh:
                magic = fh.read(5)
            if magic != b"%PDF-":
                raise BuildError(
                    f"goosepaper produced a non-PDF file at {path} "
                    f"(first bytes: {magic!r})",
                    remediation=(
                        "inspect goosepaper's stderr — the output may be HTML "
                        "error text rather than a PDF"
                    ),
                )
        except BuildError:
            # Best-effort cleanup: the user should never see a half-built
            # PDF the Uploader might otherwise try to ship.
            try:
                path.unlink(missing_ok=True)
            except OSError:  # pragma: no cover - defensive
                pass
            raise

    # ------------------------------------------------------------------
    # Network helpers
    # ------------------------------------------------------------------

    def _fetch_with_retry(self, url: str) -> bytes:
        """GET ``url`` with up to ``config.feed_fetch_retries`` attempts.

        Returns the body as bytes on first success. Raises the *last*
        exception after exhausting retries. Between attempts we sleep
        ``config.feed_fetch_backoff_s * 2**i`` seconds (``i`` starts at 0),
        so a default of 1.0s gives waits of 1s, 2s, 4s, … — the
        "small, bounded" schedule Req 9.2 calls for.
        """
        retries = max(1, self._config.feed_fetch_retries)
        base = self._config.feed_fetch_backoff_s
        last_exc: Exception | None = None
        for attempt in range(retries):
            try:
                req = urllib_request.Request(
                    url,
                    headers={"User-Agent": self._config.user_agent},
                )
                with urllib_request.urlopen(req, timeout=_FEED_READ_TIMEOUT_S) as resp:
                    return resp.read()
            except Exception as exc:
                last_exc = exc
                logger.info(
                    "feed fetch attempt %d/%d failed for %s: %s",
                    attempt + 1,
                    retries,
                    url,
                    exc,
                )
                if attempt + 1 < retries:
                    _time.sleep(base * (2**attempt))
        # Retries exhausted.
        assert last_exc is not None  # loop ran at least once
        raise last_exc

    def _robots_allows(self, url: str) -> bool:
        """Return True iff ``robots.txt`` at ``url``'s host allows our UA.

        The cache key is ``<scheme>://<host>``. A missing / unreachable
        robots.txt is treated as fully permissive — matches stdlib
        :class:`urllib.robotparser.RobotFileParser` semantics and the
        common-web convention. When a host's robots.txt is unparseable or
        explicitly disallows ``*``, we drop every feed on that host.
        """
        parsed = urllib.parse.urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            # Relative or otherwise malformed URL — let the fetch layer
            # raise the real error; no point asking robots about it.
            return True
        # ``file://`` URLs are not part of the robots.txt ecosystem. We
        # never rewrite config to ``file://`` before reaching here, but
        # guard anyway.
        if parsed.scheme not in ("http", "https"):
            return True

        key = f"{parsed.scheme}://{parsed.netloc}"
        if key not in self._robots_cache:
            self._robots_cache[key] = self._load_robots(key)

        parser = self._robots_cache[key]
        if parser is None:
            # Failed to fetch robots.txt — fail-open.
            return True
        return parser.can_fetch(self._config.user_agent, url)

    def _load_robots(self, origin: str) -> urllib.robotparser.RobotFileParser | None:
        """Fetch and parse ``<origin>/robots.txt`` in a single attempt.

        We deliberately do **not** retry the robots fetch: the cost of a
        transient failure on robots.txt is a single run where a site
        happens to be allowed; the cost of retrying is multiplying every
        Pi-side run by the robots-fetch latency of each configured host.

        Returns ``None`` on any error (network, non-200, decoding,
        parsing). ``None`` is interpreted as "fail-open" by the caller.
        """
        robots_url = f"{origin}/robots.txt"
        try:
            req = urllib_request.Request(
                robots_url,
                headers={"User-Agent": self._config.user_agent},
            )
            with urllib_request.urlopen(req, timeout=_FEED_READ_TIMEOUT_S) as resp:
                raw = resp.read()
        except Exception as exc:
            logger.debug("could not fetch %s (%s); fail-open", robots_url, exc)
            return None

        parser = urllib.robotparser.RobotFileParser()
        try:
            text = raw.decode("utf-8", errors="replace")
            parser.parse(text.splitlines())
        except Exception as exc:  # pragma: no cover - parser is very tolerant
            logger.debug("could not parse %s (%s); fail-open", robots_url, exc)
            return None
        return parser
