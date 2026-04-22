"""Configuration loader and validator for renewsable.

Design reference: the "Config" component block and "Data Models" persistent-
file table in ``.kiro/specs/daily-paper/design.md``.

Requirements covered:
- 1.1 Settings live in one human-editable JSON file at a documented default
  path or an explicit ``--config`` path.
- 1.2 The file may set: list of news sources (``stories``), reMarkable folder,
  schedule time, output directory.
- 1.3 Missing file -> ``ConfigError`` naming the expected path.
- 1.4 Present-but-malformed field -> ``ConfigError`` naming the field and the
  problem.
- 1.5 Edits take effect on the next invocation (every ``Config.load`` is a
  fresh read; no module-level caching).
- 1.6 Documented defaults for every optional field.

Shape
-----
``Config`` is a ``frozen=True`` dataclass — once loaded it is a value object
that callers can pass around without fear of mutation. Every field has a
default at the dataclass level so the dataclass itself is constructible
without arguments; the *real* invariants ("``stories`` non-empty",
"``schedule_time`` parses as a clock") are enforced by ``validate()``, which
``load()`` invokes before returning. This keeps the type-level contract and
the runtime contract cleanly separated.

Closed-set top-level keys
-------------------------
``Config.load`` rejects any unknown top-level JSON key. This catches typos
("``stoires``", "``schedule_tlme``") that would otherwise silently fall back
to a default and waste an entire scheduled run debugging "why didn't my new
feed appear".
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import re
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from . import paths
from .errors import ConfigError
from .profiles import BUILTIN_PROFILES, DeviceProfile, resolve as _resolve_profile


__all__ = ["Config"]


logger = logging.getLogger(__name__)


# Input-only keys: accepted in the JSON payload but normalised into
# ``device_profiles`` before the dataclass is constructed. They are not
# dataclass fields and never appear on the resulting ``Config`` object.
_PROFILE_INPUT_KEYS: frozenset[str] = frozenset({"device_profile", "device_profiles"})


# ``HH:MM`` 24-hour clock. The pattern is intentionally strict (two digits
# each, colon separator) — design.md "Config" -> "Postconditions" pins the
# regex to ``^\d{2}:\d{2}$``. ``datetime.time.fromisoformat`` provides the
# range check (00-23 / 00-59) so we do not duplicate it here.
_SCHEDULE_TIME_RE = re.compile(r"^\d{2}:\d{2}$")


# Fields whose values are paths and therefore need ``~`` expansion plus
# ``resolve()`` to an absolute path before landing in the dataclass.
_PATH_FIELDS: frozenset[str] = frozenset({"output_dir", "log_dir"})


@dataclass(frozen=True)
class Config:
    """Immutable runtime settings, loaded from a single JSON file.

    Field order, names, types, and defaults match the "Config" component
    block in design.md. Every field has a default so the dataclass is
    constructible without arguments; ``Config.load`` then overlays the JSON
    payload on top and calls ``validate()`` to enforce the real invariants.
    """

    schedule_time: str = "05:30"
    # Defaults computed at load time (see ``_apply_defaults``) so XDG env
    # changes between import and load are honoured. The sentinel here is a
    # placeholder ``Path()`` that ``load`` replaces; ``validate`` enforces
    # absoluteness so the placeholder is never a valid live config.
    output_dir: Path = field(default_factory=lambda: Path())
    remarkable_folder: str = "/News"
    stories: list[dict[str, Any]] = field(default_factory=list)
    font_size: int | None = None
    log_dir: Path | None = None
    user_agent: str = "renewsable/0.1 (+https://github.com/bnc/renewsable)"
    goosepaper_bin: str = "goosepaper"
    rmapi_bin: str = "rmapi"
    feed_fetch_retries: int = 3
    feed_fetch_backoff_s: float = 1.0
    upload_retries: int = 3
    upload_backoff_s: float = 2.0
    subprocess_timeout_s: int = 180
    device_profiles: list[DeviceProfile] = field(
        default_factory=lambda: [BUILTIN_PROFILES["rm2"]]
    )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, path: Path | str) -> "Config":
        """Load + validate a config from ``path``.

        Returns a populated, frozen ``Config``. Raises ``ConfigError`` (with
        the file path embedded in the message) when the file is missing,
        contains invalid JSON, has an unknown top-level key, has a known
        key with the wrong type, or fails any semantic invariant.
        """
        cfg_path = Path(path)

        # ---- Phase 1: read the file ----
        if not cfg_path.exists():
            raise ConfigError(
                f"config file not found: {cfg_path}",
                remediation="create this file or pass --config <path>",
            )
        try:
            raw_text = cfg_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigError(
                f"cannot read config file {cfg_path}: {exc}",
                remediation="check file permissions",
            ) from exc

        # ---- Phase 2: parse JSON ----
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise ConfigError(
                f"invalid JSON in {cfg_path}: {exc.msg} (line {exc.lineno}, col {exc.colno})",
                remediation="run the file through a JSON linter",
            ) from exc
        if not isinstance(data, dict):
            raise ConfigError(
                f"config file {cfg_path} must contain a JSON object at the top level, "
                f"got {type(data).__name__}",
            )

        # ---- Phase 3: closed-set key check ----
        # ``device_profile`` and ``device_profiles`` are input-only keys: the
        # loader normalises them into the dataclass field ``device_profiles``
        # (a list of :class:`DeviceProfile`). They are accepted in the JSON
        # payload but never stored verbatim, and the two are mutually
        # exclusive.
        dataclass_field_names = {f.name for f in fields(cls)}
        accepted_input_keys = (
            dataclass_field_names - {"device_profiles"}
        ) | _PROFILE_INPUT_KEYS
        for key in data:
            if key not in accepted_input_keys:
                raise ConfigError(
                    f"unknown config field {key!r} in {cfg_path}",
                    remediation=(
                        f"remove this key or check for a typo; valid keys are: "
                        f"{', '.join(sorted(accepted_input_keys))}"
                    ),
                )

        if "device_profile" in data and "device_profiles" in data:
            raise ConfigError(
                f"config at {cfg_path} declares both 'device_profile' and "
                f"'device_profiles'; choose one",
                remediation="remove one of the keys",
            )

        # ---- Phase 4: per-field type-check + path expansion ----
        kwargs: dict[str, Any] = {}
        for f in fields(cls):
            if f.name == "device_profiles":
                continue  # handled separately below
            if f.name not in data:
                continue
            value = data[f.name]
            kwargs[f.name] = _coerce_field(cfg_path, f.name, value)

        # ---- Phase 4b: normalise profile input shapes ----
        profile_list = _normalise_device_profiles(cfg_path, data)
        if profile_list is not None:
            kwargs["device_profiles"] = profile_list
        # else: leave unset so the dataclass default_factory + _apply_defaults
        # supply the built-in rm2 profile and emit the DEBUG log.

        # ---- Phase 5: defaults for omitted optional fields ----
        kwargs = _apply_defaults(kwargs)

        # ---- Phase 6: build + validate ----
        cfg = cls(**kwargs)
        cfg.validate(_source_path=cfg_path)
        return cfg

    def validate(self, *, _source_path: Path | None = None) -> None:
        """Enforce semantic invariants. Raise ``ConfigError`` on violation.

        The optional ``_source_path`` keyword lets ``load()`` thread the
        config-file path into error messages so users always see the file
        they need to edit alongside the field name (Requirement 1.4). When
        called standalone (e.g. by tests constructing a ``Config`` by hand),
        the path falls back to ``"<config>"``.
        """
        where = str(_source_path) if _source_path is not None else "<config>"

        # schedule_time: must match HH:MM and parse as a wall-clock time.
        if not isinstance(self.schedule_time, str) or not _SCHEDULE_TIME_RE.match(
            self.schedule_time
        ):
            raise ConfigError(
                f"config field 'schedule_time' in {where}: expected 'HH:MM' "
                f"(24-hour clock), got {self.schedule_time!r}",
                remediation="use a value like '05:30' or '17:45'",
            )
        try:
            _dt.time.fromisoformat(self.schedule_time)
        except ValueError as exc:
            raise ConfigError(
                f"config field 'schedule_time' in {where}: not a valid wall-clock "
                f"time ({exc}); got {self.schedule_time!r}",
                remediation="use a value like '05:30' (00-23 hours, 00-59 minutes)",
            ) from exc

        # remarkable_folder: must be an absolute-style path (begins with '/').
        if not isinstance(self.remarkable_folder, str) or not self.remarkable_folder.startswith("/"):
            raise ConfigError(
                f"config field 'remarkable_folder' in {where}: must be a string "
                f"starting with '/' (e.g. '/News'); got {self.remarkable_folder!r}",
            )

        # stories: required, non-empty list of objects.
        if not isinstance(self.stories, list):
            raise ConfigError(
                f"config field 'stories' in {where}: expected list, got "
                f"{type(self.stories).__name__}",
            )
        if len(self.stories) == 0:
            raise ConfigError(
                f"config field 'stories' in {where} must be a non-empty list of "
                f"goosepaper story objects",
                remediation="add at least one entry like "
                '{"provider": "rss", "config": {"rss_path": "..."}}',
            )
        for i, story in enumerate(self.stories):
            if not isinstance(story, dict):
                raise ConfigError(
                    f"config field 'stories[{i}]' in {where}: expected object, "
                    f"got {type(story).__name__}",
                )

        # output_dir: present and absolute (load() resolves; this guards
        # against direct ``Config(...)`` construction).
        if not isinstance(self.output_dir, Path) or not self.output_dir.is_absolute():
            raise ConfigError(
                f"config field 'output_dir' in {where}: must resolve to an "
                f"absolute path; got {self.output_dir!r}",
            )

        # log_dir: optional, but if present must be an absolute Path.
        if self.log_dir is not None:
            if not isinstance(self.log_dir, Path) or not self.log_dir.is_absolute():
                raise ConfigError(
                    f"config field 'log_dir' in {where}: must resolve to an "
                    f"absolute path; got {self.log_dir!r}",
                )

        # Bounded retry counts must be positive (Req. 9.2 / 9.3 talk about
        # "small, bounded number of times" — zero would silently disable).
        for name in ("feed_fetch_retries", "upload_retries", "subprocess_timeout_s"):
            value = getattr(self, name)
            if not isinstance(value, int) or value <= 0:
                raise ConfigError(
                    f"config field {name!r} in {where}: must be a positive integer; "
                    f"got {value!r}",
                )
        for name in ("feed_fetch_backoff_s", "upload_backoff_s"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or value <= 0:
                raise ConfigError(
                    f"config field {name!r} in {where}: must be a positive number; "
                    f"got {value!r}",
                )
        if self.font_size is not None:
            if not isinstance(self.font_size, int) or self.font_size <= 0:
                raise ConfigError(
                    f"config field 'font_size' in {where}: must be a positive integer "
                    f"or omitted; got {self.font_size!r}",
                )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


# (field name) -> (display name, accepted Python types)
# We use a *tuple* of accepted types so JSON's loose number model (an
# integer literal arrives as ``int``, never the wider type the dataclass
# annotates) is handled without forcing the user to write ``1.0`` for a
# field annotated ``float``.
_TYPE_RULES: dict[str, tuple[str, tuple[type, ...]]] = {
    "schedule_time": ("string", (str,)),
    "remarkable_folder": ("string", (str,)),
    "stories": ("list", (list,)),
    "font_size": ("integer", (int,)),
    "user_agent": ("string", (str,)),
    "goosepaper_bin": ("string", (str,)),
    "rmapi_bin": ("string", (str,)),
    "feed_fetch_retries": ("integer", (int,)),
    "feed_fetch_backoff_s": ("number", (int, float)),
    "upload_retries": ("integer", (int,)),
    "upload_backoff_s": ("number", (int, float)),
    "subprocess_timeout_s": ("integer", (int,)),
    # Path fields accept a string (file path) only.
    "output_dir": ("string (filesystem path)", (str,)),
    "log_dir": ("string (filesystem path)", (str,)),
}


def _coerce_field(cfg_path: Path, name: str, value: Any) -> Any:
    """Type-check ``value`` against the rule for ``name`` and coerce paths.

    The dataclass's static types are advisory; this function is the
    runtime gate that turns "JSON of unknown shape" into "values that
    match the dataclass". ``bool`` is rejected for integer fields because
    Python treats ``True`` / ``False`` as ints and we do not want
    ``"feed_fetch_retries": true`` to silently mean "1".
    """
    rule = _TYPE_RULES.get(name)
    if rule is None:  # pragma: no cover - guarded by closed-set check
        raise ConfigError(f"unknown config field {name!r} in {cfg_path}")

    display, accepted = rule

    # Reject bool -> int slip for integer-typed fields.
    if accepted == (int,) and isinstance(value, bool):
        raise ConfigError(
            f"config field {name!r} in {cfg_path}: expected {display}, "
            f"got bool ({value!r})",
        )
    # Reject bool for number fields too (same reasoning).
    if accepted == (int, float) and isinstance(value, bool):
        raise ConfigError(
            f"config field {name!r} in {cfg_path}: expected {display}, "
            f"got bool ({value!r})",
        )

    if not isinstance(value, accepted):
        raise ConfigError(
            f"config field {name!r} in {cfg_path}: expected {display}, "
            f"got {type(value).__name__}",
        )

    if name in _PATH_FIELDS:
        # Expand ``~`` then resolve to an absolute path. ``resolve()`` is
        # safe to call on a non-existent path on Python 3.11+ (strict=False
        # is the default).
        return Path(value).expanduser().resolve()

    # Pass-through for everything else; the dataclass holds the value
    # verbatim. ``stories`` is opaque to renewsable beyond "list of dicts"
    # — goosepaper owns deeper validation (per design Implementation Notes).
    return value


def _apply_defaults(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Fill in defaults that depend on the *current* environment.

    Path defaults are computed here (not at class-definition time) so that
    XDG env changes between module import and ``Config.load`` are honoured
    — this is what makes the helpers in :mod:`renewsable.paths` testable
    via ``monkeypatch.setenv`` and what makes ``Config.load`` reusable in a
    long-lived process where env may be reconfigured.
    """
    if "output_dir" not in kwargs:
        kwargs["output_dir"] = paths.default_output_dir()
    if "log_dir" not in kwargs:
        kwargs["log_dir"] = paths.default_log_dir()
    if "device_profiles" not in kwargs:
        # No profile fields declared — fall back to the default rm2 profile.
        # DEBUG (not WARNING) because this is the expected single-profile
        # path for the majority of operators (Req 4.1, Req 1.1).
        logger.debug("no device profile declared, defaulting to %s", "rm2")
        kwargs["device_profiles"] = [BUILTIN_PROFILES["rm2"]]
    return kwargs


