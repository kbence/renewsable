"""Default filesystem locations for renewsable.

Design reference: the "File Structure Plan" section and the persistent-file
table in ``.kiro/specs/daily-paper/design.md`` (the "Data Models" subsection
listing each artefact's default location).

Requirements covered:
- 1.1 (configuration file default location on the Pi)
- 5.1 (systemd user-unit install location)
- 8.2 (plain-text log file directory under a documented path)

Contract
--------
Each helper:
* Takes no arguments.
* Reads the relevant XDG environment variable every call (so tests and
  callers that mutate the process environment see the effect immediately).
* Returns a :class:`pathlib.Path`. Returned paths are purely computed —
  **no directory is created**. Callers that need to write to the returned
  location are responsible for ``path.parent.mkdir(parents=True,
  exist_ok=True)`` (or an equivalent).
* Follows the XDG Base Directory Specification, including the rule that an
  *empty-string* value of ``$XDG_CONFIG_HOME`` / ``$XDG_STATE_HOME`` is
  treated the same as the variable being unset.

Why no-arg rather than ``env: dict[str, str] | None = None``
------------------------------------------------------------
1. The "observable" in tasks.md describes in-process env manipulation
   (``XDG_CONFIG_HOME=/tmp/x`` vs unset). Reading ``os.environ`` fresh
   matches that behaviour without extra plumbing.
2. ``pytest``'s ``monkeypatch.setenv`` / ``monkeypatch.delenv`` give the
   equivalent isolation an explicit ``env`` parameter would provide, with
   no added call-site complexity.
3. Downstream callers (config loader, scheduler, uploader, pairing) are
   stateless orchestrators that each need one or two of these paths and
   should not have to thread an ``env`` argument through their APIs.
"""

from __future__ import annotations

import os
import sys  # noqa: F401  (kept as module-level alias for tests; see rmapi_config_path)
from pathlib import Path

__all__ = [
    "default_config_path",
    "default_output_dir",
    "default_log_dir",
    "systemd_user_unit_dir",
    "rmapi_config_path",
]


# ---------------------------------------------------------------------------
# Internal XDG resolution
# ---------------------------------------------------------------------------


def _xdg_base(var: str, default_relative_to_home: str) -> Path:
    """Return the base directory named by ``var``, falling back to ``$HOME``.

    Per the XDG Base Directory Specification, a variable that is unset *or*
    set to the empty string falls back to the default. Non-absolute values
    are also treated as invalid (spec: "if an implementation encounters a
    relative path, it should either ignore the setting or consider the
    variable as not set").
    """
    value = os.environ.get(var, "")
    if value:
        candidate = Path(value)
        if candidate.is_absolute():
            return candidate
    return Path.home() / default_relative_to_home


def _xdg_config_home() -> Path:
    """Return ``$XDG_CONFIG_HOME`` or ``~/.config``."""
    return _xdg_base("XDG_CONFIG_HOME", ".config")


def _xdg_state_home() -> Path:
    """Return ``$XDG_STATE_HOME`` or ``~/.local/state``."""
    return _xdg_base("XDG_STATE_HOME", ".local/state")


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def default_config_path() -> Path:
    """Default path to renewsable's JSON config file.

    ``$XDG_CONFIG_HOME/renewsable/config.json`` if set (non-empty, absolute),
    else ``~/.config/renewsable/config.json``.
    """
    return _xdg_config_home() / "renewsable" / "config.json"


def default_output_dir() -> Path:
    """Default directory for daily EPUB artefacts.

    ``$XDG_STATE_HOME/renewsable/out`` else ``~/.local/state/renewsable/out``.
    """
    return _xdg_state_home() / "renewsable" / "out"


def default_log_dir() -> Path:
    """Default directory for the plain-text run log (Req. 8.2).

    ``$XDG_STATE_HOME/renewsable/logs`` else ``~/.local/state/renewsable/logs``.
    """
    return _xdg_state_home() / "renewsable" / "logs"


def systemd_user_unit_dir() -> Path:
    """Directory where the systemd user service + timer units are installed.

    ``$XDG_CONFIG_HOME/systemd/user`` else ``~/.config/systemd/user``. This
    is the standard per-user unit search path honoured by ``systemctl --user``.
    """
    return _xdg_config_home() / "systemd" / "user"


def rmapi_config_path() -> Path:
    """Path to the ``rmapi`` client's persisted device-token config file.

    Resolution order (mirrors what the ``rmapi`` binary itself does):

    1. ``$RMAPI_CONFIG``, if set to a non-empty value. ``rmapi`` honours this
       env var as an explicit path override on every platform.
    2. The platform-default location:

       * **Linux** (and unknown platforms): ``$XDG_CONFIG_HOME/rmapi/rmapi.conf``
         else ``~/.config/rmapi/rmapi.conf``.
       * **macOS**: ``$HOME/Library/Application Support/rmapi/rmapi.conf``.
         The ddvk fork of rmapi (Go) calls ``os.UserConfigDir()``, which
         resolves to ``Library/Application Support`` on Darwin — *not* the
         Linux-style XDG path. Surfaced during mac-manual-mode E2E
         verification when ``Pairing.is_paired()`` always returned False on
         macOS despite a successful ``rmapi`` pair (see ``research.md``
         addendum on this spec).

    Platform detection reads ``sys.platform`` via the module-level alias so
    tests can monkeypatch ``paths.sys.platform`` (the same seam pattern
    ``scheduler.py`` uses for its Darwin-refusal).

    This file is owned by the external ``rmapi`` binary; renewsable only
    *reads around* it (for "token present?" detection) and never writes it.
    """
    override = os.environ.get("RMAPI_CONFIG", "")
    if override:
        return Path(override)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "rmapi" / "rmapi.conf"
    return _xdg_config_home() / "rmapi" / "rmapi.conf"
