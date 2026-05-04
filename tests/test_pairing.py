"""Unit tests for :mod:`renewsable.pairing` (Task 2.3).

Design reference: "Pairing" component block in
``.kiro/specs/daily-paper/design.md``.

Requirements covered:
- 6.2 Pair prompts for one-time code, persists token.
- 6.3 Subsequent runs headless (token cached).
- 8.5 Never log the device token, one-time code, or any credential.

Testability note
----------------
``rmapi`` does not exist on the macOS dev box or in CI. Every test MUST
patch ``renewsable.pairing.subprocess.run`` so no real subprocess is
spawned. The pairing module exposes ``subprocess`` as a module-level
alias specifically for this boundary (same pattern as ``scheduler``).

The rmapi config path is resolved via ``renewsable.paths.rmapi_config_path``,
which is **platform-aware**: ``$XDG_CONFIG_HOME/rmapi/rmapi.conf`` on Linux
but ``$HOME/Library/Application Support/rmapi/rmapi.conf`` on macOS (rmapi
itself uses Go's ``os.UserConfigDir()``). Tests pin both ``$XDG_CONFIG_HOME``
*and* ``paths.sys.platform = "linux"`` (plus clear ``$RMAPI_CONFIG``) so the
resolved path is deterministic on any host the suite runs on, including the
macOS dev box.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from renewsable import pairing as pairing_mod
from renewsable import paths as paths_mod
from renewsable.config import Config
from renewsable.errors import PairingError
from renewsable.pairing import Pairing


# Fake "secrets" that MUST NEVER appear in log records.
FAKE_CODE = "ABCD1234"
FAKE_TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJkZXZpY2UiOiJ4In0.sIgNaTuReBlOb"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(tmp_path: Path, rmapi_bin: str = "rmapi") -> Config:
    """Construct a valid Config by hand (no JSON round-trip)."""
    return Config(
        schedule_time="05:30",
        output_dir=tmp_path / "out",
        remarkable_folder="/News",
        stories=[{"provider": "rss", "config": {"rss_path": "https://x/y"}}],
        rmapi_bin=rmapi_bin,
    )


@pytest.fixture
def xdg_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pin rmapi_config_path() to tmp_path so the resolved path is deterministic.

    rmapi_config_path() branches on ``sys.platform`` (Linux uses XDG, macOS
    uses Library/Application Support) and honours ``$RMAPI_CONFIG`` as an
    explicit override. To make the Linux-XDG branch the only path under test,
    we:

    1. Force the platform alias to ``"linux"`` so rmapi_config_path() takes
       the XDG branch even on a macOS dev box.
    2. Clear ``$RMAPI_CONFIG`` so its override does not pre-empt XDG.
    3. Pin ``$XDG_CONFIG_HOME`` to ``tmp_path``.

    The returned path is ``$XDG_CONFIG_HOME/rmapi/rmapi.conf``.
    """
    monkeypatch.setattr(paths_mod.sys, "platform", "linux")
    monkeypatch.delenv("RMAPI_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path / "rmapi" / "rmapi.conf"


class _FakeRun:
    """Capturing replacement for ``subprocess.run``.

    Records every invocation (argv + kwargs). ``side_effect`` is a
    callable(argv, kwargs) run before returning; tests use it to simulate
    rmapi either writing the token file or not. ``returncode`` is the rc
    returned to the caller.
    """

    def __init__(
        self,
        *,
        side_effect: Any = None,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.side_effect = side_effect
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    def __call__(self, argv: list[str], **kwargs: Any) -> Any:
        self.calls.append((list(argv), dict(kwargs)))
        if self.side_effect is not None:
            self.side_effect(argv, kwargs)
        return SimpleNamespace(
            returncode=self.returncode, stdout=self.stdout, stderr=self.stderr
        )


# ---------------------------------------------------------------------------
# 1. is_paired()
# ---------------------------------------------------------------------------


def test_is_paired_false_when_config_missing(tmp_path, xdg_tmp, monkeypatch):
    # rmapi config file does NOT exist.
    assert not xdg_tmp.exists()

    cfg = _make_config(tmp_path)
    p = Pairing(cfg)
    assert p.is_paired() is False


def test_is_paired_false_when_config_empty(tmp_path, xdg_tmp, monkeypatch):
    # File exists but is zero bytes — user touched it but rmapi never wrote.
    xdg_tmp.parent.mkdir(parents=True, exist_ok=True)
    xdg_tmp.write_text("", encoding="utf-8")
    assert xdg_tmp.exists() and xdg_tmp.stat().st_size == 0

    cfg = _make_config(tmp_path)
    p = Pairing(cfg)
    assert p.is_paired() is False


def test_is_paired_true_when_config_has_content(tmp_path, xdg_tmp, monkeypatch):
    xdg_tmp.parent.mkdir(parents=True, exist_ok=True)
    xdg_tmp.write_text(f"devicetoken: {FAKE_TOKEN}\n", encoding="utf-8")
    assert xdg_tmp.stat().st_size > 0

    cfg = _make_config(tmp_path)
    p = Pairing(cfg)
    assert p.is_paired() is True


# ---------------------------------------------------------------------------
# 2. pair(force=False) short-circuits when already paired
# ---------------------------------------------------------------------------


def test_pair_returns_early_when_already_paired(tmp_path, xdg_tmp, monkeypatch):
    xdg_tmp.parent.mkdir(parents=True, exist_ok=True)
    xdg_tmp.write_text("already-a-token\n", encoding="utf-8")

    fake = _FakeRun()
    monkeypatch.setattr(pairing_mod.subprocess, "run", fake)

    cfg = _make_config(tmp_path)
    Pairing(cfg).pair()  # must NOT raise and must NOT spawn.

    assert fake.calls == []


# ---------------------------------------------------------------------------
# 3. pair(force=True) re-runs even if already paired
# ---------------------------------------------------------------------------


def test_pair_force_invokes_subprocess_even_if_paired(
    tmp_path, xdg_tmp, monkeypatch
):
    # Pre-populate so is_paired() == True.
    xdg_tmp.parent.mkdir(parents=True, exist_ok=True)
    xdg_tmp.write_text("old-token\n", encoding="utf-8")

    def rewrite_token(argv: list[str], kwargs: dict[str, Any]) -> None:
        # Simulate rmapi overwriting the token file with a fresh token.
        xdg_tmp.write_text(f"devicetoken: {FAKE_TOKEN}\n", encoding="utf-8")

    fake = _FakeRun(side_effect=rewrite_token)
    monkeypatch.setattr(pairing_mod.subprocess, "run", fake)

    cfg = _make_config(tmp_path)
    Pairing(cfg).pair(force=True)

    assert len(fake.calls) == 1
    assert fake.calls[0][0] == [cfg.rmapi_bin]


# ---------------------------------------------------------------------------
# 4. pair() spawns rmapi with inherited stdio
# ---------------------------------------------------------------------------


def test_pair_spawns_rmapi_with_inherited_stdio(tmp_path, xdg_tmp, monkeypatch):
    # Not yet paired: no config file.
    assert not xdg_tmp.exists()

    def write_token(argv: list[str], kwargs: dict[str, Any]) -> None:
        xdg_tmp.parent.mkdir(parents=True, exist_ok=True)
        xdg_tmp.write_text(f"devicetoken: {FAKE_TOKEN}\n", encoding="utf-8")

    fake = _FakeRun(side_effect=write_token)
    monkeypatch.setattr(pairing_mod.subprocess, "run", fake)

    cfg = _make_config(tmp_path, rmapi_bin="/usr/local/bin/rmapi")
    Pairing(cfg).pair()

    assert len(fake.calls) == 1
    argv, kwargs = fake.calls[0]
    assert argv == ["/usr/local/bin/rmapi"]
    # stdin/stdout/stderr must all be inherited (i.e. None), so the user
    # can type the one-time code directly into rmapi and see its prompts.
    assert kwargs.get("stdin") is None
    assert kwargs.get("stdout") is None
    assert kwargs.get("stderr") is None
    # ``check=False``: rmapi's exit code is ignored; what matters is the
    # token file having been written.
    assert kwargs.get("check") is False


# ---------------------------------------------------------------------------
# 5. pair() raises PairingError when no token was written
# ---------------------------------------------------------------------------


def test_pair_raises_when_subprocess_leaves_no_token(
    tmp_path, xdg_tmp, monkeypatch
):
    # Not paired, and the fake rmapi will NOT write the token.
    assert not xdg_tmp.exists()

    fake = _FakeRun()  # no side_effect -> no file written
    monkeypatch.setattr(pairing_mod.subprocess, "run", fake)

    cfg = _make_config(tmp_path)
    with pytest.raises(PairingError) as excinfo:
        Pairing(cfg).pair()

    # Subprocess was actually invoked (we wanted to *try* pairing).
    assert len(fake.calls) == 1
    # Message names the missing token path so the user can investigate.
    assert str(xdg_tmp) in excinfo.value.message
    # Remediation points back at the CLI command.
    assert excinfo.value.remediation is not None
    assert "renewsable pair" in excinfo.value.remediation


def test_pair_raises_when_subprocess_writes_empty_file(
    tmp_path, xdg_tmp, monkeypatch
):
    # rmapi created the config file but it ended up empty — treat as failure.
    def write_empty(argv: list[str], kwargs: dict[str, Any]) -> None:
        xdg_tmp.parent.mkdir(parents=True, exist_ok=True)
        xdg_tmp.write_text("", encoding="utf-8")

    fake = _FakeRun(side_effect=write_empty)
    monkeypatch.setattr(pairing_mod.subprocess, "run", fake)

    cfg = _make_config(tmp_path)
    with pytest.raises(PairingError):
        Pairing(cfg).pair()


# ---------------------------------------------------------------------------
# 6. pair() succeeds when subprocess writes a real token
# ---------------------------------------------------------------------------


def test_pair_succeeds_when_token_written(tmp_path, xdg_tmp, monkeypatch):
    def write_token(argv: list[str], kwargs: dict[str, Any]) -> None:
        xdg_tmp.parent.mkdir(parents=True, exist_ok=True)
        xdg_tmp.write_text(f"devicetoken: {FAKE_TOKEN}\n", encoding="utf-8")

    fake = _FakeRun(side_effect=write_token)
    monkeypatch.setattr(pairing_mod.subprocess, "run", fake)

    cfg = _make_config(tmp_path)
    # Must not raise.
    Pairing(cfg).pair()
    # Post-condition: token file is present and non-empty.
    assert xdg_tmp.exists() and xdg_tmp.stat().st_size > 0


# ---------------------------------------------------------------------------
# 7. Logging: Pairing itself MUST NOT emit the code or token
# ---------------------------------------------------------------------------


def test_pair_never_logs_code_or_token(tmp_path, xdg_tmp, monkeypatch, caplog):
    """Pairing's own log calls must never name the one-time code or the token.

    This is defence-in-depth: even if the RedactionFilter from task 1.4 is
    not installed (unit-test context here uses pytest's caplog), Pairing
    must not construct log records that include these secrets.

    We simulate an rmapi that "echoed" the code + token on its stdout/stderr
    (via ``stdout``/``stderr`` on the CompletedProcess); Pairing must not
    capture or forward that output into any log record.
    """
    # rmapi writes a token and "echoes" the code/token on its stdout/stderr.
    def write_token(argv: list[str], kwargs: dict[str, Any]) -> None:
        xdg_tmp.parent.mkdir(parents=True, exist_ok=True)
        xdg_tmp.write_text(f"devicetoken: {FAKE_TOKEN}\n", encoding="utf-8")

    fake = _FakeRun(
        side_effect=write_token,
        stdout=f"Enter code: {FAKE_CODE}\nstored token {FAKE_TOKEN}\n",
        stderr=f"debug: using code {FAKE_CODE}\n",
    )
    monkeypatch.setattr(pairing_mod.subprocess, "run", fake)

    cfg = _make_config(tmp_path)
    # Capture at DEBUG so we see everything Pairing logs.
    caplog.set_level(logging.DEBUG, logger="renewsable.pairing")
    Pairing(cfg).pair()

    # No record produced by pairing may contain the fake code or token —
    # in the raw message, the formatted message, or the args tuple.
    for record in caplog.records:
        # Only enforce against our own module's records.
        if not record.name.startswith("renewsable.pairing"):
            continue
        raw_msg = record.msg if isinstance(record.msg, str) else str(record.msg)
        formatted = record.getMessage()
        args_repr = repr(record.args)
        for secret in (FAKE_CODE, FAKE_TOKEN):
            assert secret not in raw_msg, (
                f"log record raw msg leaked secret {secret!r}: {raw_msg!r}"
            )
            assert secret not in formatted, (
                f"log record formatted msg leaked secret {secret!r}: {formatted!r}"
            )
            assert secret not in args_repr, (
                f"log record args leaked secret {secret!r}: {args_repr!r}"
            )


def test_pair_failure_logs_do_not_leak_secrets(
    tmp_path, xdg_tmp, monkeypatch, caplog
):
    """The failure path (no token written) also must not log code/token."""
    fake = _FakeRun(
        stdout=f"Enter code: {FAKE_CODE}\n",
        stderr=f"failed after code {FAKE_CODE}\n",
    )
    monkeypatch.setattr(pairing_mod.subprocess, "run", fake)

    cfg = _make_config(tmp_path)
    caplog.set_level(logging.DEBUG, logger="renewsable.pairing")
    with pytest.raises(PairingError):
        Pairing(cfg).pair()

    for record in caplog.records:
        if not record.name.startswith("renewsable.pairing"):
            continue
        raw_msg = record.msg if isinstance(record.msg, str) else str(record.msg)
        formatted = record.getMessage()
        args_repr = repr(record.args)
        for secret in (FAKE_CODE, FAKE_TOKEN):
            assert secret not in raw_msg
            assert secret not in formatted
            assert secret not in args_repr
