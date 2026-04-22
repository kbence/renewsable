"""Click-based CLI for renewsable.

Design reference: the "cli (summary-only)" section in
``.kiro/specs/daily-paper/design.md`` plus each orchestration component's
"Service Interface" block (Config, Builder, Uploader, Scheduler, Pairing).

Requirements covered:
- 1.1  Config read from the default path OR the explicit ``--config`` argument.
- 1.3  Missing config file -> ``ConfigError`` whose message names the expected
  path; CLI maps that to exit code 2.
- 4.1  ``run`` calls ``Uploader.upload`` after a successful ``Builder.build``.
- 4.5  On ``BuildError`` from ``run``, ``Uploader.upload`` is never invoked.
- 6.5  ``test-pipeline`` exercises the full build+upload pipeline on demand
  (verbose by default).
- 10.1 All required subcommands (``build``, ``upload``, ``run``,
  ``install-schedule``, ``uninstall-schedule``, ``pair``, ``test-pipeline``)
  are exposed.
- 10.2 ``--help`` prints a usage summary for every command.
- 10.3 ``upload PATH`` uploads an explicit file without rebuilding; bare
  ``upload`` uploads today's built PDF.
- 10.4 Success -> exit code 0.
- 10.5 Failure -> non-zero exit with a human-readable message on stderr.

Exit code contract
------------------
* ``0`` — the command completed successfully.
* ``1`` — any :class:`~renewsable.errors.RenewsableError` subclass that is
  not :class:`~renewsable.errors.ConfigError` (i.e. ``BuildError``,
  ``UploadError``, ``PairingError``, ``ScheduleError``).
* ``2`` — :class:`~renewsable.errors.ConfigError`. Kept distinct so shell
  scripts can tell "config is wrong, fix it" from "pipeline failed at
  runtime". Click itself also uses exit code 2 for bad CLI usage (unknown
  option, bad ``--choice`` value), which is consistent with "user input
  error" vs "runtime failure".

Why each command re-loads config
--------------------------------
Config is loaded once per invocation (a single shell run of ``renewsable
<cmd>``), but that load happens inside each command callback rather than
in the group. Doing it in the group would force every ``--help``
invocation to also read config; doing it per-command keeps ``renewsable
<cmd> --help`` fast and failure-free even when no config exists yet.

Component imports as module-level attributes
--------------------------------------------
``Builder``, ``Uploader``, ``Scheduler``, ``Pairing`` are deliberately
bound as module-level names in this file so tests can monkeypatch a
single attribute (``monkeypatch.setattr(cli, "Builder", FakeBuilder)``)
and have the CLI pick up the fake without any dependency injection
plumbing. Same pattern used elsewhere in the codebase for ``subprocess``
aliases.

``datetime`` module alias
-------------------------
The ``datetime`` module alias is kept at module level (not as a
``from datetime import date``) so tests can monkeypatch
``cli.datetime.date`` to pin "today" to a fixed value when testing the
default-upload-path branch.
"""

from __future__ import annotations

import datetime  # noqa: F401  (kept as module-level alias for tests)
import logging
import sys
from pathlib import Path
from typing import Callable

import click

from . import __version__
from . import paths as paths_mod
from .builder import Builder
from .config import Config
from .errors import ConfigError, RenewsableError
from .logging_setup import configure_logging
from .pairing import Pairing
from .scheduler import Scheduler
from .uploader import Uploader


logger = logging.getLogger(__name__)


__all__ = ["main"]


