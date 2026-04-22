"""Tests for `renewsable.profiles` — DeviceProfile, BUILTIN_PROFILES, resolve.

Covers task 1.1 of the device-profiles spec.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from renewsable.errors import ConfigError
from renewsable.profiles import BUILTIN_PROFILES, DeviceProfile, resolve


class TestBuiltinRegistry:
    """Both built-ins exist with documented defaults."""

    def test_registry_keys(self) -> None:
        assert sorted(BUILTIN_PROFILES) == ["paper_pro_move", "rm2"]

    def test_rm2_defaults(self) -> None:
        p = BUILTIN_PROFILES["rm2"]
        assert isinstance(p, DeviceProfile)
        assert p.name == "rm2"
        assert p.page_width_in == 6.18
        assert p.page_height_in == 8.23
        assert p.margin_in == 0.35
        assert p.font_size_pt == 12
        assert p.color is True
        assert p.remarkable_folder is None

    def test_paper_pro_move_defaults(self) -> None:
        p = BUILTIN_PROFILES["paper_pro_move"]
        assert isinstance(p, DeviceProfile)
        assert p.name == "paper_pro_move"
        assert p.page_width_in == 4.38
        assert p.page_height_in == 5.84
        assert p.margin_in == 0.25
        assert p.font_size_pt == 11
        assert p.color is True
        assert p.remarkable_folder is None

    def test_builtins_are_portrait(self) -> None:
        for p in BUILTIN_PROFILES.values():
            assert p.page_width_in < p.page_height_in


class TestResolveNoOverrides:
    """resolve(name) returns the built-in unchanged (identity-preserving)."""

    def test_rm2_identity(self) -> None:
        assert resolve("rm2") is BUILTIN_PROFILES["rm2"]

    def test_paper_pro_move_identity(self) -> None:
        assert resolve("paper_pro_move") is BUILTIN_PROFILES["paper_pro_move"]

    def test_resolve_with_none_overrides_identity(self) -> None:
        assert resolve("rm2", None) is BUILTIN_PROFILES["rm2"]

    def test_resolve_with_empty_overrides_identity(self) -> None:
        # Empty dict is semantically "no overrides" — keep identity.
        assert resolve("rm2", {}) is BUILTIN_PROFILES["rm2"]


class TestResolveWithOverrides:
    """resolve(name, overrides) shallow-merges validated overrides."""

    def test_remarkable_folder_override(self) -> None:
        p = resolve("paper_pro_move", {"remarkable_folder": "/News-Move"})
        base = BUILTIN_PROFILES["paper_pro_move"]
        assert p.remarkable_folder == "/News-Move"
        # Every other field matches the built-in.
        assert p.name == base.name
        assert p.page_width_in == base.page_width_in
        assert p.page_height_in == base.page_height_in
        assert p.margin_in == base.margin_in
        assert p.font_size_pt == base.font_size_pt
        assert p.color == base.color
        # Base is not mutated.
        assert base.remarkable_folder is None

    def test_color_override(self) -> None:
        p = resolve("rm2", {"color": False})
        assert p.color is False
        assert p.name == "rm2"
        assert BUILTIN_PROFILES["rm2"].color is True  # base intact

    def test_page_dimension_override(self) -> None:
        p = resolve("rm2", {"page_width_in": 5.5, "page_height_in": 8.0})
        assert p.page_width_in == 5.5
        assert p.page_height_in == 8.0

    def test_font_size_override(self) -> None:
        p = resolve("rm2", {"font_size_pt": 14})
        assert p.font_size_pt == 14

    def test_remarkable_folder_none_allowed(self) -> None:
        p = resolve("paper_pro_move", {"remarkable_folder": None})
        assert p.remarkable_folder is None


class TestResolveUnknownName:
    """Unknown name raises ConfigError naming both the value and supported set."""

    def test_unknown_name_raises(self) -> None:
        with pytest.raises(ConfigError) as excinfo:
            resolve("kindle")
        msg = str(excinfo.value)
        assert "kindle" in msg
        assert "rm2" in msg
        assert "paper_pro_move" in msg

    def test_empty_name_raises(self) -> None:
        with pytest.raises(ConfigError):
            resolve("")

    def test_non_matching_regex_raises(self) -> None:
        # starts with digit — fails the name regex
        with pytest.raises(ConfigError):
            resolve("2rm")

    def test_uppercase_rejected(self) -> None:
        with pytest.raises(ConfigError):
            resolve("RM2")

    def test_non_string_name_raises(self) -> None:
        with pytest.raises(ConfigError):
            resolve(123)  # type: ignore[arg-type]


class TestResolveOverrideRejections:
    """Override validation — name, unknown keys, invalid types."""

    def test_override_name_rejected(self) -> None:
        with pytest.raises(ConfigError) as excinfo:
            resolve("rm2", {"name": "rm2"})
        assert "name" in str(excinfo.value)

    def test_override_name_rejected_even_when_same(self) -> None:
        # design: overrides may not change name; we forbid the key entirely
        with pytest.raises(ConfigError):
            resolve("rm2", {"name": "rm2"})

    def test_unknown_override_key_rejected(self) -> None:
        with pytest.raises(ConfigError) as excinfo:
            resolve("rm2", {"bogus_key": 1})
        assert "bogus_key" in str(excinfo.value)

    def test_page_width_zero_rejected(self) -> None:
        with pytest.raises(ConfigError):
            resolve("rm2", {"page_width_in": 0})

    def test_page_width_negative_rejected(self) -> None:
        with pytest.raises(ConfigError):
            resolve("rm2", {"page_width_in": -1.0})

    def test_page_height_negative_rejected(self) -> None:
        with pytest.raises(ConfigError):
            resolve("rm2", {"page_height_in": -2.0})

    def test_margin_negative_rejected(self) -> None:
        with pytest.raises(ConfigError):
            resolve("rm2", {"margin_in": -0.1})

    def test_font_size_zero_rejected(self) -> None:
        with pytest.raises(ConfigError):
            resolve("rm2", {"font_size_pt": 0})

    def test_font_size_non_int_rejected(self) -> None:
        with pytest.raises(ConfigError):
            resolve("rm2", {"font_size_pt": "12"})

    def test_color_int_rejected(self) -> None:
        # bool is int in Python; we still reject raw int 1 as a bool value
        with pytest.raises(ConfigError):
            resolve("rm2", {"color": 1})

    def test_color_string_rejected(self) -> None:
        with pytest.raises(ConfigError):
            resolve("rm2", {"color": "true"})

    def test_remarkable_folder_no_leading_slash_rejected(self) -> None:
        with pytest.raises(ConfigError):
            resolve("rm2", {"remarkable_folder": "News"})

    def test_remarkable_folder_non_string_rejected(self) -> None:
        with pytest.raises(ConfigError):
            resolve("rm2", {"remarkable_folder": 42})

    def test_page_width_non_number_rejected(self) -> None:
        with pytest.raises(ConfigError):
            resolve("rm2", {"page_width_in": "6.18"})


class TestProfileIsFrozen:
    """DeviceProfile is a frozen dataclass."""

    def test_cannot_mutate_remarkable_folder(self) -> None:
        p = BUILTIN_PROFILES["rm2"]
        with pytest.raises((FrozenInstanceError, AttributeError)):
            p.remarkable_folder = "/Papers"  # type: ignore[misc]

    def test_cannot_mutate_page_width(self) -> None:
        p = BUILTIN_PROFILES["rm2"]
        with pytest.raises((FrozenInstanceError, AttributeError)):
            p.page_width_in = 1.0  # type: ignore[misc]

    def test_cannot_mutate_color(self) -> None:
        p = resolve("paper_pro_move", {"remarkable_folder": "/x"})
        with pytest.raises((FrozenInstanceError, AttributeError)):
            p.color = False  # type: ignore[misc]
