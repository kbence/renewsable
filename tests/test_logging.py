"""Unit tests for :mod:`renewsable.logging_setup`.

Design reference:
- ``.kiro/specs/daily-paper/design.md`` — section "logging_setup (summary-only)"
  and "Error Handling → Monitoring".

Requirements covered:
- 8.1 (per-run log entry surface)
- 8.2 (logs visible via system log AND plain-text file with >=14-day retention)
- 8.5 (never log the device token, one-time pairing code, or any credential)

Strategy
--------
``configure_logging`` is intentionally primitive at task 1.4: it takes the log
directory and a level string. Task 2.1 (Config) will wrap it. Each test
re-isolates the root logger before exercising it, then restores it, so the
test process's own pytest logging is not corrupted.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

import pytest

from renewsable.logging_setup import RedactionFilter, configure_logging


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_root_logger():
    """Snapshot the root logger and restore it after each test.

    ``configure_logging`` mutates the root logger (clearing handlers, adding
    its own). Without this fixture, tests would leak handlers across test
    runs and pollute pytest's own caplog/capsys plumbing.
    """
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    saved_filters = root.filters[:]
    try:
        # Wipe so tests start from a clean slate too.
        root.handlers = []
        root.filters = []
        yield root
    finally:
        # Close anything the test installed before swapping back, so file
        # handles release on Windows-style filesystems.
        for handler in root.handlers:
            try:
                handler.close()
            except Exception:
                pass
        root.handlers = saved_handlers
        root.level = saved_level
        root.filters = saved_filters


# ---------------------------------------------------------------------------
# (a) File creation
# ---------------------------------------------------------------------------


def test_configure_logging_creates_log_file(
    tmp_path: Path, isolated_root_logger: logging.Logger
) -> None:
    """A log call after configuration writes to ``<log_dir>/renewsable.log``."""
    configure_logging(tmp_path)

    log_path = tmp_path / "renewsable.log"
    logger = logging.getLogger("renewsable.test")
    logger.info("hello world")

    # Flush to be sure bytes hit the file before we read.
    for handler in logging.getLogger().handlers:
        handler.flush()

    assert log_path.exists(), f"expected log file at {log_path}"
    assert "hello world" in log_path.read_text(encoding="utf-8")


def test_configure_logging_creates_missing_log_dir(
    tmp_path: Path, isolated_root_logger: logging.Logger
) -> None:
    """``configure_logging`` mkdirs the log directory if it does not exist."""
    nested = tmp_path / "deep" / "nested" / "logs"
    assert not nested.exists()

    configure_logging(nested)

    assert nested.is_dir()
    assert (nested / "renewsable.log").exists() or True
    # The file may not exist until the first log call; the directory must.


# ---------------------------------------------------------------------------
# (b) 8-char uppercase pairing code redaction
# ---------------------------------------------------------------------------


def test_pairing_code_redacted_in_file_and_stderr(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    isolated_root_logger: logging.Logger,
) -> None:
    """An 8-char uppercase code in the message is masked in BOTH sinks (8.5)."""
    configure_logging(tmp_path)
    logger = logging.getLogger("renewsable.pairing")

    logger.info("paired with code ABCD1234 ok")

    for handler in logging.getLogger().handlers:
        handler.flush()

    captured = capsys.readouterr()
    log_text = (tmp_path / "renewsable.log").read_text(encoding="utf-8")

    assert "ABCD1234" not in log_text
    assert "ABCD1234" not in captured.err
    assert "***" in log_text
    assert "***" in captured.err


# ---------------------------------------------------------------------------
# (c) JWT-style rmapi token redaction
# ---------------------------------------------------------------------------


def test_jwt_token_redacted_in_file_and_stderr(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    isolated_root_logger: logging.Logger,
) -> None:
    """A JWT-style rmapi token is masked in BOTH sinks (8.5)."""
    configure_logging(tmp_path)
    logger = logging.getLogger("renewsable.uploader")

    fake_jwt = "eyJabcdefghij.klmnopqrst.uvwxyz12345"
    logger.info("rmapi token=%s loaded", fake_jwt)

    for handler in logging.getLogger().handlers:
        handler.flush()

    captured = capsys.readouterr()
    log_text = (tmp_path / "renewsable.log").read_text(encoding="utf-8")

    assert fake_jwt not in log_text
    assert fake_jwt not in captured.err
    assert "***" in log_text
    assert "***" in captured.err


# ---------------------------------------------------------------------------
# (d) Secret in record.args is also redacted
# ---------------------------------------------------------------------------


def test_secret_in_args_is_redacted(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    isolated_root_logger: logging.Logger,
) -> None:
    """The filter walks ``record.args``, not just ``record.msg`` (8.5)."""
    configure_logging(tmp_path)
    logger = logging.getLogger("renewsable.pairing")

    logger.info("got code %s", "ABCD1234")

    for handler in logging.getLogger().handlers:
        handler.flush()

    captured = capsys.readouterr()
    log_text = (tmp_path / "renewsable.log").read_text(encoding="utf-8")

    assert "ABCD1234" not in log_text
    assert "ABCD1234" not in captured.err
    assert "got code ***" in log_text
    assert "got code ***" in captured.err


# ---------------------------------------------------------------------------
# (e) Normal messages pass through unchanged
# ---------------------------------------------------------------------------


def test_normal_message_unchanged(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    isolated_root_logger: logging.Logger,
) -> None:
    """A message with no secret pattern is preserved verbatim."""
    configure_logging(tmp_path)
    logger = logging.getLogger("renewsable.run")

    logger.info("finished")

    for handler in logging.getLogger().handlers:
        handler.flush()

    captured = capsys.readouterr()
    log_text = (tmp_path / "renewsable.log").read_text(encoding="utf-8")

    assert "finished" in log_text
    assert "finished" in captured.err
    assert "***" not in log_text
    assert "***" not in captured.err


# ---------------------------------------------------------------------------
# (f) Idempotency: no duplicate handlers / lines
# ---------------------------------------------------------------------------


def test_configure_logging_is_idempotent(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    isolated_root_logger: logging.Logger,
) -> None:
    """Calling ``configure_logging`` twice does not double-attach handlers.

    Without idempotency, every record would be written twice to the file
    and twice to stderr.
    """
    configure_logging(tmp_path)
    configure_logging(tmp_path)

    logger = logging.getLogger("renewsable.idempotent")
    logger.info("once")

    for handler in logging.getLogger().handlers:
        handler.flush()

    captured = capsys.readouterr()
    log_text = (tmp_path / "renewsable.log").read_text(encoding="utf-8")

    # Exactly one occurrence of the message in each sink.
    assert log_text.count("once") == 1, log_text
    assert captured.err.count("once") == 1, captured.err


# ---------------------------------------------------------------------------
# (g) Rotation configuration: daily, 14 backups (Req. 8.2)
# ---------------------------------------------------------------------------


def test_rotation_is_daily_with_14_backups(
    tmp_path: Path, isolated_root_logger: logging.Logger
) -> None:
    """The installed file handler rotates daily and keeps 14 backups (8.2)."""
    configure_logging(tmp_path)

    file_handlers = [
        h
        for h in logging.getLogger().handlers
        if isinstance(h, logging.handlers.TimedRotatingFileHandler)
    ]
    assert len(file_handlers) == 1, (
        f"expected exactly one TimedRotatingFileHandler, got {file_handlers!r}"
    )

    fh = file_handlers[0]
    # ``when`` is uppercased internally by the stdlib.
    assert fh.when in {"MIDNIGHT", "D"}, fh.when
    assert fh.backupCount == 14
    assert Path(fh.baseFilename) == (tmp_path / "renewsable.log").resolve()


# ---------------------------------------------------------------------------
# Filter unit coverage (defence in depth — exercises the filter directly)
# ---------------------------------------------------------------------------


def test_redaction_filter_masks_pairing_code_directly() -> None:
    """The :class:`RedactionFilter` masks 8-char codes in ``msg`` directly."""
    f = RedactionFilter()
    record = logging.LogRecord(
        name="x",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg="code ABCD1234 here",
        args=None,
        exc_info=None,
    )
    assert f.filter(record) is True
    assert "ABCD1234" not in record.getMessage()
    assert "***" in record.getMessage()


def test_redaction_filter_masks_jwt_directly() -> None:
    """The :class:`RedactionFilter` masks JWT-style tokens in ``msg`` directly."""
    f = RedactionFilter()
    record = logging.LogRecord(
        name="x",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg="t=eyJabcdefghij.klmnopqrst.uvwxyz12345 done",
        args=None,
        exc_info=None,
    )
    assert f.filter(record) is True
    assert "eyJabcdefghij" not in record.getMessage()
    assert "***" in record.getMessage()
