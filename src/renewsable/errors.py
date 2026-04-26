"""Exception hierarchy for renewsable.

Design reference: "Error Handling" -> "Error Strategy" in
``.kiro/specs/daily-paper/design.md``.

A single base (:class:`RenewsableError`) with five orchestration subclasses.
Each exception carries a user-facing ``message`` plus an optional
``remediation`` hint. The CLI prints both on failure; ``str(exc)`` includes
both when ``remediation`` is set and just the message otherwise.
"""

from __future__ import annotations


class RenewsableError(Exception):
    """Base class for every error raised by the renewsable package.

    Parameters
    ----------
    message:
        User-facing description of the failure (the *what*).
    remediation:
        Optional hint describing how the user can recover (the *how*).
        Kept as a keyword-only argument so call sites stay explicit.
    """

    def __init__(self, message: str, *, remediation: str | None = None) -> None:
        self.message: str = message
        self.remediation: str | None = remediation
        # Pass a single composite arg to Exception so ``repr`` and default
        # traceback formatting remain useful even if a caller bypasses __str__.
        super().__init__(message)

    def __str__(self) -> str:  # pragma: no cover - exercised indirectly
        if self.remediation:
            return f"{self.message}\n  hint: {self.remediation}"
        return self.message


class ConfigError(RenewsableError):
    """Raised when the configuration file is missing, unreadable, or invalid."""


class BuildError(RenewsableError):
    """Raised when the Builder cannot produce today's EPUB."""


class UploadError(RenewsableError):
    """Raised when uploading the EPUB to the reMarkable cloud fails."""


class PairingError(RenewsableError):
    """Raised when first-run rmapi pairing fails to persist a token."""


class ScheduleError(RenewsableError):
    """Raised when installing or removing the systemd user units fails."""


__all__ = [
    "RenewsableError",
    "ConfigError",
    "BuildError",
    "UploadError",
    "PairingError",
    "ScheduleError",
]
