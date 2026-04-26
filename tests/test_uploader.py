"""Unit tests for :mod:`renewsable.uploader` (Task 2.5).

Design reference: the "Uploader" component block in
``.kiro/specs/daily-paper/design.md``.

Requirements covered:
- 4.1 The combined build+upload flow uploads the produced EPUB.
- 4.2 If the destination folder does not exist, create it before uploading.
- 4.3 Re-upload of the same date replaces the prior file (``put --force``).
- 4.4 Upload failure → non-zero, log folder/path/reason, keep local EPUB.
- 6.4 Missing/rejected token → explanatory error with "run `renewsable pair`".
- 8.4 Upload failure log identifies folder, local EPUB path, reason.
- 9.3 Bounded retries with exponential backoff.
- 9.5 No hang / crash / partial upload on upload failure.

Testability note
----------------
``rmapi`` is not installed on the dev box or in CI. Every test MUST patch
``renewsable.uploader.subprocess.run`` and
``renewsable.uploader._time.sleep`` so no real subprocess is spawned and
no real sleeps happen. The uploader module exposes those as module-level
aliases specifically for this boundary — the same pattern used by
:mod:`renewsable.builder`, :mod:`renewsable.scheduler`,
:mod:`renewsable.pairing`.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from renewsable import uploader as uploader_mod
from renewsable.config import Config
from renewsable.errors import UploadError
from renewsable.uploader import Uploader


# A JWT-shaped fake token used to verify the error-message redactor.
FAKE_TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJkZXZpY2UiOiJ4In0.sIgNaTuReBlOb"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    tmp_path: Path,
    *,
    folder: str = "/News",
    rmapi_bin: str = "rmapi",
    upload_retries: int = 3,
    upload_backoff_s: float = 2.0,
) -> Config:
    """Build a valid Config by hand (no JSON round-trip)."""
    return Config(
        schedule_time="05:30",
        output_dir=tmp_path / "out",
        remarkable_folder=folder,
        stories=[{"provider": "rss", "config": {"rss_path": "https://x/y"}}],
        rmapi_bin=rmapi_bin,
        upload_retries=upload_retries,
        upload_backoff_s=upload_backoff_s,
    )


def _make_pdf(tmp_path: Path, name: str = "renewsable-2026-04-19.pdf") -> Path:
    """Write a minimal %PDF- file in tmp_path and return its path."""
    pdf = tmp_path / name
    pdf.write_bytes(b"%PDF-1.4\n%fake\n%%EOF\n")
    return pdf


class _ScriptedRun:
    """Replacement for ``subprocess.run`` driven by a scripted queue.

    Each call consumes one entry from ``script``. An entry is a
    ``(returncode, stderr)`` tuple, OR a callable ``(argv, kwargs) -> tuple``
    that returns ``(returncode, stderr)`` dynamically. Stdout is always
    empty.

    All invocations (argv + relevant kwargs) are appended to ``calls`` so
    tests can assert the exact command lines.
    """

    def __init__(self, script: list[Any]) -> None:
        self._script = list(script)
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> Any:
        self.calls.append((list(argv), dict(kwargs)))
        if not self._script:
            raise AssertionError(
                f"subprocess.run called more times than scripted; argv={argv!r}"
            )
        entry = self._script.pop(0)
        if callable(entry):
            rc, stderr = entry(argv, kwargs)
        else:
            rc, stderr = entry
        return SimpleNamespace(returncode=rc, stdout="", stderr=stderr)


class _SleepSpy:
    """Record ``time.sleep`` durations without actually sleeping."""

    def __init__(self) -> None:
        self.durations: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.durations.append(seconds)


# ---------------------------------------------------------------------------
# 1. Happy path — mkdir + put argv (Req 4.1, 4.2, 4.3)
# ---------------------------------------------------------------------------


def test_upload_happy_path_mkdir_then_put(tmp_path, monkeypatch):
    pdf = _make_pdf(tmp_path)
    script = _ScriptedRun([(0, ""), (0, "")])  # mkdir 0, put 0
    sleep_spy = _SleepSpy()
    monkeypatch.setattr(uploader_mod.subprocess, "run", script)
    monkeypatch.setattr(uploader_mod._time, "sleep", sleep_spy)

    cfg = _make_config(tmp_path, rmapi_bin="/usr/local/bin/rmapi")
    Uploader(cfg).upload(pdf)

    assert len(script.calls) == 2
    # mkdir first
    assert script.calls[0][0] == ["/usr/local/bin/rmapi", "mkdir", "/News"]
    # then put --force
    assert script.calls[1][0] == [
        "/usr/local/bin/rmapi",
        "put",
        "--force",
        str(pdf),
        "/News/",
    ]
    # Local PDF remains (upload never deletes).
    assert pdf.exists()
    # No sleeps on happy path.
    assert sleep_spy.durations == []


def test_subprocess_invocations_bounded_by_rmapi_timeout_constant(tmp_path, monkeypatch):
    """The subprocess invocations must be bounded by ``_RMAPI_TIMEOUT_S``
    and capture stdio so stderr can be classified.
    """
    pdf = _make_pdf(tmp_path)
    script = _ScriptedRun([(0, ""), (0, "")])
    monkeypatch.setattr(uploader_mod.subprocess, "run", script)
    monkeypatch.setattr(uploader_mod._time, "sleep", _SleepSpy())
    monkeypatch.setattr(uploader_mod, "_RMAPI_TIMEOUT_S", 42)

    cfg = _make_config(tmp_path)
    Uploader(cfg).upload(pdf)

    for _argv, kwargs in script.calls:
        assert kwargs.get("capture_output") is True
        assert kwargs.get("text") is True
        assert kwargs.get("timeout") == 42
        assert kwargs.get("check") is False


# ---------------------------------------------------------------------------
# 2. mkdir "already exists" is treated as success (Req 4.2)
# ---------------------------------------------------------------------------


def test_mkdir_already_exists_is_treated_as_success(tmp_path, monkeypatch):
    pdf = _make_pdf(tmp_path)
    # mkdir fails with an "already exists" stderr, then put succeeds.
    script = _ScriptedRun(
        [
            (1, "ERROR: entry already exists at /News"),
            (0, ""),
        ]
    )
    monkeypatch.setattr(uploader_mod.subprocess, "run", script)
    monkeypatch.setattr(uploader_mod._time, "sleep", _SleepSpy())

    cfg = _make_config(tmp_path)
    Uploader(cfg).upload(pdf)  # must not raise

    # put must have been called despite mkdir's non-zero exit.
    assert len(script.calls) == 2
    assert script.calls[1][0][1:4] == ["put", "--force", str(pdf)]


def test_mkdir_exists_pattern_variants_accepted(tmp_path, monkeypatch):
    """``exists`` (without ``already``) should also be treated as success.

    rmapi variants have used "entry exists", "folder exists", and
    "already exists" in different releases; classify liberally.
    """
    pdf = _make_pdf(tmp_path)
    script = _ScriptedRun(
        [
            (1, "folder already exists: /News"),
            (0, ""),
        ]
    )
    monkeypatch.setattr(uploader_mod.subprocess, "run", script)
    monkeypatch.setattr(uploader_mod._time, "sleep", _SleepSpy())

    cfg = _make_config(tmp_path)
    Uploader(cfg).upload(pdf)

    assert len(script.calls) == 2


# ---------------------------------------------------------------------------
# 3. mkdir genuine failure (not "exists") → UploadError (Req 4.4)
# ---------------------------------------------------------------------------


def test_mkdir_genuine_failure_raises_and_skips_put(tmp_path, monkeypatch):
    pdf = _make_pdf(tmp_path)
    script = _ScriptedRun([(1, "ERROR: permission denied writing to /News")])
    monkeypatch.setattr(uploader_mod.subprocess, "run", script)
    monkeypatch.setattr(uploader_mod._time, "sleep", _SleepSpy())

    cfg = _make_config(tmp_path)
    with pytest.raises(UploadError) as excinfo:
        Uploader(cfg).upload(pdf)

    # put must not have been attempted.
    assert len(script.calls) == 1
    # Error names the folder so the operator can investigate.
    assert "/News" in excinfo.value.message
    # PDF still on disk (Req 4.4).
    assert pdf.exists()


# ---------------------------------------------------------------------------
# 4. put retries then succeeds: exit sequence (1, 1, 0) (Req 9.3)
# ---------------------------------------------------------------------------


def test_put_succeeds_after_two_retries(tmp_path, monkeypatch):
    pdf = _make_pdf(tmp_path)
    # mkdir 0, then put: 1, 1, 0.
    script = _ScriptedRun(
        [
            (0, ""),  # mkdir
            (1, "connection reset"),  # put attempt 1
            (1, "connection reset"),  # put attempt 2
            (0, ""),  # put attempt 3
        ]
    )
    sleep_spy = _SleepSpy()
    monkeypatch.setattr(uploader_mod.subprocess, "run", script)
    monkeypatch.setattr(uploader_mod._time, "sleep", sleep_spy)

    cfg = _make_config(tmp_path, upload_retries=3, upload_backoff_s=2.0)
    Uploader(cfg).upload(pdf)  # must not raise

    # mkdir + 3 put attempts == 4 subprocess calls.
    assert len(script.calls) == 4
    # Exactly two sleeps (between attempts 1→2 and 2→3).
    assert len(sleep_spy.durations) == 2


# ---------------------------------------------------------------------------
# 5. put exhausts retries → UploadError with folder, path, stderr (Req 4.4, 8.4, 9.3)
# ---------------------------------------------------------------------------


def test_put_exhausts_retries_raises_with_folder_path_stderr(
    tmp_path, monkeypatch
):
    pdf = _make_pdf(tmp_path)
    # mkdir ok, then put fails all 3 attempts.
    last_stderr = "connection reset by peer"
    script = _ScriptedRun(
        [
            (0, ""),
            (1, "transient network blip"),
            (1, "another blip"),
            (1, last_stderr),
        ]
    )
    monkeypatch.setattr(uploader_mod.subprocess, "run", script)
    monkeypatch.setattr(uploader_mod._time, "sleep", _SleepSpy())

    cfg = _make_config(tmp_path, upload_retries=3, upload_backoff_s=2.0)
    with pytest.raises(UploadError) as excinfo:
        Uploader(cfg).upload(pdf)

    # mkdir + 3 put attempts.
    assert len(script.calls) == 4
    msg = excinfo.value.message
    # Folder name (Req 8.4).
    assert "/News" in msg
    # Local PDF path (Req 8.4).
    assert str(pdf) in msg
    # Captured stderr (Req 8.4).
    assert last_stderr in msg
    # Local PDF remains (Req 4.4).
    assert pdf.exists()


# ---------------------------------------------------------------------------
# 6. Exponential backoff durations (Req 9.3)
# ---------------------------------------------------------------------------


def test_exponential_backoff_durations(tmp_path, monkeypatch):
    pdf = _make_pdf(tmp_path)
    # mkdir ok, three failing put attempts -> 2 sleeps between them.
    script = _ScriptedRun(
        [
            (0, ""),
            (1, "blip"),
            (1, "blip"),
            (1, "blip"),
        ]
    )
    sleep_spy = _SleepSpy()
    monkeypatch.setattr(uploader_mod.subprocess, "run", script)
    monkeypatch.setattr(uploader_mod._time, "sleep", sleep_spy)

    cfg = _make_config(tmp_path, upload_retries=3, upload_backoff_s=2.0)
    with pytest.raises(UploadError):
        Uploader(cfg).upload(pdf)

    # base=2.0; first sleep is base*2**0=2.0, second is base*2**1=4.0.
    assert sleep_spy.durations == [2.0, 4.0]


# ---------------------------------------------------------------------------
# 7. Token error → no retry, remediation hint (Req 6.4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token_stderr",
    [
        "ERROR: invalid token, please re-authenticate",
        "HTTP 401 unauthorized",
        "authentication failed",
        "expired token",
        "rmapi: UNAUTHORIZED",
    ],
)
def test_token_error_raises_immediately_without_retry(
    tmp_path, monkeypatch, token_stderr
):
    pdf = _make_pdf(tmp_path)
    # mkdir 0, put fails once with a token-class stderr.
    script = _ScriptedRun([(0, ""), (1, token_stderr)])
    sleep_spy = _SleepSpy()
    monkeypatch.setattr(uploader_mod.subprocess, "run", script)
    monkeypatch.setattr(uploader_mod._time, "sleep", sleep_spy)

    cfg = _make_config(tmp_path, upload_retries=3, upload_backoff_s=2.0)
    with pytest.raises(UploadError) as excinfo:
        Uploader(cfg).upload(pdf)

    # mkdir + exactly one put (no retries).
    assert len(script.calls) == 2
    assert sleep_spy.durations == []
    # Remediation names the pair command (Req 6.4).
    assert excinfo.value.remediation is not None
    assert "renewsable pair" in excinfo.value.remediation


# ---------------------------------------------------------------------------
# 8. Network error is retried (Req 9.3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "network_stderr",
    [
        "connection refused",
        "connection timed out",
        "dns lookup failed",
        "network unreachable",
        "no route to host",
        "TLS handshake error",
    ],
)
def test_network_error_is_retried(tmp_path, monkeypatch, network_stderr):
    pdf = _make_pdf(tmp_path)
    # mkdir ok, put fails with a network-class stderr once, then succeeds.
    script = _ScriptedRun([(0, ""), (1, network_stderr), (0, "")])
    sleep_spy = _SleepSpy()
    monkeypatch.setattr(uploader_mod.subprocess, "run", script)
    monkeypatch.setattr(uploader_mod._time, "sleep", sleep_spy)

    cfg = _make_config(tmp_path, upload_retries=3, upload_backoff_s=1.0)
    Uploader(cfg).upload(pdf)  # must not raise

    assert len(script.calls) == 3
    assert sleep_spy.durations == [1.0]


# ---------------------------------------------------------------------------
# 9. "Other" (non-classified) errors are also retried as transient
# ---------------------------------------------------------------------------


def test_other_error_is_retried_as_transient(tmp_path, monkeypatch):
    pdf = _make_pdf(tmp_path)
    script = _ScriptedRun(
        [
            (0, ""),
            (1, "unexpected server error 500"),
            (0, ""),
        ]
    )
    sleep_spy = _SleepSpy()
    monkeypatch.setattr(uploader_mod.subprocess, "run", script)
    monkeypatch.setattr(uploader_mod._time, "sleep", sleep_spy)

    cfg = _make_config(tmp_path, upload_retries=3, upload_backoff_s=1.0)
    Uploader(cfg).upload(pdf)  # must not raise

    assert len(script.calls) == 3
    assert sleep_spy.durations == [1.0]


# ---------------------------------------------------------------------------
# 10. Stderr redaction in error message (Req 8.5-in-spirit / 8.4)
# ---------------------------------------------------------------------------


def test_stderr_redacted_in_error_message(tmp_path, monkeypatch):
    pdf = _make_pdf(tmp_path)
    leaky_stderr = (
        f"unexpected failure; sent Authorization: Bearer {FAKE_TOKEN} and "
        f"got no response"
    )
    script = _ScriptedRun(
        [
            (0, ""),
            (1, leaky_stderr),
            (1, leaky_stderr),
            (1, leaky_stderr),
        ]
    )
    monkeypatch.setattr(uploader_mod.subprocess, "run", script)
    monkeypatch.setattr(uploader_mod._time, "sleep", _SleepSpy())

    cfg = _make_config(tmp_path, upload_retries=3, upload_backoff_s=1.0)
    with pytest.raises(UploadError) as excinfo:
        Uploader(cfg).upload(pdf)

    # The raw token must NEVER appear in the surfaced error message.
    assert FAKE_TOKEN not in excinfo.value.message
    assert FAKE_TOKEN not in str(excinfo.value)


# ---------------------------------------------------------------------------
# 11. Explicit folder argument overrides config.remarkable_folder
# ---------------------------------------------------------------------------


def test_explicit_folder_argument_overrides_config(tmp_path, monkeypatch):
    pdf = _make_pdf(tmp_path)
    script = _ScriptedRun([(0, ""), (0, "")])
    monkeypatch.setattr(uploader_mod.subprocess, "run", script)
    monkeypatch.setattr(uploader_mod._time, "sleep", _SleepSpy())

    cfg = _make_config(tmp_path, folder="/News")
    Uploader(cfg).upload(pdf, folder="/Custom")

    assert script.calls[0][0][1:] == ["mkdir", "/Custom"]
    assert script.calls[1][0][1:] == ["put", "--force", str(pdf), "/Custom/"]


# ---------------------------------------------------------------------------
# 12. PDF kept on failure (Req 4.4)
# ---------------------------------------------------------------------------


def test_pdf_kept_on_failure(tmp_path, monkeypatch):
    pdf = _make_pdf(tmp_path)
    script = _ScriptedRun(
        [
            (0, ""),
            (1, "boom"),
            (1, "boom"),
            (1, "boom"),
        ]
    )
    monkeypatch.setattr(uploader_mod.subprocess, "run", script)
    monkeypatch.setattr(uploader_mod._time, "sleep", _SleepSpy())

    cfg = _make_config(tmp_path, upload_retries=3, upload_backoff_s=1.0)
    with pytest.raises(UploadError):
        Uploader(cfg).upload(pdf)

    assert pdf.exists()


# ---------------------------------------------------------------------------
# 13. rmapi binary not found → UploadError with install remediation
# ---------------------------------------------------------------------------


def test_rmapi_binary_not_found(tmp_path, monkeypatch):
    pdf = _make_pdf(tmp_path)

    def raise_fnf(argv: list[str], **kwargs: Any) -> Any:
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr(uploader_mod.subprocess, "run", raise_fnf)
    monkeypatch.setattr(uploader_mod._time, "sleep", _SleepSpy())

    cfg = _make_config(tmp_path, rmapi_bin="/no/such/rmapi")
    with pytest.raises(UploadError) as excinfo:
        Uploader(cfg).upload(pdf)

    assert "/no/such/rmapi" in excinfo.value.message
    assert pdf.exists()


# ---------------------------------------------------------------------------
# 14. subprocess timeout during put → UploadError (Req 9.5 — no hang)
# ---------------------------------------------------------------------------


def test_put_timeout_raises_upload_error(tmp_path, monkeypatch):
    import subprocess as real_subprocess

    pdf = _make_pdf(tmp_path)
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: Any) -> Any:
        calls.append(list(argv))
        if argv[1] == "mkdir":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        # put: simulate a hang that exceeds _RMAPI_TIMEOUT_S.
        raise real_subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout", 0))

    monkeypatch.setattr(uploader_mod.subprocess, "run", run)
    monkeypatch.setattr(uploader_mod._time, "sleep", _SleepSpy())

    cfg = _make_config(tmp_path, upload_retries=3, upload_backoff_s=1.0)
    with pytest.raises(UploadError):
        Uploader(cfg).upload(pdf)

    # Local PDF remains.
    assert pdf.exists()
