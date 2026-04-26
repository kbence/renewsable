"""Unit tests for :mod:`renewsable.cli`.

Design reference: the "cli (modified)" subsection of
``.kiro/specs/epub-output/design.md`` plus each orchestration component's
"Service Interface" block (Config, Builder, Uploader, Scheduler, Pairing).

Requirements covered:
- 1.1  Config read from default path or explicit ``--config``.
- 1.3  Missing config -> exit-2 error that names the expected path.
- 4.1  ``run`` uploads the built EPUB after a successful build.
- 4.5  No upload if the preceding build failed (short-circuit on ``BuildError``).
- 6.5  ``test-pipeline`` command exercises build+upload on demand.
- 7.1  Build produces exactly one EPUB per run; no per-profile loop.
- 8.1  ``run`` uploads after a successful build, with
       ``folder=config.remarkable_folder``.
- 10.1 All required subcommands exist.
- 10.2 Each command has ``--help`` printing a usage summary.
- 10.3 ``upload`` with an explicit path skips Builder and uploads that file.
- 10.4 Successful commands exit 0.
- 10.5 Failures exit non-zero with a human-readable error on stderr.

Testability note
----------------
Every component is monkeypatched by name in the :mod:`renewsable.cli`
module namespace (``cli_mod.Builder``, etc.) so the tests never spawn a
real subprocess, open a network socket, or touch the real
``~/.config/renewsable/config.json``.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from renewsable import cli as cli_mod
from renewsable.cli import main
from renewsable.errors import (
    BuildError,
    ConfigError,
    PairingError,
    ScheduleError,
    UploadError,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures"
VALID_CONFIG = FIXTURE_DIR / "config.valid.json"


# ---------------------------------------------------------------------------
# Helpers: fake components the CLI will instantiate
# ---------------------------------------------------------------------------


class _Recorder:
    """Accumulates calls made to fake-component methods."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def record(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((name, args, kwargs))

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]


@pytest.fixture()
def recorder() -> _Recorder:
    return _Recorder()


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    rec: _Recorder,
    *,
    build_result: Path | None = None,
    build_raises: Exception | None = None,
    upload_raises: Exception | None = None,
    install_raises: Exception | None = None,
    uninstall_raises: Exception | None = None,
    pair_raises: Exception | None = None,
) -> None:
    """Swap every component the CLI uses for a recorder-backed fake.

    Any ``*_raises`` kwarg triggers the named method to raise that
    exception instead of recording success. This is the lever for every
    error-path test in this module.
    """

    class FakeBuilder:
        def __init__(self, config: Any) -> None:
            rec.record("Builder.__init__", config)

        def build(self, today: Any = None) -> Path:
            rec.record("Builder.build", today=today)
            if build_raises is not None:
                raise build_raises
            assert build_result is not None, "test must provide build_result"
            return build_result

    class FakeUploader:
        def __init__(self, config: Any) -> None:
            rec.record("Uploader.__init__", config)

        def upload(self, epub: Path, folder: str | None = None) -> None:
            rec.record("Uploader.upload", epub, folder=folder)
            if upload_raises is not None:
                raise upload_raises

    class FakeScheduler:
        def __init__(self, config: Any, exe_path: Path) -> None:
            rec.record("Scheduler.__init__", config, exe_path=exe_path)

        def install(self) -> None:
            rec.record("Scheduler.install")
            if install_raises is not None:
                raise install_raises

        def uninstall(self) -> None:
            rec.record("Scheduler.uninstall")
            if uninstall_raises is not None:
                raise uninstall_raises

    class FakePairing:
        def __init__(self, config: Any) -> None:
            rec.record("Pairing.__init__", config)

        def pair(self, force: bool = False) -> None:
            rec.record("Pairing.pair", force=force)
            if pair_raises is not None:
                raise pair_raises

    monkeypatch.setattr(cli_mod, "Builder", FakeBuilder)
    monkeypatch.setattr(cli_mod, "Uploader", FakeUploader)
    monkeypatch.setattr(cli_mod, "Scheduler", FakeScheduler)
    monkeypatch.setattr(cli_mod, "Pairing", FakePairing)


@pytest.fixture()
def isolated_xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect XDG dirs into ``tmp_path`` so no test writes into the user's home."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return tmp_path


@pytest.fixture()
def runner() -> CliRunner:
    # Click 8.2+ returns stderr separately on ``Result.stderr`` by default;
    # the legacy ``mix_stderr`` kwarg was removed in 8.2.
    return CliRunner()


