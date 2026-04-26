"""Uploader: push the built EPUB to the user's reMarkable cloud folder.

Design reference: the "Uploader" component block in
``.kiro/specs/daily-paper/design.md``.

Requirements covered:
- 4.1 ``run`` (build+upload) uploads the resulting EPUB.
- 4.2 Create the destination folder if missing (``rmapi mkdir``); treat
  "already exists" stderr patterns as success.
- 4.3 Re-upload of the same date replaces the prior file — via
  ``rmapi put --force``.
- 4.4 Upload failure → ``UploadError`` with the folder, the local EPUB
  path, and the (redacted) captured stderr. The local EPUB is never
  removed so the user can re-upload later.
- 6.4 Missing / rejected device token → ``UploadError`` whose remediation
  names the ``renewsable pair`` command, and is not retried.
- 8.4 The failure message identifies folder, local EPUB path, and reason.
- 9.3 Bounded retries with exponential backoff between attempts
  (``config.upload_retries`` total, base ``config.upload_backoff_s``).
- 9.5 No hang / partial upload: every subprocess is bounded by the
  module-level ``_RMAPI_TIMEOUT_S`` constant; timeouts raise
  ``UploadError`` without retrying. Token failures raise on first
  attempt. A successful run leaves exactly the current uploaded file
  in the folder (``--force``).

Shape
-----
One class, two subprocess invocations: first ``rmapi mkdir <folder>``,
then ``rmapi put --force <file> <folder>/``. The ``put`` step is wrapped
in a bounded retry loop. Stderr from the last failing attempt is
captured and run through the redaction helpers from
:mod:`renewsable.logging_setup` before being embedded in the raised
``UploadError`` — defence in depth on top of the
:class:`~renewsable.logging_setup.RedactionFilter` that rewrites log
records.

Subprocess / time seams
-----------------------
``subprocess`` and ``_time`` are deliberately bound as module-level
attributes so tests can replace ``subprocess.run`` and ``time.sleep`` in
a single place (``monkeypatch.setattr(uploader_mod.subprocess, "run",
...)``). This mirrors the pattern used in
:mod:`renewsable.builder`, :mod:`renewsable.scheduler`, and
:mod:`renewsable.pairing`. Tests never spawn a real ``rmapi``.

Classification of rmapi stderr
------------------------------
We bucket non-zero rmapi exits into three classes based on stderr
content:

* ``token``: missing / invalid / expired device token. Not retried; the
  user must re-pair via ``renewsable pair``. Matches strings like
  "invalid token", "401", "unauthorized", "authentication", "expired
  token".
* ``network``: transient connectivity issue. Retried with exponential
  backoff. Matches "timeout", "connection", "dns", "network
  unreachable", "no route", "connection refused", "tls", "ssl".
* ``other``: anything else. Also retried — a generic 5xx or rmapi
  transient bug is indistinguishable from a flaky network at this layer
  and the retry cost is small. Req 9.3 asks for a bounded retry, not for
  perfect classification.

The classifier is case-insensitive and pattern-based, not structured —
rmapi does not expose a machine-readable status code beyond its exit
code, so string sniffing is the pragmatic choice. The ``token`` bucket
is checked first because some token errors also match ``network``-ish
substrings (e.g. "401 Unauthorized").
"""

from __future__ import annotations

import logging
import re
import subprocess  # noqa: F401  (module-level alias kept for tests)
import time as _time  # noqa: F401  (module-level alias kept for tests)
from pathlib import Path
from typing import Final

from .config import Config
from .errors import UploadError
from .logging_setup import _JWT_RE, _PAIRING_CODE_RE, _REDACTION


__all__ = ["Uploader"]


logger = logging.getLogger(__name__)


# Bound on a single rmapi subprocess call (mkdir or put). Long enough to
# tolerate slow uploads to the reMarkable cloud, short enough that a hung
# process does not delay a scheduled run indefinitely. Was previously
# config.subprocess_timeout_s; renewsable's only remaining subprocess is
# rmapi, so the timeout lives with the Uploader rather than in Config.
_RMAPI_TIMEOUT_S: int = 180


# ---------------------------------------------------------------------------
# Stderr classification patterns
# ---------------------------------------------------------------------------


# "folder already exists" — rmapi versions vary; match any "exists"
# variant so a tightened wording in a future rmapi release does not cause
# a bogus failure. Case-insensitive.
_MKDIR_EXISTS_RE: Final[re.Pattern[str]] = re.compile(r"exists", re.IGNORECASE)

# Token / auth patterns. Checked BEFORE the network class because some
# token responses contain "401 Unauthorized" which our network regex
# would otherwise not match — but explicit-token wording must still win.
_TOKEN_PATTERNS: Final[tuple[str, ...]] = (
    r"invalid\s+token",
    r"expired\s+token",
    r"\b401\b",
    r"unauthori[sz]ed",
    r"authentication",
    r"re-?authenticate",
)
_TOKEN_RE: Final[re.Pattern[str]] = re.compile(
    "|".join(_TOKEN_PATTERNS), re.IGNORECASE
)

