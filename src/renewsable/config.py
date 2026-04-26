"""Configuration loader and validator for renewsable.

Design reference: the "Config" component block, the "Data Models" persistent-
file table, and the "Stories Schema (closed set, validated by Config.load)"
section in ``.kiro/specs/epub-output/design.md``.

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
- 2.4 No goosepaper-specific configuration keys; ``stories`` schema is owned
  by renewsable (closed-set per-entry shape).

Shape
-----
``Config`` is a ``frozen=True`` dataclass — once loaded it is a value object
that callers can pass around without fear of mutation. Every field has a
default at the dataclass level so the dataclass itself is constructible
without arguments; the *real* invariants ("``stories`` non-empty",
"``schedule_time`` parses as a clock", "each ``stories`` entry matches the
Stories Schema") are enforced by ``validate()``, which ``load()`` invokes
before returning. This keeps the type-level contract and the runtime contract
cleanly separated.

Closed-set top-level keys
-------------------------
``Config.load`` rejects any unknown top-level JSON key. This catches typos
("``stoires``", "``schedule_tlme``") that would otherwise silently fall back
to a default and waste an entire scheduled run debugging "why didn't my new
feed appear". With goosepaper removed, keys such as ``goosepaper_bin``,
``font_size``, ``subprocess_timeout_s``, ``device_profile``, and
``device_profiles`` are no longer accepted — they raise ``ConfigError`` with
a remediation that points at the new schema.
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


__all__ = ["Config"]


logger = logging.getLogger(__name__)


# ``HH:MM`` 24-hour clock. The pattern is intentionally strict (two digits
# each, colon separator) — design.md "Config" -> "Postconditions" pins the
# regex to ``^\d{2}:\d{2}$``. ``datetime.time.fromisoformat`` provides the
# range check (00-23 / 00-59) so we do not duplicate it here.
_SCHEDULE_TIME_RE = re.compile(r"^\d{2}:\d{2}$")


# Fields whose values are paths and therefore need ``~`` expansion plus
# ``resolve()`` to an absolute path before landing in the dataclass.
_PATH_FIELDS: frozenset[str] = frozenset({"output_dir", "log_dir"})


# Stories Schema (design.md "Stories Schema (closed set, validated by
# Config.load)"). Each ``stories[i]`` must have exactly two top-level keys:
# ``provider`` (string == "rss") and ``config`` (dict). Within ``config``,
# ``rss_path`` is required (http(s) URL) and ``limit`` is optional (positive
# int, no bool slip).
_STORY_TOP_KEYS: frozenset[str] = frozenset({"provider", "config"})
_STORY_CONFIG_REQUIRED: frozenset[str] = frozenset({"rss_path"})
_STORY_CONFIG_OPTIONAL: frozenset[str] = frozenset({"limit"})
_STORY_CONFIG_ALLOWED: frozenset[str] = _STORY_CONFIG_REQUIRED | _STORY_CONFIG_OPTIONAL
_STORIES_SCHEMA_REMEDIATION = (
    "each stories entry must have exactly the keys "
    '{"provider": "rss", "config": {"rss_path": "<http(s) URL>", '
    '"limit": <optional positive int>}}'
)


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
    log_dir: Path | None = None
    user_agent: str = "renewsable/0.1 (+https://github.com/kbence/renewsable)"
    rmapi_bin: str = "rmapi"
    feed_fetch_retries: int = 3
    feed_fetch_backoff_s: float = 1.0
    upload_retries: int = 3
    upload_backoff_s: float = 2.0

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
        # With goosepaper and device profiles gone, the accepted top-level
        # keys are exactly the dataclass field names. Anything else (e.g.,
        # ``goosepaper_bin``, ``font_size``, ``device_profile``) is a typo
        # or a leftover from a pre-EPUB config and must be flagged.
        dataclass_field_names = {f.name for f in fields(cls)}
        for key in data:
            if key not in dataclass_field_names:
                raise ConfigError(
                    f"unknown config field {key!r} in {cfg_path}",
                    remediation=(
                        f"remove this key or check for a typo; valid keys are: "
                        f"{', '.join(sorted(dataclass_field_names))}"
                    ),
                )

        # ---- Phase 4: per-field type-check + path expansion ----
        kwargs: dict[str, Any] = {}
        for f in fields(cls):
            if f.name not in data:
                continue
            value = data[f.name]
            kwargs[f.name] = _coerce_field(cfg_path, f.name, value)

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

        # stories: required, non-empty list of objects matching the Stories
        # Schema (design.md "Stories Schema (closed set, validated by
        # Config.load)").
        if not isinstance(self.stories, list):
            raise ConfigError(
                f"config field 'stories' in {where}: expected list, got "
                f"{type(self.stories).__name__}",
            )
        if len(self.stories) == 0:
            raise ConfigError(
                f"config field 'stories' in {where} must be a non-empty list of "
                f"story objects",
                remediation="add at least one entry like "
                '{"provider": "rss", "config": {"rss_path": "..."}}',
            )
        for i, story in enumerate(self.stories):
            _validate_story_entry(where, i, story)

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

        # Bounded retry counts must be positive (zero would silently disable).
        for name in ("feed_fetch_retries", "upload_retries"):
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
    "user_agent": ("string", (str,)),
    "rmapi_bin": ("string", (str,)),
    "feed_fetch_retries": ("integer", (int,)),
    "feed_fetch_backoff_s": ("number", (int, float)),
    "upload_retries": ("integer", (int,)),
    "upload_backoff_s": ("number", (int, float)),
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
    # verbatim. ``stories`` deeper validation happens in ``Config.validate``
    # (Stories Schema, design.md).
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
    return kwargs


def _validate_story_entry(where: str, index: int, story: Any) -> None:
    """Validate a single ``stories[i]`` against the Stories Schema.

    Design reference: "Stories Schema (closed set, validated by
    ``Config.load``)" in design.md. Required shape:

    .. code-block:: json

        {"provider": "rss",
         "config": {"rss_path": "https://...", "limit": 5}}

    Any other top-level key in the entry, any other key in ``config``, a
    ``provider`` other than ``"rss"``, or a non-http(s) ``rss_path`` raises
    :class:`ConfigError` naming both the file path and the offending key /
    value. ``limit`` is optional but if present must be a positive ``int``
    (``bool`` is rejected explicitly).
    """
    if not isinstance(story, dict):
        raise ConfigError(
            f"config field 'stories[{index}]' in {where}: expected object, "
            f"got {type(story).__name__}",
            remediation=_STORIES_SCHEMA_REMEDIATION,
        )

    # Top-level key set must be exactly {"provider", "config"}.
    extra_top = set(story.keys()) - _STORY_TOP_KEYS
    if extra_top:
        bad = sorted(extra_top)[0]
        raise ConfigError(
            f"config field 'stories[{index}]' in {where}: unknown key {bad!r}; "
            f"only 'provider' and 'config' are allowed",
            remediation=_STORIES_SCHEMA_REMEDIATION,
        )
    missing_top = _STORY_TOP_KEYS - set(story.keys())
    if missing_top:
        bad = sorted(missing_top)[0]
        raise ConfigError(
            f"config field 'stories[{index}]' in {where}: missing required "
            f"key {bad!r}",
            remediation=_STORIES_SCHEMA_REMEDIATION,
        )

    provider = story["provider"]
    if provider != "rss":
        raise ConfigError(
            f"config field 'stories[{index}].provider' in {where}: expected "
            f"'rss', got {provider!r}",
            remediation=_STORIES_SCHEMA_REMEDIATION,
        )

    cfg = story["config"]
    if not isinstance(cfg, dict):
        raise ConfigError(
            f"config field 'stories[{index}].config' in {where}: expected "
            f"object, got {type(cfg).__name__}",
            remediation=_STORIES_SCHEMA_REMEDIATION,
        )

    extra_cfg = set(cfg.keys()) - _STORY_CONFIG_ALLOWED
    if extra_cfg:
        bad = sorted(extra_cfg)[0]
        raise ConfigError(
            f"config field 'stories[{index}].config' in {where}: unknown key "
            f"{bad!r}; only {sorted(_STORY_CONFIG_ALLOWED)!r} are allowed",
            remediation=_STORIES_SCHEMA_REMEDIATION,
        )
    missing_cfg = _STORY_CONFIG_REQUIRED - set(cfg.keys())
    if missing_cfg:
        bad = sorted(missing_cfg)[0]
        raise ConfigError(
            f"config field 'stories[{index}].config' in {where}: missing "
            f"required key {bad!r}",
            remediation=_STORIES_SCHEMA_REMEDIATION,
        )

    rss_path = cfg["rss_path"]
    if not isinstance(rss_path, str) or not (
        rss_path.startswith("http://") or rss_path.startswith("https://")
    ):
        raise ConfigError(
            f"config field 'stories[{index}].config.rss_path' in {where}: "
            f"expected an http:// or https:// URL string, got {rss_path!r}",
            remediation=_STORIES_SCHEMA_REMEDIATION,
        )

    if "limit" in cfg:
        limit = cfg["limit"]
        # Reject bool first because bool is a subclass of int.
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ConfigError(
                f"config field 'stories[{index}].config.limit' in {where}: "
                f"expected a positive integer, got {limit!r}",
                remediation=_STORIES_SCHEMA_REMEDIATION,
            )
