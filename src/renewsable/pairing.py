"""First-run reMarkable pairing helper.

Design reference: the "Pairing" component block in
``.kiro/specs/daily-paper/design.md``.

Requirements covered:
- 6.2 Pairing command prompts for the one-time 8-character code from
  ``my.remarkable.com/device/desktop/connect``, completes pairing, and
  persists the resulting device token in a documented location.
- 6.3 Subsequent runs proceed headless: when a token is already present,
  :meth:`Pairing.pair` short-circuits without re-spawning ``rmapi``.
- 8.5 The code and the token never appear in any log record this module
  emits. The redaction filter from :mod:`renewsable.logging_setup` is the
  primary defence; the logging discipline in this module is defence in
  depth (we simply never place secrets into log records to begin with).

Boundary
--------
``rmapi`` is the community-maintained reMarkable cloud client and is a
P0 external dependency for this component. All invocation goes through
the module-level ``subprocess`` alias so tests can replace
``subprocess.run`` in a single place
(``monkeypatch.setattr(pairing.subprocess, "run", fake_run)``) — the same
pattern :mod:`renewsable.scheduler` uses for ``systemctl``. No real
subprocess is spawned during unit tests.

Why we do not pipe ``rmapi``'s stdio
------------------------------------
``rmapi`` is an interactive CLI: it prints a prompt and reads the 8-char
one-time code from the user's terminal. We therefore invoke it with
``stdin=None, stdout=None, stderr=None`` so the child inherits the
parent's terminal — the user types the code directly into rmapi and
sees rmapi's prompts live. This has a security benefit on top: we never
have rmapi's stdout/stderr bytes in our process, which means we cannot
accidentally funnel the code or token into a log record.

Why we ignore rmapi's exit code
-------------------------------
rmapi's exit semantics are not stable across versions (and not relevant
to us). What matters is whether pairing actually persisted a token file.
We check ``is_paired()`` after the subprocess returns — presence of a
non-empty token file is authoritative.
"""

from __future__ import annotations

import logging
import subprocess  # noqa: F401  (module-level alias kept for tests)

from .config import Config
from .errors import PairingError
from .paths import rmapi_config_path


__all__ = ["Pairing"]


logger = logging.getLogger(__name__)


class Pairing:
    """First-run rmapi pairing wrapper.

    Parameters
    ----------
    config:
        The loaded :class:`renewsable.config.Config`. Only ``rmapi_bin``
        is consumed; the rmapi *config path* (where the token lives) is
        resolved via :func:`renewsable.paths.rmapi_config_path` — it is
        part of the rmapi client's contract, not ours, and therefore is
        not a knob in our config schema.
    """

    def __init__(self, config: Config) -> None:
        self._config = config

    def is_paired(self) -> bool:
        """Return True iff the rmapi config file exists and is non-empty.

        rmapi persists the device token inside ``rmapi.conf``; a missing
        or zero-byte file means no token, which in turn means we need to
        re-pair. We deliberately do not parse the file — its on-disk
        format is owned by rmapi — so "non-empty" is the strongest
        claim we can make portably across rmapi versions.
        """
        path = rmapi_config_path()
        try:
            return path.is_file() and path.stat().st_size > 0
        except OSError:
            # A transient stat() failure (e.g. permission glitch) is
            # indistinguishable from "not paired" for our purposes: the
            # caller will re-spawn rmapi, which will surface the real
            # problem to the user's terminal directly.
            return False

    def pair(self, force: bool = False) -> None:
        """Ensure the user has completed rmapi pairing.

        * If :meth:`is_paired` is already ``True`` and ``force`` is
          ``False``: return immediately. No subprocess. No log of any
          secret-bearing value.
        * Otherwise: spawn ``rmapi`` with inherited stdin/stdout/stderr
          so the user types the one-time code directly into the tool.
          After the subprocess exits, verify the token file is now
          present; raise :class:`PairingError` if it is not.

        Raises
        ------
        PairingError
            ``rmapi`` exited but no token file was persisted. The
            remediation text names the CLI command the user must re-run.
        """
        if self.is_paired() and not force:
            # Already paired → subsequent runs are headless (Req. 6.3).
            logger.debug("rmapi token already present; skipping pairing")
            return

        token_path = rmapi_config_path()
        logger.info("starting reMarkable pairing")
        # NOTE: We intentionally pass stdin/stdout/stderr=None so the
        # child inherits the parent terminal. We do NOT capture output;
        # rmapi's stdout may contain the one-time code and its config
        # write may echo the token, neither of which we want in memory.
        # ``check=False`` is deliberate: we ignore rmapi's exit code and
        # inspect the token file instead (see module docstring).
        try:
            self._run_rmapi(self._config.rmapi_bin)
        except FileNotFoundError as exc:
            # ``subprocess.run`` raises this when the binary cannot be
            # located on PATH. Surface a clean PairingError with a
            # remediation pointing at the config knob / install step.
            logger.error("rmapi binary not found: %s", self._config.rmapi_bin)
            raise PairingError(
                f"rmapi binary not found: {self._config.rmapi_bin}",
                remediation=(
                    "install rmapi (https://github.com/ddvk/rmapi) or set "
                    "'rmapi_bin' in your renewsable config to its absolute path"
                ),
            ) from exc

        if not self.is_paired():
            logger.error("pairing failed: rmapi exited without writing a token")
            raise PairingError(
                f"reMarkable pairing did not complete; no token at {token_path}",
                remediation=(
                    "Re-run `renewsable pair` and enter the one-time code from "
                    "https://my.remarkable.com/device/desktop/connect"
                ),
            )

        logger.info("pairing complete")

    # ------------------------------------------------------------------
    # Internal seam
    # ------------------------------------------------------------------

    @staticmethod
    def _run_rmapi(rmapi_bin: str) -> None:
        """Invoke ``rmapi`` interactively. Return value discarded.

        Kept as a one-line indirection so the call site stays readable
        and so tests can assert the exact kwargs we pass. ``check=False``
        suppresses the ``CalledProcessError`` branch; we rely on the
        post-spawn ``is_paired()`` check to determine success.
        """
        # Access through the module-level alias so monkeypatching
        # ``pairing.subprocess.run`` takes effect in tests.
        subprocess.run(
            [rmapi_bin],
            stdin=None,
            stdout=None,
            stderr=None,
            check=False,
        )