def _normalise_device_profiles(
    cfg_path: Path, data: dict[str, Any]
) -> list[DeviceProfile] | None:
    """Normalise ``device_profile`` / ``device_profiles`` input into a list.

    Returns ``None`` when neither key is present, signalling the caller to
    apply the default (and emit the DEBUG log). Otherwise returns the fully
    resolved list of :class:`DeviceProfile` instances. Raises
    :class:`ConfigError` on type or validation failure; ``_resolve_profile``
    (``profiles.resolve``) raises on unknown names / bad overrides.
    """
    if "device_profile" in data:
        return [_resolve_single(cfg_path, "device_profile", data["device_profile"])]
    if "device_profiles" in data:
        raw = data["device_profiles"]
        if not isinstance(raw, list):
            raise ConfigError(
                f"config field 'device_profiles' in {cfg_path}: expected list, "
                f"got {type(raw).__name__}",
            )
        return [
            _resolve_single(cfg_path, f"device_profiles[{i}]", entry)
            for i, entry in enumerate(raw)
        ]
    return None


def _resolve_single(cfg_path: Path, where: str, entry: Any) -> DeviceProfile:
    """Resolve a single profile entry (string shorthand or object form)."""
    if isinstance(entry, str):
        return _resolve_profile(entry)
    if isinstance(entry, dict):
        if "name" not in entry:
            raise ConfigError(
                f"config field {where!r} in {cfg_path}: profile object must "
                f"include a 'name' key; got keys {sorted(entry)!r}",
            )
        name = entry["name"]
        overrides = {k: v for k, v in entry.items() if k != "name"}
        return _resolve_profile(name, overrides or None)
    raise ConfigError(
        f"config field {where!r} in {cfg_path}: expected string or object, "
        f"got {type(entry).__name__}",
    )
