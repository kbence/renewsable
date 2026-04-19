"""Unit tests for :mod:`renewsable.paths`.

Design reference: "File Structure Plan" and the "Data Models" persistent-file
table in ``.kiro/specs/daily-paper/design.md``.

Requirements covered:
- 1.1 (documented default config path)
- 5.1 (systemd user-unit directory)
- 8.2 (documented plain-text log directory path)

The helpers honour the XDG Base Directory Specification:
* ``$XDG_CONFIG_HOME`` rooted files fall back to ``~/.config`` when the env
  var is unset *or* set to an empty string (per spec).
* ``$XDG_STATE_HOME`` rooted files fall back to ``~/.local/state`` under the
  same rule.

Each helper is exercised in three scenarios:
1. env var set to a concrete path  -> returned path is rooted there
2. env var unset                    -> returned path is rooted at the HOME fallback
3. env var set to ``""``            -> same fallback as (2)

Every helper is additionally checked to return a :class:`pathlib.Path`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from renewsable.paths import (
    default_config_path,
    default_log_dir,
    default_output_dir,
    rmapi_config_path,
    systemd_user_unit_dir,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Pin ``$HOME`` so ``Path.home()`` is deterministic regardless of the runner."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    # macOS / Linux: ``Path.home()`` uses ``HOME`` first. Also clear any
    # alternate resolvers so tests do not depend on the host environment.
    monkeypatch.delenv("USERPROFILE", raising=False)
    return home


# ---------------------------------------------------------------------------
# default_config_path
# ---------------------------------------------------------------------------


class TestDefaultConfigPath:
    def test_returns_path_instance(self, fake_home, monkeypatch):
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        assert isinstance(default_config_path(), Path)

    def test_xdg_config_home_set(self, fake_home, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/x")
        assert default_config_path() == Path("/tmp/x/renewsable/config.json")

    def test_xdg_config_home_unset_falls_back_to_home(self, fake_home, monkeypatch):
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        assert default_config_path() == fake_home / ".config" / "renewsable" / "config.json"

    def test_xdg_config_home_empty_falls_back_to_home(self, fake_home, monkeypatch):
        # XDG spec: empty string is treated as unset.
        monkeypatch.setenv("XDG_CONFIG_HOME", "")
        assert default_config_path() == fake_home / ".config" / "renewsable" / "config.json"


# ---------------------------------------------------------------------------
# default_output_dir
# ---------------------------------------------------------------------------


class TestDefaultOutputDir:
    def test_returns_path_instance(self, fake_home, monkeypatch):
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
        assert isinstance(default_output_dir(), Path)

    def test_xdg_state_home_set(self, fake_home, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", "/tmp/x")
        assert default_output_dir() == Path("/tmp/x/renewsable/out")

    def test_xdg_state_home_unset_falls_back_to_home(self, fake_home, monkeypatch):
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
        assert default_output_dir() == fake_home / ".local" / "state" / "renewsable" / "out"

    def test_xdg_state_home_empty_falls_back_to_home(self, fake_home, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", "")
        assert default_output_dir() == fake_home / ".local" / "state" / "renewsable" / "out"


# ---------------------------------------------------------------------------
# default_log_dir
# ---------------------------------------------------------------------------


class TestDefaultLogDir:
    def test_returns_path_instance(self, fake_home, monkeypatch):
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
        assert isinstance(default_log_dir(), Path)

    def test_xdg_state_home_set(self, fake_home, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", "/tmp/x")
        assert default_log_dir() == Path("/tmp/x/renewsable/logs")

    def test_xdg_state_home_unset_falls_back_to_home(self, fake_home, monkeypatch):
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
        assert default_log_dir() == fake_home / ".local" / "state" / "renewsable" / "logs"

    def test_xdg_state_home_empty_falls_back_to_home(self, fake_home, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", "")
        assert default_log_dir() == fake_home / ".local" / "state" / "renewsable" / "logs"


# ---------------------------------------------------------------------------
# systemd_user_unit_dir
# ---------------------------------------------------------------------------


class TestSystemdUserUnitDir:
    def test_returns_path_instance(self, fake_home, monkeypatch):
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        assert isinstance(systemd_user_unit_dir(), Path)

    def test_xdg_config_home_set(self, fake_home, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/x")
        assert systemd_user_unit_dir() == Path("/tmp/x/systemd/user")

    def test_xdg_config_home_unset_falls_back_to_home(self, fake_home, monkeypatch):
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        assert systemd_user_unit_dir() == fake_home / ".config" / "systemd" / "user"

    def test_xdg_config_home_empty_falls_back_to_home(self, fake_home, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", "")
        assert systemd_user_unit_dir() == fake_home / ".config" / "systemd" / "user"


# ---------------------------------------------------------------------------
# rmapi_config_path
# ---------------------------------------------------------------------------


class TestRmapiConfigPath:
    def test_returns_path_instance(self, fake_home, monkeypatch):
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        assert isinstance(rmapi_config_path(), Path)

    def test_xdg_config_home_set(self, fake_home, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/x")
        assert rmapi_config_path() == Path("/tmp/x/rmapi/rmapi.conf")

    def test_xdg_config_home_unset_falls_back_to_home(self, fake_home, monkeypatch):
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        assert rmapi_config_path() == fake_home / ".config" / "rmapi" / "rmapi.conf"

    def test_xdg_config_home_empty_falls_back_to_home(self, fake_home, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", "")
        assert rmapi_config_path() == fake_home / ".config" / "rmapi" / "rmapi.conf"


# ---------------------------------------------------------------------------
# Cross-helper invariant: no helper creates a directory. Each returned path
# is purely a computation; callers remain responsible for ``mkdir``.
# ---------------------------------------------------------------------------


class TestNoSideEffects:
    def test_helpers_do_not_create_directories(self, fake_home, monkeypatch, tmp_path):
        root = tmp_path / "xdg_root"
        monkeypatch.setenv("XDG_CONFIG_HOME", str(root / "config"))
        monkeypatch.setenv("XDG_STATE_HOME", str(root / "state"))

        # Call every helper.
        default_config_path()
        default_output_dir()
        default_log_dir()
        systemd_user_unit_dir()
        rmapi_config_path()

        # None of the parents should have been materialised.
        assert not root.exists()