# ---------------------------------------------------------------------------
# Group
# ---------------------------------------------------------------------------


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(version=__version__, prog_name="renewsable")
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help=(
        "Path to the renewsable config JSON file. Defaults to "
        "$XDG_CONFIG_HOME/renewsable/config.json "
        "(or ~/.config/renewsable/config.json if XDG_CONFIG_HOME is unset)."
    ),
)
@click.option(
    "--log-level",
    type=click.Choice(
        ["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False
    ),
    default="INFO",
    show_default=True,
    help="Logging verbosity applied to the root logger.",
)
@click.pass_context
def main(
    ctx: click.Context, config_path: Path | None, log_level: str
) -> None:
    """renewsable — daily news digest for the reMarkable 2.

    Each subcommand loads the configuration (either from ``--config`` or
    the XDG default path), configures logging, instantiates the relevant
    component, and runs exactly one operation.
    """
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path
    ctx.obj["log_level"] = log_level.upper()


# ---------------------------------------------------------------------------
# Internal helpers used by every subcommand
# ---------------------------------------------------------------------------


def _bootstrap(ctx: click.Context) -> Config:
    """Load config + configure logging, or exit 2 with a ConfigError message.

    Every subcommand begins with this helper. It centralises three
    otherwise-repeated steps:

    1. Resolve ``--config`` against ``paths.default_config_path()``.
       ``Config.load`` requires a concrete path (see tasks.md
       Implementation Note: "Config.load(path) requires a non-None path"),
       so we never pass ``None`` through.
    2. Translate ``ConfigError`` into exit code 2 with the message on
       stderr (Req 1.3 / 10.5).
    3. Install the renewsable logging sinks at the requested level before
       any component runs, so every later log record — including the very
       first one emitted by Builder / Uploader / etc. — is captured and
       redacted.
    """
    cfg_path = ctx.obj["config_path"] or paths_mod.default_config_path()
    try:
        config = Config.load(cfg_path)
    except ConfigError as exc:
        click.echo(str(exc), err=True)
        ctx.exit(2)
        raise  # unreachable; ctx.exit raises SystemExit. Keeps mypy happy.

    # Honour the user's ``log_dir`` if they set it; otherwise fall back to
    # the XDG default so ``renewsable.log`` always lands somewhere sensible.
    log_dir = config.log_dir if config.log_dir is not None else paths_mod.default_log_dir()
    configure_logging(log_dir, level=ctx.obj["log_level"])
    return config


def _run_with_error_translation(
    ctx: click.Context, fn: Callable[[], None]
) -> None:
    """Invoke ``fn`` and translate :class:`RenewsableError` to exit codes.

    ``ConfigError`` raised from inside a command body (rare — typically
    config errors surface from :func:`_bootstrap` — but possible if a
    component re-validates lazily) maps to exit code 2 just like the
    bootstrap path. Every other :class:`RenewsableError` subclass maps to
    exit code 1 with the message written to stderr (Req 10.5). Non-
    Renewsable exceptions are left to propagate so Click's default
    tracebacks surface real bugs.
    """
    try:
        fn()
    except ConfigError as exc:
        click.echo(str(exc), err=True)
        ctx.exit(2)
    except RenewsableError as exc:
        click.echo(str(exc), err=True)
        ctx.exit(1)


def _todays_pdf_path(config: Config) -> Path:
    """Absolute path to ``<output_dir>/renewsable-<today>.pdf``.

    Uses the module-level ``datetime`` alias so tests can monkeypatch
    ``cli.datetime.date`` to pin "today" without mocking the stdlib
    globally.
    """
    today = datetime.date.today()
    return config.output_dir / f"renewsable-{today.isoformat()}.pdf"


def _exe_path() -> Path:
    """Absolute path to the currently-running ``renewsable`` executable.

    Used by the Scheduler to substitute ``$exe_path`` into the systemd
    service unit. ``sys.argv[0]`` is the entrypoint the shell invoked (the
    installed console-script wrapper in the user's venv under Pi usage;
    ``tests/`` paths or ``python -m renewsable`` paths under dev usage).
    ``.resolve()`` dereferences any symlinks so the unit file always
    points at the concrete binary.
    """
    return Path(sys.argv[0]).resolve()


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


@main.command()
@click.pass_context
def build(ctx: click.Context) -> None:
    """Build today's PDF from the configured feeds.

    Iterates over ``config.device_profiles`` and prints the absolute path
    to each produced file on stdout so scripts can pipe it into another
    command. If any profile fails, its error is logged and written to
    stderr and the command exits 1 after attempting every profile;
    otherwise exits 0.
    """
    config = _bootstrap(ctx)

    any_failed = False
    for profile in config.device_profiles:
        try:
            pdf_path = Builder(config).build(profile)
            click.echo(str(pdf_path))
        except RenewsableError as exc:
            any_failed = True
            logger.error("profile %s failed: %s", profile.name, exc)
            click.echo(str(exc), err=True)
    if any_failed:
        ctx.exit(1)


@main.command()
@click.argument(
    "path",
    type=click.Path(path_type=Path, dir_okay=False),
    required=False,
)
@click.pass_context
def upload(ctx: click.Context, path: Path | None) -> None:
    """Upload a PDF to the configured reMarkable folder.

    With no PATH argument, uploads today's built PDF
    (``<output_dir>/renewsable-<YYYY-MM-DD>.pdf``). With an explicit PATH,
    uploads that file without rebuilding (Req 10.3). Either way, the
    destination folder is taken from ``config.remarkable_folder``.
    """
    config = _bootstrap(ctx)

    target = path if path is not None else _todays_pdf_path(config)

    def _do() -> None:
        Uploader(config).upload(target)

    _run_with_error_translation(ctx, _do)


@main.command()
@click.pass_context
def run(ctx: click.Context) -> None:
    """Build today's PDF and then upload it, per configured profile.

    This is the command the scheduled systemd timer invokes. For each
    profile in ``config.device_profiles`` it sequences
    ``Builder.build(profile)`` followed by ``Uploader.upload(pdf,
    folder=...)``. A profile's upload step is skipped when its own build
    raised, but the next profile is still attempted (Req 6.3). The
    command exits 1 if any profile failed, 0 otherwise.
    """
    config = _bootstrap(ctx)

    any_failed = False
    for profile in config.device_profiles:
        try:
            pdf = Builder(config).build(profile)
            click.echo(str(pdf))
            folder = profile.remarkable_folder or config.remarkable_folder
            Uploader(config).upload(pdf, folder=folder)
        except RenewsableError as exc:
            any_failed = True
            logger.error("profile %s failed: %s", profile.name, exc)
            click.echo(str(exc), err=True)
    if any_failed:
        ctx.exit(1)


@main.command("install-schedule")
@click.pass_context
def install_schedule(ctx: click.Context) -> None:
    """Install the systemd user timer that runs the daily pipeline.

    Writes ``renewsable.service`` and ``renewsable.timer`` into the user's
    systemd unit directory, reloads the daemon, and enables the timer so
    it fires each day at ``config.schedule_time``. Idempotent: re-running
    after a schedule edit simply overwrites the unit files (Req 5.2).
    """
    config = _bootstrap(ctx)
    exe_path = _exe_path()

    def _do() -> None:
        Scheduler(config, exe_path).install()
        click.echo(f"installed schedule at {config.schedule_time}")

    _run_with_error_translation(ctx, _do)


@main.command("uninstall-schedule")
@click.pass_context
def uninstall_schedule(ctx: click.Context) -> None:
    """Remove the installed systemd user timer (Req 5.5).

    Disables and stops the timer, deletes the unit files, then reloads the
    daemon. Idempotent when no timer is currently installed.
    """
    config = _bootstrap(ctx)
    exe_path = _exe_path()

    def _do() -> None:
        Scheduler(config, exe_path).uninstall()
        click.echo("uninstalled schedule")

    _run_with_error_translation(ctx, _do)


@main.command()
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Re-pair even if a device token already exists.",
)
@click.pass_context
def pair(ctx: click.Context, force: bool) -> None:
    """Pair this device with the user's reMarkable cloud account.

    Spawns ``rmapi`` with an inherited terminal so the user can type the
    one-time 8-character code from
    ``my.remarkable.com/device/desktop/connect`` directly into the tool.
    A persisted token means subsequent ``renewsable run`` invocations
    proceed headlessly (Req 6.3). Pass ``--force`` to re-pair even when a
    token is already present.
    """
    config = _bootstrap(ctx)

    def _do() -> None:
        Pairing(config).pair(force=force)
        click.echo("pairing complete")

    _run_with_error_translation(ctx, _do)


