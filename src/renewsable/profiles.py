"""Device profile value object, built-in registry, and resolver.

Design reference: ``.kiro/specs/device-profiles/design.md`` — "Config /
profiles" component block. Requirements 1.1, 1.2, 1.3, 1.4, 3.1, 3.2, 9.1.

The :class:`DeviceProfile` is a pure frozen value object; validation lives in
:func:`resolve`, which is the single entry point callers use to obtain a
profile (optionally with shallow-merged overrides).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

from renewsable.errors import ConfigError

__all__ = [
    "DeviceProfile",
    "BUILTIN_PROFILES",
    "resolve",
]


# Profile names must be lowercase identifiers, 1..32 chars, starting with a
# letter. This constrains filesystem-visible `<tmpdir>/styles/<name>.css`
# paths (see design "Security Considerations").
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")

# Fields an operator may override via `resolve(name, overrides)`. `name` is
# intentionally excluded — resolving always binds to the supplied name.
_OVERRIDABLE_FIELDS = frozenset(
    {
        "page_width_in",
        "page_height_in",
        "margin_in",
        "font_size_pt",
        "color",
        "remarkable_folder",
    }
)


@dataclass(frozen=True)
class DeviceProfile:
    """Immutable per-device render settings.

    No behaviour beyond construction and equality. All validation happens in
    :func:`resolve`, keeping this type a pure value object.
    """

    name: str
    page_width_in: float
    page_height_in: float
    margin_in: float
    font_size_pt: int
    color: bool = True
    remarkable_folder: str | None = None


BUILTIN_PROFILES: dict[str, DeviceProfile] = {
    "rm2": DeviceProfile(
        name="rm2",
        page_width_in=6.18,
        page_height_in=8.23,
        margin_in=0.35,
        font_size_pt=12,
        color=True,
    ),
    "paper_pro_move": DeviceProfile(
        name="paper_pro_move",
        page_width_in=4.38,
        page_height_in=5.84,
        margin_in=0.25,
        font_size_pt=11,
        color=True,
    ),
}


def _supported_names_phrase() -> str:
    """Format the supported profile names for inclusion in error messages."""
    return ", ".join(sorted(BUILTIN_PROFILES))


def _validate_positive_number(field: str, value: Any) -> None:
    # bool is an int subclass — reject it explicitly so color-like values
    # don't slip through as dimensions.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(
            f"invalid value for profile override {field!r}: "
            f"expected a positive number, got {value!r}"
        )
    if value <= 0:
        raise ConfigError(
            f"invalid value for profile override {field!r}: "
            f"expected a positive number, got {value!r}"
        )


def _validate_positive_int(field: str, value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(
            f"invalid value for profile override {field!r}: "
            f"expected a positive integer, got {value!r}"
        )
    if value <= 0:
        raise ConfigError(
            f"invalid value for profile override {field!r}: "
            f"expected a positive integer, got {value!r}"
        )


def _validate_bool(field: str, value: Any) -> None:
    if not isinstance(value, bool):
        raise ConfigError(
            f"invalid value for profile override {field!r}: "
            f"expected a bool, got {value!r}"
        )


def _validate_remarkable_folder(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        raise ConfigError(
            "invalid value for profile override 'remarkable_folder': "
            f"expected a string starting with '/' or None, got {value!r}"
        )
    if not value.startswith("/"):
        raise ConfigError(
            "invalid value for profile override 'remarkable_folder': "
            f"expected a path starting with '/', got {value!r}"
        )


def _validate_overrides(overrides: dict[str, Any]) -> None:
    for key, value in overrides.items():
        if key == "name":
            raise ConfigError(
                "profile override may not change 'name'; "
                "use a different profile or adjust the profile entry's key"
            )
        if key not in _OVERRIDABLE_FIELDS:
            raise ConfigError(
                f"unknown profile override key {key!r}; "
                "supported override keys: "
                + ", ".join(sorted(_OVERRIDABLE_FIELDS))
            )
        if key in ("page_width_in", "page_height_in", "margin_in"):
            _validate_positive_number(key, value)
        elif key == "font_size_pt":
            _validate_positive_int(key, value)
        elif key == "color":
            _validate_bool(key, value)
        elif key == "remarkable_folder":
            _validate_remarkable_folder(value)


def resolve(name: str, overrides: dict[str, Any] | None = None) -> DeviceProfile:
    """Return the built-in profile ``name`` with optional overrides applied.

    Overrides are shallow-merged on top of the built-in via
    :func:`dataclasses.replace`. Passing ``None`` or an empty dict returns
    the built-in instance unchanged (identity-preserving).

    Raises
    ------
    ConfigError
        If ``name`` is not a valid profile name, not a known built-in, or if
        any override key/value is invalid (unknown key, attempt to change
        ``name``, non-matching type, non-positive dimension, bad folder).
    """
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise ConfigError(
            f"invalid profile name {name!r}; "
            f"supported profiles: {_supported_names_phrase()}"
        )
    if name not in BUILTIN_PROFILES:
        raise ConfigError(
            f"unknown profile {name!r}; "
            f"supported profiles: {_supported_names_phrase()}"
        )

    base = BUILTIN_PROFILES[name]
    if not overrides:
        return base

    _validate_overrides(overrides)
    return replace(base, **overrides)