# ---------------------------------------------------------------------------
# --help surface (Req 10.1, 10.2)
# ---------------------------------------------------------------------------


def test_top_level_help_lists_all_subcommands(runner: CliRunner) -> None:
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0, result.output
    expected = [
        "build",
        "upload",
        "run",
        "install-schedule",
        "uninstall-schedule",
        "pair",
        "test-pipeline",
    ]
    for name in expected:
        assert name in result.output, f"missing subcommand {name!r} in --help"


@pytest.mark.parametrize(
    "subcmd",
    [
        "build",
        "upload",
        "run",
        "install-schedule",
        "uninstall-schedule",
        "pair",
        "test-pipeline",
    ],
)
def test_each_subcommand_has_help(runner: CliRunner, subcmd: str) -> None:
    result = runner.invoke(main, [subcmd, "--help"])
    assert result.exit_code == 0, result.output
    # Click always emits a "Usage:" line at the top of --help output.
    assert "Usage:" in result.output


def test_version_option(runner: CliRunner) -> None:
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    # Version option prints the version — anything non-empty is fine.
    assert result.output.strip() != ""


# ---------------------------------------------------------------------------
# Global --config: missing file -> exit 2 (Req 1.3)
# ---------------------------------------------------------------------------


def test_missing_config_exits_2_and_names_path(
    runner: CliRunner, tmp_path: Path, isolated_xdg: Path
) -> None:
    bogus = tmp_path / "does" / "not" / "exist.json"
    result = runner.invoke(main, ["--config", str(bogus), "build"])
    assert result.exit_code == 2, (result.output, result.stderr)
    # The ConfigError message from config.load includes the path verbatim.
    assert str(bogus) in result.stderr


def test_missing_default_config_exits_2(
    runner: CliRunner, isolated_xdg: Path
) -> None:
    # No --config passed; default resolves inside the isolated XDG dir,
    # which does not contain a config file.
    result = runner.invoke(main, ["build"])
    assert result.exit_code == 2
    assert "config" in result.stderr.lower()


# ---------------------------------------------------------------------------
# build command (Req 7.1, 10.4, 10.5)
# ---------------------------------------------------------------------------


def test_build_invokes_builder_exactly_once_and_prints_path(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    recorder: _Recorder,
    isolated_xdg: Path,
    tmp_path: Path,
) -> None:
    produced = tmp_path / "renewsable-2026-04-19.epub"
    _install_fakes(monkeypatch, recorder, build_result=produced)

    result = runner.invoke(main, ["--config", str(VALID_CONFIG), "build"])

    assert result.exit_code == 0, (result.output, result.stderr)
    # Builder.build called exactly once — Req 7.1, no per-profile loop.
    build_calls = [c for c in recorder.calls if c[0] == "Builder.build"]
    assert len(build_calls) == 1
    # The built path should be echoed so shell scripts can pick it up.
    assert str(produced) in result.output
    # build does not invoke Uploader.
    assert "Uploader.upload" not in recorder.names()


def test_build_build_error_exits_1_with_stderr(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    recorder: _Recorder,
    isolated_xdg: Path,
) -> None:
    _install_fakes(
        monkeypatch,
        recorder,
        build_raises=BuildError("no feeds produced content"),
    )
    result = runner.invoke(main, ["--config", str(VALID_CONFIG), "build"])
    assert result.exit_code == 1
    assert "no feeds produced content" in result.stderr


def test_build_config_error_exits_2(
    runner: CliRunner, tmp_path: Path, isolated_xdg: Path
) -> None:
    """A missing config file produces exit code 2, distinct from BuildError's 1."""
    bogus = tmp_path / "missing.json"
    result = runner.invoke(main, ["--config", str(bogus), "build"])
    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# upload command (Req 10.3)
# ---------------------------------------------------------------------------


def test_upload_with_explicit_path_skips_builder(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    recorder: _Recorder,
    isolated_xdg: Path,
    tmp_path: Path,
) -> None:
    explicit = tmp_path / "arbitrary.epub"
    _install_fakes(monkeypatch, recorder)
    result = runner.invoke(
        main, ["--config", str(VALID_CONFIG), "upload", str(explicit)]
    )
    assert result.exit_code == 0, (result.output, result.stderr)
    assert "Builder.build" not in recorder.names()
    # Upload was called with the explicit path.
    upload_calls = [c for c in recorder.calls if c[0] == "Uploader.upload"]
    assert len(upload_calls) == 1
    assert Path(str(upload_calls[0][1][0])) == explicit


