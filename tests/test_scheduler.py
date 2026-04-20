"""Unit tests for :mod:`renewsable.scheduler`.

Design reference: "Scheduler" component block in
``.kiro/specs/daily-paper/design.md`` — including the EXACT unit file
templates pinned there.

Requirements covered:
- 5.1 Install scheduled job at configured time.
- 5.2 Reinstall with a new schedule time updates the active schedule.
- 5.5 Uninstall command removes the installed schedule.
- 5.6 No concurrent runs (``Type=oneshot`` in the rendered service unit).

Testability note
----------------
``systemctl`` does not exist on the macOS dev box. Every test MUST patch
``renewsable.scheduler.subprocess.run`` so no real subprocess is spawned.
The scheduler exposes ``subprocess`` as a module-level alias specifically
for this boundary.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from renewsable import scheduler as sched_mod
from renewsable.config import Config
from renewsable.errors import ScheduleError
from renewsable.scheduler import Scheduler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(tmp_path: Path, schedule_time: str = "05:30") -> Config:
    """Construct a valid Config by hand (no JSON round-trip)."""
    return Config(
        schedule_time=schedule_time,
        output_dir=tmp_path / "out",
        remarkable_folder="/News",
        stories=[{"provider": "rss", "config": {"rss_path": "https://x/y"}}],
    )


class _FakeRun:
    """Capturing replacement for ``subprocess.run``.

    Records every invocation's argv as a list. By default returns rc=0
    with empty stdout/stderr; ``preset`` lets individual tests force
    specific return values by command key (first three argv tokens).
    """

    def __init__(
        self,
        preset: dict[tuple[str, ...], tuple[int, str, str]] | None = None,
    ) -> None:
        self.calls: list[list[str]] = []
        self.preset = preset or {}

    def __call__(self, argv: list[str], **kwargs: Any) -> Any:
        # Record the argv verbatim.
        self.calls.append(list(argv))
        # The scheduler must always pass capture_output=True and text=True.
        assert kwargs.get("capture_output") is True, kwargs
        assert kwargs.get("text") is True, kwargs
        # ``check=False`` is required — the scheduler inspects returncode itself.
        assert kwargs.get("check") is False, kwargs
        # Look up by a normalised key: ("systemctl", "--user", <action>)
        key = tuple(argv[:3])
        rc, out, err = self.preset.get(key, (0, "", ""))
        return SimpleNamespace(returncode=rc, stdout=out, stderr=err)


# ---------------------------------------------------------------------------
# 1. Template render golden
# ---------------------------------------------------------------------------


def test_service_template_renders_exactly(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    fake = _FakeRun()
    monkeypatch.setattr(sched_mod.subprocess, "run", fake)

    cfg = _make_config(tmp_path, schedule_time="05:30")
    s = Scheduler(cfg, exe_path=Path("/usr/local/bin/renewsable"))
    s.install()

    service_text = (tmp_path / "cfg" / "systemd" / "user" / "renewsable.service").read_text()
    expected = (
        "[Unit]\n"
        "Description=renewsable daily paper\n"
        "After=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        "ExecStart=/usr/local/bin/renewsable run\n"
        f"Environment=HOME={Path.home()}\n"
    )
    assert service_text == expected


def test_timer_template_renders_exactly(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    fake = _FakeRun()
    monkeypatch.setattr(sched_mod.subprocess, "run", fake)

    cfg = _make_config(tmp_path, schedule_time="05:30")
    s = Scheduler(cfg, exe_path=Path("/usr/local/bin/renewsable"))
    s.install()

    timer_text = (tmp_path / "cfg" / "systemd" / "user" / "renewsable.timer").read_text()
    expected = (
        "[Unit]\n"
        "Description=renewsable daily timer\n"
        "\n"
        "[Timer]\n"
        "OnCalendar=*-*-* 05:30:00\n"
        "Persistent=true\n"
        "Unit=renewsable.service\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )
    assert timer_text == expected


# ---------------------------------------------------------------------------
# 2. Install against a temp XDG — files exist + systemctl called
# ---------------------------------------------------------------------------


def test_install_writes_units_and_calls_systemctl(tmp_path, monkeypatch):
    xdg = tmp_path / "cfg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    fake = _FakeRun()
    monkeypatch.setattr(sched_mod.subprocess, "run", fake)

    cfg = _make_config(tmp_path, schedule_time="07:00")
    exe = Path("/opt/renewsable/bin/renewsable")
    Scheduler(cfg, exe_path=exe).install()

    svc = xdg / "systemd" / "user" / "renewsable.service"
    tim = xdg / "systemd" / "user" / "renewsable.timer"
    assert svc.is_file()
    assert tim.is_file()
    assert "ExecStart=/opt/renewsable/bin/renewsable run" in svc.read_text()
    assert "OnCalendar=*-*-* 07:00:00" in tim.read_text()

    # Two systemctl calls, in order: daemon-reload, then enable --now.
    assert fake.calls == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", "renewsable.timer"],
    ]


# ---------------------------------------------------------------------------
# 3. Install idempotent
# ---------------------------------------------------------------------------


def test_install_is_idempotent(tmp_path, monkeypatch):
    xdg = tmp_path / "cfg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    fake = _FakeRun()
    monkeypatch.setattr(sched_mod.subprocess, "run", fake)

    cfg = _make_config(tmp_path, schedule_time="05:30")
    exe = Path("/usr/local/bin/renewsable")
    s = Scheduler(cfg, exe_path=exe)
    s.install()
    s.install()  # must not raise

    svc = xdg / "systemd" / "user" / "renewsable.service"
    tim = xdg / "systemd" / "user" / "renewsable.timer"
    assert svc.is_file()
    assert tim.is_file()
    # Both calls should have happened twice (2x daemon-reload + 2x enable).
    assert fake.calls.count(["systemctl", "--user", "daemon-reload"]) == 2
    assert fake.calls.count(
        ["systemctl", "--user", "enable", "--now", "renewsable.timer"]
    ) == 2


# ---------------------------------------------------------------------------
# 4. Uninstall removes files
# ---------------------------------------------------------------------------


def test_uninstall_removes_unit_files(tmp_path, monkeypatch):
    xdg = tmp_path / "cfg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    fake = _FakeRun()
    monkeypatch.setattr(sched_mod.subprocess, "run", fake)

    cfg = _make_config(tmp_path)
    s = Scheduler(cfg, exe_path=Path("/usr/local/bin/renewsable"))
    s.install()

    svc = xdg / "systemd" / "user" / "renewsable.service"
    tim = xdg / "systemd" / "user" / "renewsable.timer"
    assert svc.is_file() and tim.is_file()

    s.uninstall()

    assert not svc.exists()
    assert not tim.exists()
    # Last subprocess calls include disable + daemon-reload.
    assert ["systemctl", "--user", "disable", "--now", "renewsable.timer"] in fake.calls
    # daemon-reload should appear at least twice (install + uninstall).
    assert fake.calls.count(["systemctl", "--user", "daemon-reload"]) >= 2


# ---------------------------------------------------------------------------
# 5. Uninstall idempotent when nothing is installed
# ---------------------------------------------------------------------------


def test_uninstall_is_idempotent_when_not_installed(tmp_path, monkeypatch):
    xdg = tmp_path / "cfg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    # systemctl disable on a non-existent unit exits non-zero; scheduler
    # must tolerate that so uninstall is idempotent.
    fake = _FakeRun(
        preset={
            ("systemctl", "--user", "disable"): (
                1,
                "",
                "Failed to disable unit: Unit file renewsable.timer does not exist.\n",
            ),
        }
    )
    monkeypatch.setattr(sched_mod.subprocess, "run", fake)

    cfg = _make_config(tmp_path)
    s = Scheduler(cfg, exe_path=Path("/usr/local/bin/renewsable"))
    # Must not raise even though nothing is installed.
    s.uninstall()

    # No unit files exist, no exception.
    assert not (xdg / "systemd" / "user" / "renewsable.service").exists()
    assert not (xdg / "systemd" / "user" / "renewsable.timer").exists()


# ---------------------------------------------------------------------------
# 6. Schedule-time substitution
# ---------------------------------------------------------------------------


def test_schedule_time_is_substituted_into_timer(tmp_path, monkeypatch):
    xdg = tmp_path / "cfg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setattr(sched_mod.subprocess, "run", _FakeRun())

    cfg = _make_config(tmp_path, schedule_time="06:30")
    Scheduler(cfg, exe_path=Path("/usr/local/bin/renewsable")).install()

    timer_text = (xdg / "systemd" / "user" / "renewsable.timer").read_text()
    assert "OnCalendar=*-*-* 06:30:00" in timer_text


# ---------------------------------------------------------------------------
# 7. status() parses list-timers output
# ---------------------------------------------------------------------------


def test_status_returns_list_timers_output(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    sample_stdout = (
        "Wed 2026-04-22 05:30:00 CEST 12h left Tue 2026-04-21 05:30:00 CEST 11h ago "
        "renewsable.timer renewsable.service\n"
    )
    fake = _FakeRun(
        preset={("systemctl", "--user", "list-timers"): (0, sample_stdout, "")}
    )
    monkeypatch.setattr(sched_mod.subprocess, "run", fake)

    cfg = _make_config(tmp_path)
    result = Scheduler(cfg, exe_path=Path("/usr/local/bin/renewsable")).status()

    assert "renewsable.timer" in result
    # The scheduler should have invoked list-timers with the expected argv.
    assert fake.calls == [
        [
            "systemctl",
            "--user",
            "list-timers",
            "renewsable.timer",
            "--no-legend",
        ]
    ]


def test_status_returns_fallback_when_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    fake = _FakeRun(
        preset={("systemctl", "--user", "list-timers"): (0, "", "")}
    )
    monkeypatch.setattr(sched_mod.subprocess, "run", fake)

    cfg = _make_config(tmp_path)
    result = Scheduler(cfg, exe_path=Path("/usr/local/bin/renewsable")).status()
    assert result == "no scheduled timer"


# ---------------------------------------------------------------------------
# 8. subprocess failure raises ScheduleError
# ---------------------------------------------------------------------------


def test_install_raises_schedule_error_on_systemctl_failure(tmp_path, monkeypatch):
    xdg = tmp_path / "cfg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    fake = _FakeRun(
        preset={
            ("systemctl", "--user", "daemon-reload"): (
                1,
                "",
                "Failed to reload daemon: Permission denied\n",
            ),
        }
    )
    monkeypatch.setattr(sched_mod.subprocess, "run", fake)

    cfg = _make_config(tmp_path)
    s = Scheduler(cfg, exe_path=Path("/usr/local/bin/renewsable"))
    with pytest.raises(ScheduleError) as excinfo:
        s.install()
    assert "Permission denied" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 9. Re-install with new schedule_time overwrites (Req 5.2)
# ---------------------------------------------------------------------------


def test_reinstall_with_new_time_overwrites_timer(tmp_path, monkeypatch):
    xdg = tmp_path / "cfg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setattr(sched_mod.subprocess, "run", _FakeRun())

    cfg1 = _make_config(tmp_path, schedule_time="05:30")
    Scheduler(cfg1, exe_path=Path("/usr/local/bin/renewsable")).install()
    timer = xdg / "systemd" / "user" / "renewsable.timer"
    assert "OnCalendar=*-*-* 05:30:00" in timer.read_text()

    cfg2 = replace(cfg1, schedule_time="09:45")
    Scheduler(cfg2, exe_path=Path("/usr/local/bin/renewsable")).install()
    assert "OnCalendar=*-*-* 09:45:00" in timer.read_text()
    assert "05:30" not in timer.read_text()