# Transient-network patterns. Matching any of these causes a retry.
_NETWORK_PATTERNS: Final[tuple[str, ...]] = (
    r"timed?\s*out",
    r"timeout",
    r"connection\s+refused",
    r"connection\s+reset",
    r"connection",
    r"\bdns\b",
    r"network\s+unreachable",
    r"no\s+route",
    r"\btls\b",
    r"\bssl\b",
)
_NETWORK_RE: Final[re.Pattern[str]] = re.compile(
    "|".join(_NETWORK_PATTERNS), re.IGNORECASE
)


def _classify_stderr(stderr: str) -> str:
    """Return ``"token"``, ``"network"``, or ``"other"`` for a failing rmapi call.

    ``"token"`` short-circuits the retry loop (the device token is no
    longer valid, so no amount of retrying helps). ``"network"`` and
    ``"other"`` are both retried; we keep them distinct only to produce
    accurate log messages.
    """
    if _TOKEN_RE.search(stderr):
        return "token"
    if _NETWORK_RE.search(stderr):
        return "network"
    return "other"


def _redact(text: str) -> str:
    """Strip rmapi tokens and pairing codes out of free-form text.

    Reuses the two regexes that :mod:`renewsable.logging_setup` installs
    on every log handler. We apply them here so the surfaced
    ``UploadError`` message is safe even if the caller formats it with
    ``print()`` or writes it somewhere that does not pass through our
    logging stack (Req 8.5 applied to the exception payload itself).
    """
    text = _JWT_RE.sub(_REDACTION, text)
    text = _PAIRING_CODE_RE.sub(_REDACTION, text)
    return text


# ---------------------------------------------------------------------------
# Uploader
# ---------------------------------------------------------------------------


