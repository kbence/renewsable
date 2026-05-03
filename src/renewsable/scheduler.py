"""Systemd user-unit scheduler for renewsable.

Design reference: the "Scheduler" component block in
``.kiro/specs/daily-paper/design.md`` — in particular the pinned
``renewsable.service`` / ``renewsable.timer`` templates and the
``Scheduler`` Service Interface.

Requirements covered:
- 5.1 Install scheduled job at configured time.
- 5.2 Reinstall with a new schedule time updates the active schedule
  (each ``install()`` overwrites the unit files and re-runs daemon-reload).
- 5.5 Uninstall command removes the installed schedule.
- 5.6 No concurrent runs — the rendered service unit uses ``Type=oneshot``,
  which relies on systemd's single-instance guarantee for user services.

Boundary
--------
All ``systemctl`` interaction is funnelled through one module-level
``subprocess`` alias so tests can replace ``subprocess.run`` in a single
spot (``monkeypatch.setattr(scheduler.subprocess, "run", fake_run)``).
No real subprocess is spawned during unit tests.

Templates
---------
``renewsable.service.tmpl`` and ``renewsable.timer.tmpl`` live in the
``renewsable.templates`` subpackage and are loaded via
:mod:`importlib.resources` so they ship with the installed wheel.
"""

from __future__ import annotations

import subprocess  # noqa: F401  (kept as module-level alias for tests)
import sys  # noqa: F401  (kept as module-level alias for tests)
from importlib.resources import files
from pathlib import Path
from string import Template

from .config import Config
from .errors import ScheduleError
from .paths import systemd_user_unit_dir


__all__ = ["Scheduler"]


# Unit names — kept as module-level constants so tests and downstream code
# (CLI status-printing) can reference them without hardcoding the strings.
_SERVICE_UNIT = "renewsable.service"
_TIMER_UNIT = "renewsable.timer"
_SERVICE_TEMPLATE = "renewsable.service.tmpl"
_TIMER_TEMPLATE = "renewsable.timer.tmpl"


def _load_template(name: str) -> Template:
    """Load a bundled ``.tmpl`` file as a :class:`string.Template`.

    Uses :mod:`importlib.resources` so the template ships with the
    installed wheel (see ``[tool.setuptools.package-data]`` in
    ``pyproject.toml``). Reading on every call keeps things simple — a
    tight install loop is not a hot path.
    """
    text = (files("renewsable.templates") / name).read_text(encoding="utf-8")
    return Template(text)


class Scheduler:
    """Install, uninstall, and report on the renewsable systemd user timer.

    Parameters
    ----------
    config:
        Loaded :class:`renewsable.config.Config`. Only ``schedule_time`` is
        consumed today; other fields are kept on the object for future
        extension (e.g. injecting env vars into the service unit).
    exe_path:
        Absolute path to the installed ``renewsable`` entrypoint (e.g.
        ``/home/pi/.venv/bin/renewsable``). Substituted into
        ``ExecStart=$exe_path run``.
    """

    def __init__(self, config: Config, exe_path: Path) -> None:
        self.config = config
        self.exe_path = exe_path

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def install(self) -> None:
        """Render + write unit files, then reload systemd and enable the timer.

        Idempotent: each call overwrites the unit files cleanly; a second
        ``daemon-reload`` + ``enable --now`` is a no-op on systemd.
        """
        unit_dir = systemd_user_unit_dir()
        unit_dir.mkdir(parents=True, exist_ok=True)

        service_text = _load_template(_SERVICE_TEMPLATE).substitute(
            exe_path=str(self.exe_path),
            home=str(Path.home()),
        )
        timer_text = _load_template(_TIMER_TEMPLATE).substitute(
            schedule_time=self.config.schedule_time,
        )

        (unit_dir / _SERVICE_UNIT).write_text(service_text, encoding="utf-8")
        (unit_dir / _TIMER_UNIT).write_text(timer_text, encoding="utf-8")

        self._run_systemctl(
            ["systemctl", "--user", "daemon-reload"],
            error_msg="systemctl --user daemon-reload failed",
        )
        self._run_systemctl(
            ["systemctl", "--user", "enable", "--now", _TIMER_UNIT],
            error_msg=f"systemctl --user enable --now {_TIMER_UNIT} failed",
        )

    def uninstall(self) -> None:
        """Disable + stop the timer, delete the unit files, reload systemd.

        Idempotent: ``systemctl disable --now`` on a unit that was never
        enabled exits non-zero with a "does not exist" stderr; we treat
        that as success. Missing unit files are tolerated via
        ``Path.unlink(missing_ok=True)``.
        """
        unit_dir = systemd_user_unit_dir()

        # disable --now — tolerate "unit does not exist" style failures.
        result = subprocess.run(
            ["systemctl", "--user", "disable", "--now", _TIMER_UNIT],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 and not _is_missing_unit_error(result.stderr):
            raise ScheduleError(
                f"systemctl --user disable --now {_TIMER_UNIT} failed: "
                f"{result.stderr.strip()}",
                remediation="inspect `systemctl --user status renewsable.timer`",
            )

        (unit_dir / _SERVICE_UNIT).unlink(missing_ok=True)
        (unit_dir / _TIMER_UNIT).unlink(missing_ok=True)

        self._run_systemctl(
            ["systemctl", "--user", "daemon-reload"],
            error_msg="systemctl --user daemon-reload failed",
        )

    def status(self) -> str:
        """Return a short string derived from ``systemctl --user list-timers``.

        Falls back to ``"no scheduled timer"`` when the command fails or
        produces no output (e.g. the timer is not installed).
        """
        result = subprocess.run(
            [
                "systemctl",
                "--user",
                "list-timers",
                _TIMER_UNIT,
                "--no-legend",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return "no scheduled timer"
        stdout = result.stdout.strip()
        if not stdout:
            return "no scheduled timer"
        return stdout

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _run_systemctl(argv: list[str], *, error_msg: str) -> None:
        """Run a systemctl command and raise :class:`ScheduleError` on failure.

        Every systemctl call in the scheduler goes through this helper so
        failure handling is uniform and the subprocess boundary is narrow.
        """
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise ScheduleError(
                f"{error_msg}: {result.stderr.strip()}",
                remediation=(
                    "run the failing command manually and inspect the full output; "
                    "confirm `loginctl enable-linger <user>` has been performed"
                ),
            )


def _is_missing_unit_error(stderr: str) -> bool:
    """Return True if ``stderr`` indicates the timer unit simply isn't installed.

    systemd's wording drifts a bit across versions, but all known variants
    contain both "renewsable.timer" (or "Unit file") and either "does not
    exist" or "No such file". We match conservatively so genuine failures
    (permission denied, dbus missing, etc.) still raise.
    """
    lowered = stderr.lower()
    missing_phrases = (
        "does not exist",
        "no such file",
        "not loaded",
    )
    return any(phrase in lowered for phrase in missing_phrases)