def test_upload_without_path_uses_todays_epub(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    recorder: _Recorder,
    isolated_xdg: Path,
    tmp_path: Path,
) -> None:
    _install_fakes(monkeypatch, recorder)
    # Pin "today" so the test is not date-dependent.
    fixed_today = _dt.date(2026, 4, 19)

    class _FixedDate(_dt.date):
        @classmethod
        def today(cls) -> "_dt.date":  # type: ignore[override]
            return fixed_today

    monkeypatch.setattr(cli_mod.datetime, "date", _FixedDate)

    result = runner.invoke(main, ["--config", str(VALID_CONFIG), "upload"])
    assert result.exit_code == 0, (result.output, result.stderr)
    upload_calls = [c for c in recorder.calls if c[0] == "Uploader.upload"]
    assert len(upload_calls) == 1
    # Filename follows the renewsable-<YYYY-MM-DD>.epub contract (Req 7.2).
    uploaded_path = Path(str(upload_calls[0][1][0]))
    assert uploaded_path.suffix == ".epub"
    assert uploaded_path.name == "renewsable-2026-04-19.epub"
    # Parent directory is the configured output_dir (from the valid fixture).
    assert uploaded_path.parent.name == "out"


def test_upload_upload_error_exits_1_with_stderr(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    recorder: _Recorder,
    isolated_xdg: Path,
    tmp_path: Path,
) -> None:
    _install_fakes(
        monkeypatch,
        recorder,
        upload_raises=UploadError(
            "rmapi put failed", remediation="run `renewsable pair`"
        ),
    )
    explicit = tmp_path / "x.epub"
    result = runner.invoke(
        main, ["--config", str(VALID_CONFIG), "upload", str(explicit)]
    )
    assert result.exit_code == 1
    assert "rmapi put failed" in result.stderr


# ---------------------------------------------------------------------------
# run command: sequencing + short-circuit (Req 4.1, 4.5, 8.1)
# ---------------------------------------------------------------------------


def test_run_sequences_build_then_upload_with_configured_folder(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    recorder: _Recorder,
    isolated_xdg: Path,
    tmp_path: Path,
) -> None:
    produced = tmp_path / "renewsable-2026-04-19.epub"
    _install_fakes(monkeypatch, recorder, build_result=produced)

    result = runner.invoke(main, ["--config", str(VALID_CONFIG), "run"])
    assert result.exit_code == 0, (result.output, result.stderr)

    # Builder.build called exactly once (Req 7.1) and Uploader.upload once.
    build_calls = [c for c in recorder.calls if c[0] == "Builder.build"]
    upload_calls = [c for c in recorder.calls if c[0] == "Uploader.upload"]
    assert len(build_calls) == 1
    assert len(upload_calls) == 1

    # Order matters: Builder.build must precede Uploader.upload.
    names = recorder.names()
    assert names.index("Builder.build") < names.index("Uploader.upload")

    # The upload target is the path Builder returned, and the folder is
    # config.remarkable_folder (Req 8.1). The valid fixture sets it to "/News".
    assert Path(str(upload_calls[0][1][0])) == produced
    assert upload_calls[0][2]["folder"] == "/News"


def test_run_short_circuits_on_build_error(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    recorder: _Recorder,
    isolated_xdg: Path,
) -> None:
    _install_fakes(
        monkeypatch,
        recorder,
        build_raises=BuildError("every feed failed"),
    )
    result = runner.invoke(main, ["--config", str(VALID_CONFIG), "run"])
    assert result.exit_code == 1
    assert "every feed failed" in result.stderr
    # Uploader.upload must NOT have been called (Req 4.5).
    assert "Uploader.upload" not in recorder.names()


# ---------------------------------------------------------------------------
# install-schedule / uninstall-schedule
# ---------------------------------------------------------------------------


def test_install_schedule_calls_scheduler(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    recorder: _Recorder,
    isolated_xdg: Path,
) -> None:
    _install_fakes(monkeypatch, recorder)
    result = runner.invoke(
        main, ["--config", str(VALID_CONFIG), "install-schedule"]
    )
    assert result.exit_code == 0, (result.output, result.stderr)
    assert "Scheduler.install" in recorder.names()