@main.command("test-pipeline")
@click.pass_context
def test_pipeline(ctx: click.Context) -> None:
    """Run the full build+upload pipeline once, verbosely.

    Exists so the operator can confirm an end-to-end run on demand
    without waiting for the scheduled fire time (Req 6.5). Raises the
    effective log level to at least INFO so operational breadcrumbs
    (per-feed fetches, goosepaper invocation, rmapi calls) are visible
    even if the caller set ``--log-level WARNING``; leaves DEBUG alone if
    the caller asked for it explicitly.
    """
    # Nudge WARNING/ERROR up to INFO so the run is loud (Req 6.5). If the
    # caller already asked for DEBUG (even more verbose) we leave that
    # alone — "at least INFO" is the contract, not "exactly INFO".
    if ctx.obj["log_level"] in {"WARNING", "ERROR"}:
        ctx.obj["log_level"] = "INFO"
    config = _bootstrap(ctx)

    any_failed = False
    for profile in config.device_profiles:
        try:
            pdf = Builder(config).build(profile)
            click.echo(f"built {pdf}")
            folder = profile.remarkable_folder or config.remarkable_folder
            Uploader(config).upload(pdf, folder=folder)
            click.echo("uploaded successfully")
        except RenewsableError as exc:
            any_failed = True
            logger.error("profile %s failed: %s", profile.name, exc)
            click.echo(str(exc), err=True)
    if any_failed:
        ctx.exit(1)
