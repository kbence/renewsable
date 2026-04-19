"""Logging configuration for renewsable.

Design reference:
- ``.kiro/specs/daily-paper/design.md`` — section "logging_setup
  (summary-only)" and "Error Handling → Monitoring".

Requirements covered:
- 8.1 (each run produces a log entry the operator can read).
- 8.2 (logs visible via the system log facility AND as a plain-text file
  under a documented path, retaining at least the last 14 days of runs).
- 8.5 (never log the device token, one-time pairing code, or any other
  credential in plain text).

Public surface
--------------
* :func:`configure_logging` — install file + stderr handlers on the root
  logger. Idempotent. Called once from the CLI before any component runs.
* :class:`RedactionFilter` — :class:`logging.Filter` subclass that masks
  reMarkable pairing codes and rmapi-style JWT tokens.

Why ``TimedRotatingFileHandler`` rather than ``RotatingFileHandler``?
---------------------------------------------------------------------
The tasks.md description and the design table both say "14 backups, daily
rotation". ``RotatingFileHandler`` rotates by file size, not by time;
expressing "14 days" through it requires a size estimate that gets stale.
``TimedRotatingFileHandler(when='midnight', interval=1, backupCount=14)``
expresses the requirement directly: rotate at 00:00 local time and retain
the last 14 dated backups (Req. 8.2 — "at least the last 14 days of runs").
This deviation from the literal class name in tasks.md is documented in
the implementer's status report (CONCERNS).

Why a primitive ``log_dir`` argument rather than a ``Config`` object?
---------------------------------------------------------------------
Task 1.4 sits below task 2.1 in the dependency graph; the ``Config``
dataclass does not exist yet. Designing ``configure_logging`` as
``configure_logging(log_dir, level)`` lets task 2.1 wrap it without
forcing 1.4 to invent a config schema. The CLI (task 3.1) will end up
calling ``configure_logging(config.log_dir, config.log_level)``.

Idempotency
-----------
``configure_logging`` clears any handlers it previously installed before
attaching new ones, so repeated calls do not produce duplicate log lines.
This matters because tests, the CLI, and any future REPL bootstrap may
all reach for ``configure_logging`` independently.

Filter placement
----------------
The redaction filter is attached to **each handler**, not to the root
logger. Filters attached to a logger only run when the logger itself is
the source of the record (see CPython's ``Logger.callHandlers`` /
``Logger.filter`` interaction); a record propagated up from a child
logger bypasses parent-logger filters but still hits the parent's
handlers. Attaching to handlers is the only placement that guarantees
every emitted record passes through the redactor.
"""

from __future__ import annotations

import logging
import logging.handlers
import re
import sys
from pathlib import Path
from typing import Final

__all__ = ["RedactionFilter", "configure_logging"]


# ---------------------------------------------------------------------------
# Redaction filter
# ---------------------------------------------------------------------------


# 8-character uppercase alphanumeric sequence at a word boundary. This is
# the format the reMarkable cloud issues as the one-time pairing code (e.g.
# ``ABCD1234``). The boundary anchors prevent matching inside longer
# all-caps tokens such as ``DEADBEEF12345`` (which is not a pairing code).
_PAIRING_CODE_RE: Final[re.Pattern[str]] = re.compile(r"\b[A-Z0-9]{8}\b")

# rmapi persists the device token as a JWT (header.payload.signature, each
# segment base64url). Standard JWTs always begin with ``eyJ`` because that
# is the base64 encoding of ``{"`` — every JWT header is a JSON object. The
# ``eyJ`` anchor is deliberate: it eliminates false positives against
# arbitrary three-segment dotted strings (file paths, version numbers, etc.).
_JWT_RE: Final[re.Pattern[str]] = re.compile(
    r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"
)

_REDACTION: Final[str] = "***"


def _redact(text: str) -> str:
    """Apply both redaction patterns to ``text`` and return the result."""
    text = _JWT_RE.sub(_REDACTION, text)
    text = _PAIRING_CODE_RE.sub(_REDACTION, text)
    return text