class Uploader:
    """Push an EPUB to the user's reMarkable cloud folder via ``rmapi``.

    Parameters
    ----------
    config:
        The loaded :class:`renewsable.config.Config`. The Uploader reads
        only settings; it never mutates the config.
    """

    def __init__(self, config: Config) -> None:
        self._config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def upload(self, pdf: Path, folder: str | None = None) -> None:
        """Ensure ``folder`` exists on the cloud and upload ``pdf`` into it.

        ``folder`` defaults to ``config.remarkable_folder``. The file is
        uploaded with ``--force`` so a re-run for the same date replaces
        the prior file rather than producing a duplicate (Req 4.3).

        The ``pdf`` parameter name is a historical artefact; the Uploader
        is provider-agnostic and now receives an EPUB path. The name is
        kept to avoid changing the public API surface.

        Raises
        ------
        UploadError
            * ``rmapi`` binary not found.
            * ``rmapi mkdir`` failed for any reason other than "folder
              already exists".
            * ``rmapi put`` exhausted all ``config.upload_retries``
              attempts.
            * ``rmapi put`` reported a token-class error on any attempt
              (not retried; remediation points at ``renewsable pair``).
            * A subprocess call exceeded ``_RMAPI_TIMEOUT_S``.

        On any raised ``UploadError`` the local file is left in place
        (Req 4.4); the caller may re-run later.
        """
        cfg = self._config
        dest_folder = folder if folder is not None else cfg.remarkable_folder

        logger.info("uploading %s to %s", pdf, dest_folder)

        # Step 1: ensure the folder exists.
        self._run_mkdir(dest_folder)

        # Step 2: put with bounded retries.
        self._run_put_with_retry(pdf, dest_folder)

        logger.info("upload complete: %s -> %s", pdf.name, dest_folder)

    # ------------------------------------------------------------------
    # Step 1: mkdir
    # ------------------------------------------------------------------

    def _run_mkdir(self, folder: str) -> None:
        """Run ``rmapi mkdir <folder>``, tolerant of "already exists".

        A non-zero exit whose stderr mentions "exists" is treated as a
        no-op success (the folder was already there). Any other failure
        raises :class:`UploadError` naming the folder so the operator
        sees which destination refused the mkdir.
        """
        cfg = self._config
        argv = [cfg.rmapi_bin, "mkdir", folder]
        logger.debug("invoking rmapi: %s", " ".join(argv))

        try:
            result = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=_RMAPI_TIMEOUT_S,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise UploadError(
                f"rmapi mkdir {folder} timed out after {_RMAPI_TIMEOUT_S}s",
                remediation=(
                    "investigate a hung rmapi process or persistent reMarkable "
                    "cloud slowness"
                ),
            ) from exc
        except FileNotFoundError as exc:
            raise UploadError(
                f"rmapi binary not found: {cfg.rmapi_bin}",
                remediation=(
                    "install rmapi (https://github.com/ddvk/rmapi) or set "
                    "'rmapi_bin' in your renewsable config to its absolute path"
                ),
            ) from exc

        if result.returncode == 0:
            return

        # Non-zero: "already exists" is a benign no-op.
        stderr = result.stderr or ""
        if _MKDIR_EXISTS_RE.search(stderr):
            logger.debug("rmapi mkdir: folder %s already exists; continuing", folder)
            return

        # Some other mkdir failure — fatal. Surface folder + redacted stderr.
        redacted = _redact(stderr.strip())
        logger.warning(
            "rmapi mkdir %s failed (exit %d): %s", folder, result.returncode, redacted
        )
        raise UploadError(
            f"rmapi mkdir {folder} failed (exit {result.returncode}): {redacted}",
            remediation=(
                "verify the folder path is valid on your reMarkable cloud and "
                "that your device token is still active (`renewsable pair` if not)"
            ),
        )

    # ------------------------------------------------------------------
    # Step 2: put with retry
    # ------------------------------------------------------------------

    def _run_put_with_retry(self, pdf: Path, folder: str) -> None:
        """Run ``rmapi put --force <file> <folder>/`` with bounded retries.

        Retries only on non-token failure classes. Between attempts, sleep
        ``upload_backoff_s * 2**attempt_idx`` seconds (``attempt_idx``
        starts at 0), so a default base of 2.0s produces waits of 2s,
        4s, 8s — the "small, bounded" schedule Req 9.3 calls for.

        On exhaustion, raises :class:`UploadError` naming the folder, the
        local file path, and the redacted stderr of the final attempt.
        """
        cfg = self._config
        # Trailing slash signals "upload *into* this folder" to rmapi.
        dest = folder if folder.endswith("/") else f"{folder}/"
        argv = [cfg.rmapi_bin, "put", "--force", str(pdf), dest]

        retries = max(1, cfg.upload_retries)
        base_backoff = cfg.upload_backoff_s
        last_stderr = ""
        last_rc: int | None = None

        for attempt in range(retries):
            logger.debug(
                "invoking rmapi (attempt %d/%d): %s",
                attempt + 1,
                retries,
                " ".join(argv),
            )
            try:
                result = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=_RMAPI_TIMEOUT_S,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                # A timeout is a hard failure: we cannot know whether the
                # upload landed, and retrying a partial multi-megabyte
                # upload risks duplicate work on the cloud side. Surface
                # it immediately rather than looping.
                raise UploadError(
                    f"rmapi put {pdf} -> {folder} timed out after "
                    f"{_RMAPI_TIMEOUT_S}s",
                    remediation=(
                        "investigate a hung rmapi process or persistent "
                        "reMarkable cloud slowness"
                    ),
                ) from exc
            except FileNotFoundError as exc:
                raise UploadError(
                    f"rmapi binary not found: {cfg.rmapi_bin}",
                    remediation=(
                        "install rmapi (https://github.com/ddvk/rmapi) or set "
                        "'rmapi_bin' in your renewsable config to its absolute path"
                    ),
                ) from exc

            if result.returncode == 0:
                return  # success

            stderr = result.stderr or ""
            last_stderr = stderr
            last_rc = result.returncode
            cls = _classify_stderr(stderr)
            redacted = _redact(stderr.strip())

            logger.warning(
                "rmapi put attempt %d/%d failed (exit %d, class=%s): %s",
                attempt + 1,
                retries,
                result.returncode,
                cls,
                redacted,
            )

            if cls == "token":
                # Device token is missing / expired / rejected. Retrying
                # will not change that — the user must re-pair.
                raise UploadError(
                    f"reMarkable rejected the upload to {folder} "
                    f"(pdf={pdf}, exit {result.returncode}): {redacted}",
                    remediation=(
                        "run `renewsable pair` and enter a fresh one-time code "
                        "from https://my.remarkable.com/device/desktop/connect"
                    ),
                )

            # Transient failure. Sleep before the next attempt, if any.
            if attempt + 1 < retries:
                delay = base_backoff * (2**attempt)
                _time.sleep(delay)

        # All retries exhausted on non-token failure classes. Leave the
        # local file in place (Req 4.4) and surface a message that names
        # folder, local path, and the (redacted) final stderr (Req 8.4).
        redacted_last = _redact((last_stderr or "").strip())
        raise UploadError(
            (
                f"rmapi put failed to upload {pdf} to {folder} after {retries} "
                f"attempts (last exit {last_rc}): {redacted_last}"
            ),
            remediation=(
                "inspect the log for per-attempt errors; common causes are "
                "network flakiness, reMarkable cloud downtime, or an oversized EPUB"
            ),
        )