def test_uninstall_schedule_calls_scheduler(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    recorder: _Recorder,
    isolated_xdg: Path,
) -> None:
    _install_fakes(monkeypatch, recorder)
    result = runner.invoke(
        main, ["--config", str(VALID_CONFIG), "uninstall-schedule"]
    )
    assert result.exit_code == 0, (result.output, result.stderr)
    assert "Scheduler.uninstall" in recorder.names()


def test_install_schedule_schedule_error_exits_1(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    recorder: _Recorder,
    isolated_xdg: Path,
) -> None:
    _install_fakes(
        monkeypatch,
        recorder,
        install_raises=ScheduleError("systemctl failed"),
    )
    result = runner.invoke(
        main, ["--config", str(VALID_CONFIG), "install-schedule"]
    )
    assert result.exit_code == 1
    assert "systemctl failed" in result.stderr


# ---------------------------------------------------------------------------
# pair command (--force)
# ---------------------------------------------------------------------------


def test_pair_passes_force_false_by_default(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    recorder: _Recorder,
    isolated_xdg: Path,
) -> None:
    _install_fakes(monkeypatch, recorder)
    result = runner.invoke(main, ["--config", str(VALID_CONFIG), "pair"])
    assert result.exit_code == 0, (result.output, result.stderr)
    pair_calls = [c for c in recorder.calls if c[0] == "Pairing.pair"]
    assert len(pair_calls) == 1
    assert pair_calls[0][2]["force"] is False


def test_pair_with_force_flag(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    recorder: _Recorder,
    isolated_xdg: Path,
) -> None:
    _install_fakes(monkeypatch, recorder)
    result = runner.invoke(
        main, ["--config", str(VALID_CONFIG), "pair", "--force"]
    )
    assert result.exit_code == 0, (result.output, result.stderr)
    pair_calls = [c for c in recorder.calls if c[0] == "Pairing.pair"]
    assert pair_calls[0][2]["force"] is True


def test_pair_pairing_error_exits_1(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    recorder: _Recorder,
    isolated_xdg: Path,
) -> None:
    _install_fakes(
        monkeypatch,
        recorder,
        pair_raises=PairingError("no token written"),
    )
    result = runner.invoke(main, ["--config", str(VALID_CONFIG), "pair"])
    assert result.exit_code == 1
    assert "no token written" in result.stderr


# ---------------------------------------------------------------------------
# test-pipeline command (Req 6.5, 7.1, 8.1)
# ---------------------------------------------------------------------------


def test_test_pipeline_runs_build_then_upload_once(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    recorder: _Recorder,
    isolated_xdg: Path,
    tmp_path: Path,
) -> None:
    produced = tmp_path / "renewsable-2026-04-19.epub"
    _install_fakes(monkeypatch, recorder, build_result=produced)
    result = runner.invoke(
        main, ["--config", str(VALID_CONFIG), "test-pipeline"]
    )
    assert result.exit_code == 0, (result.output, result.stderr)
    build_calls = [c for c in recorder.calls if c[0] == "Builder.build"]
    upload_calls = [c for c in recorder.calls if c[0] == "Uploader.upload"]
    assert len(build_calls) == 1
    assert len(upload_calls) == 1
    names = recorder.names()
    assert names.index("Builder.build") < names.index("Uploader.upload")
    # The upload target is the path Builder returned, folder from config.
    assert Path(str(upload_calls[0][1][0])) == produced
    assert upload_calls[0][2]["folder"] == "/News"
    # Verbose breadcrumbs visible on stdout.
    assert f"built {produced}" in result.output
    assert "uploaded successfully" in result.output


# ---------------------------------------------------------------------------
# Defensive: invalid log level rejected by Click
# ---------------------------------------------------------------------------


def test_invalid_log_level_rejected(
    runner: CliRunner, isolated_xdg: Path
) -> None:
    result = runner.invoke(
        main,
        ["--log-level", "CHATTY", "--config", str(VALID_CONFIG), "build"],
    )
    # Click rejects bad --choice values with exit code 2 before the command runs.
    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# Defensive: ConfigError raised from a later phase (e.g. invalid JSON)
# is translated to exit code 2, not 1.
# ---------------------------------------------------------------------------


def test_invalid_json_config_exits_2(
    runner: CliRunner, tmp_path: Path, isolated_xdg: Path
) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("this is not json", encoding="utf-8")
    result = runner.invoke(main, ["--config", str(bad), "build"])
    assert result.exit_code == 2
    assert str(bad) in result.stderr