class RedactionFilter(logging.Filter):
    """Mask pairing codes and rmapi tokens in log records (Req. 8.5).

    The filter mutates ``record.msg`` and each string element of
    ``record.args`` in place, then returns ``True`` so the record is
    still emitted. Non-string ``msg`` values (typically an exception or
    arbitrary object passed to ``logger.exception``) are coerced via
    ``str()`` before substitution; if the coerced form is identical to
    the original the record is left untouched, otherwise the redacted
    string replaces ``msg`` and ``args`` is cleared (since the original
    format string no longer holds).
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        # ---- record.msg -------------------------------------------------
        msg = record.msg
        if isinstance(msg, str):
            redacted = _redact(msg)
            if redacted is not msg:
                record.msg = redacted
        else:
            # Coerce non-string msg defensively (e.g. an Exception instance).
            coerced = str(msg)
            redacted = _redact(coerced)
            if redacted != coerced:
                record.msg = redacted
                # The original args were intended for the original
                # (non-string) msg's __str__; once we replace msg with a
                # plain string they no longer apply.
                record.args = None

        # ---- record.args -----------------------------------------------
        args = record.args
        if isinstance(args, tuple):
            new_args = tuple(_redact(a) if isinstance(a, str) else a for a in args)
            if new_args != args:
                record.args = new_args
        elif isinstance(args, dict):
            new_dict = {
                k: (_redact(v) if isinstance(v, str) else v) for k, v in args.items()
            }
            if new_dict != args:
                record.args = new_dict
        elif isinstance(args, str):
            # ``logger.info("msg", "single_arg")`` lands here in some paths.
            redacted_arg = _redact(args)
            if redacted_arg != args:
                record.args = redacted_arg

        return True


# ---------------------------------------------------------------------------
# configure_logging
# ---------------------------------------------------------------------------


_FORMAT: Final[str] = "%(asctime)s %(levelname)s %(name)s: %(message)s"

# Sentinel attribute we set on each handler we install so a second call can
# identify and remove only its own handlers (rather than nuking handlers
# pytest's caplog has wired in).
_OWN_MARKER: Final[str] = "_renewsable_managed"


def configure_logging(log_dir: Path, level: str = "INFO") -> None:
    """Install renewsable's file + stderr log sinks on the root logger.

    Parameters
    ----------
    log_dir:
        Directory that will contain ``renewsable.log`` and its rotated
        backups. Created (with parents) if it does not exist.
    level:
        Standard Python logging level name (``"DEBUG"``, ``"INFO"``,
        ``"WARNING"``, ...). Applied to the root logger. Both handlers
        therefore receive every record at or above this level.

    Behaviour
    ---------
    * Creates ``log_dir`` (``mkdir(parents=True, exist_ok=True)``).
    * Installs a :class:`logging.handlers.TimedRotatingFileHandler` writing
      to ``<log_dir>/renewsable.log``, rotating at midnight and retaining
      14 backups (Req. 8.2).
    * Installs a :class:`logging.StreamHandler` on :data:`sys.stderr` so
      systemd's journald captures the same records under the user unit
      (Req. 8.2 — "via the Pi's standard system log facility").
    * Attaches a :class:`RedactionFilter` to **each** handler (Req. 8.5).
    * Idempotent: a previous renewsable handler set is removed first so
      repeated calls do not double-log.
    """
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()

    # Idempotency: remove only handlers we previously installed. Anything
    # else (pytest's LogCaptureHandler, an embedding host's handler, ...)
    # stays put.
    for handler in list(root.handlers):
        if getattr(handler, _OWN_MARKER, False):
            root.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                # Closing must not break configuration. The handler is
                # already detached; leaking the file descriptor is
                # acceptable in the pathological case.
                pass

    formatter = logging.Formatter(_FORMAT)
    redactor = RedactionFilter()

    file_handler = logging.handlers.TimedRotatingFileHandler(
        filename=str(log_dir / "renewsable.log"),
        when="midnight",
        interval=1,
        backupCount=14,
        encoding="utf-8",
        delay=False,
        utc=False,
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(redactor)
    setattr(file_handler, _OWN_MARKER, True)

    stderr_handler = logging.StreamHandler(stream=sys.stderr)
    stderr_handler.setFormatter(formatter)
    # Each handler gets its own filter instance so adding/removing one does
    # not affect the other (filters are not shared list members in the
    # stdlib but using distinct instances is the conventional, surprise-free
    # choice).
    stderr_handler.addFilter(RedactionFilter())
    setattr(stderr_handler, _OWN_MARKER, True)

    root.addHandler(file_handler)
    root.addHandler(stderr_handler)

    # ``setLevel`` accepts both a level name ("INFO") and an int. Normalize
    # to upper-case so callers may pass "info" without surprise.
    root.setLevel(level.upper() if isinstance(level, str) else level)
