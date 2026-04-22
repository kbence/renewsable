"""Unit tests for :mod:`renewsable.config` (Task 2.1).

Design reference: the "Config" component block and the "Data Models" persistent-
file table in ``.kiro/specs/daily-paper/design.md``.

Requirements covered: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6.

Each scenario probes one acceptance criterion of Requirement 1:

* 1.1 — load via explicit path argument.
* 1.2 — fields exposed: feeds (``stories``), reMarkable folder, schedule time,
  output directory.
* 1.3 — missing config file: error names the expected path.
* 1.4 — present file with missing or malformed required field: error names the
  field and the path.
* 1.5 — every load is fresh (no caching that would survive an edit).
* 1.6 — defaults applied for omitted optional fields.

Plus invariants from the design:
* Frozen dataclass (immutability).
* ``~`` expansion + absolute resolution for path fields.
* Closed-set top-level keys (unknown keys rejected).
"""

from __future__ import annotations

import json
import logging
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from renewsable.config import Config
from renewsable.errors import ConfigError
from renewsable.profiles import BUILTIN_PROFILES


FIXTURES_DIR = Path(__file__).parent / "fixtures"
VALID_FIXTURE = FIXTURES_DIR / "config.valid.json"
MISSING_FIELD_FIXTURE = FIXTURES_DIR / "config.missing_field.json"
BAD_SCHEDULE_FIXTURE = FIXTURES_DIR / "config.bad_schedule.json"
PROFILE_STRING_FIXTURE = FIXTURES_DIR / "config.profile_string.json"
PROFILE_OBJECT_FIXTURE = FIXTURES_DIR / "config.profile_object.json"
PROFILES_LIST_FIXTURE = FIXTURES_DIR / "config.profiles_list.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Pin ``$HOME`` so ``~`` expansion is deterministic."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("USERPROFILE", raising=False)
    # Also pin XDG so default_output_dir / default_log_dir are deterministic.
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return home


def _write_json(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Happy path: load the valid fixture
# ---------------------------------------------------------------------------


class TestLoadValidFixture:
    """Requirement 1.1, 1.2, 1.6."""

    def test_returns_config_instance(self, fake_home):
        cfg = Config.load(VALID_FIXTURE)
        assert isinstance(cfg, Config)

    def test_schedule_time_from_file(self, fake_home):
        cfg = Config.load(VALID_FIXTURE)
        assert cfg.schedule_time == "06:15"

    def test_remarkable_folder_from_file(self, fake_home):
        cfg = Config.load(VALID_FIXTURE)
        assert cfg.remarkable_folder == "/News"

    def test_stories_from_file(self, fake_home):
        cfg = Config.load(VALID_FIXTURE)
        assert isinstance(cfg.stories, list)
        assert len(cfg.stories) == 2
        assert cfg.stories[0]["provider"] == "rss"
        assert cfg.stories[0]["config"]["rss_path"] == "https://telex.hu/rss"

    def test_output_dir_is_absolute_and_tilde_expanded(self, fake_home):
        cfg = Config.load(VALID_FIXTURE)
        assert cfg.output_dir.is_absolute()
        # The fixture uses "~/renewsable/out"; with HOME pinned it expands here.
        assert cfg.output_dir == fake_home / "renewsable" / "out"

    def test_defaults_applied_for_omitted_optional_fields(self, fake_home):
        """Requirement 1.6: documented defaults for optional fields."""
        cfg = Config.load(VALID_FIXTURE)
        # font_size omitted -> None
        assert cfg.font_size is None
        # User agent default
        assert cfg.user_agent == "renewsable/0.1 (+https://github.com/bnc/renewsable)"
        # Subprocess defaults
        assert cfg.goosepaper_bin == "goosepaper"
        assert cfg.rmapi_bin == "rmapi"
        assert cfg.feed_fetch_retries == 3
        assert cfg.feed_fetch_backoff_s == 1.0
        assert cfg.upload_retries == 3
        assert cfg.upload_backoff_s == 2.0
        assert cfg.subprocess_timeout_s == 180

    def test_log_dir_default_is_absolute(self, fake_home):
        """Requirement 1.6: log_dir defaults to ``paths.default_log_dir()``."""
        cfg = Config.load(VALID_FIXTURE)
        assert cfg.log_dir is not None
        assert cfg.log_dir.is_absolute()
        # With HOME pinned and XDG_STATE_HOME unset.
        assert cfg.log_dir == fake_home / ".local" / "state" / "renewsable" / "logs"


# ---------------------------------------------------------------------------
# Default for output_dir when omitted entirely
# ---------------------------------------------------------------------------


class TestOutputDirDefault:
    """Requirement 1.6: default applied when ``output_dir`` is omitted."""

    def test_output_dir_omitted_uses_default(self, fake_home, tmp_path):
        cfg_path = _write_json(
            tmp_path / "cfg.json",
            {
                "schedule_time": "05:30",
                "remarkable_folder": "/News",
                "stories": [
                    {"provider": "rss", "config": {"rss_path": "https://x/y"}}
                ],
            },
        )
        cfg = Config.load(cfg_path)
        assert cfg.output_dir == fake_home / ".local" / "state" / "renewsable" / "out"
        assert cfg.output_dir.is_absolute()


# ---------------------------------------------------------------------------
# Missing config file
# ---------------------------------------------------------------------------


class TestMissingFile:
    """Requirement 1.3."""

    def test_raises_config_error_naming_path(self, fake_home, tmp_path):
        missing = tmp_path / "nope.json"
        with pytest.raises(ConfigError) as ei:
            Config.load(missing)
        # Message names the path.
        assert str(missing) in ei.value.message
        # And mentions "not found" so the operator knows what kind of error.
        assert "not found" in ei.value.message.lower()


# ---------------------------------------------------------------------------
# Missing required field
# ---------------------------------------------------------------------------


class TestMissingRequiredField:
    """Requirement 1.4 — file present, ``stories`` absent."""

    def test_missing_stories_raises_config_error(self, fake_home):
        with pytest.raises(ConfigError) as ei:
            Config.load(MISSING_FIELD_FIXTURE)
        rendered = ei.value.message
        assert "stories" in rendered
        # And names the file.
        assert str(MISSING_FIELD_FIXTURE) in rendered

    def test_empty_stories_list_raises_config_error(self, fake_home, tmp_path):
        cfg_path = _write_json(
            tmp_path / "cfg.json",
            {
                "schedule_time": "05:30",
                "remarkable_folder": "/News",
                "stories": [],
            },
        )
        with pytest.raises(ConfigError) as ei:
            Config.load(cfg_path)
        assert "stories" in ei.value.message
        assert str(cfg_path) in ei.value.message


# ---------------------------------------------------------------------------
# Malformed schedule_time
# ---------------------------------------------------------------------------


class TestBadScheduleTime:
    """Requirement 1.4 — ``schedule_time`` malformed."""

    def test_out_of_range_schedule_time(self, fake_home):
        with pytest.raises(ConfigError) as ei:
            Config.load(BAD_SCHEDULE_FIXTURE)
        assert "schedule_time" in ei.value.message
        assert str(BAD_SCHEDULE_FIXTURE) in ei.value.message

    @pytest.mark.parametrize("bad_value", ["5:30", "abc", "12:60", "12-30", "", "12:5"])
    def test_pattern_violations(self, fake_home, tmp_path, bad_value):
        cfg_path = _write_json(
            tmp_path / "cfg.json",
            {
                "schedule_time": bad_value,
                "remarkable_folder": "/News",
                "stories": [{"provider": "rss", "config": {"rss_path": "https://x/y"}}],
            },
        )
        with pytest.raises(ConfigError) as ei:
            Config.load(cfg_path)
        assert "schedule_time" in ei.value.message
        assert str(cfg_path) in ei.value.message


# ---------------------------------------------------------------------------
# Unknown top-level keys (closed set)
# ---------------------------------------------------------------------------


class TestUnknownField:
    def test_unknown_key_rejected(self, fake_home, tmp_path):
        cfg_path = _write_json(
            tmp_path / "cfg.json",
            {
                "schedule_time": "05:30",
                "remarkable_folder": "/News",
                "stories": [{"provider": "rss", "config": {"rss_path": "https://x/y"}}],
                "foo": 42,
            },
        )
        with pytest.raises(ConfigError) as ei:
            Config.load(cfg_path)
        assert "foo" in ei.value.message
        assert str(cfg_path) in ei.value.message


# ---------------------------------------------------------------------------
# Type mismatches on known fields
# ---------------------------------------------------------------------------


class TestTypeMismatch:
    def test_stories_must_be_list(self, fake_home, tmp_path):
        cfg_path = _write_json(
            tmp_path / "cfg.json",
            {
                "schedule_time": "05:30",
                "remarkable_folder": "/News",
                "stories": "not-a-list",
            },
        )
        with pytest.raises(ConfigError) as ei:
            Config.load(cfg_path)
        assert "stories" in ei.value.message
        assert str(cfg_path) in ei.value.message

    def test_remarkable_folder_must_be_string(self, fake_home, tmp_path):
        cfg_path = _write_json(
            tmp_path / "cfg.json",
            {
                "schedule_time": "05:30",
                "remarkable_folder": 42,
                "stories": [{"provider": "rss", "config": {"rss_path": "https://x/y"}}],
            },
        )
        with pytest.raises(ConfigError) as ei:
            Config.load(cfg_path)
        assert "remarkable_folder" in ei.value.message

    def test_remarkable_folder_must_start_with_slash(self, fake_home, tmp_path):
        cfg_path = _write_json(
            tmp_path / "cfg.json",
            {
                "schedule_time": "05:30",
                "remarkable_folder": "News",
                "stories": [{"provider": "rss", "config": {"rss_path": "https://x/y"}}],
            },
        )
        with pytest.raises(ConfigError) as ei:
            Config.load(cfg_path)
        assert "remarkable_folder" in ei.value.message


# ---------------------------------------------------------------------------
# Malformed JSON
# ---------------------------------------------------------------------------


class TestMalformedJson:
    def test_invalid_json_raises_config_error_naming_path(self, fake_home, tmp_path):
        cfg_path = tmp_path / "broken.json"
        cfg_path.write_text("{not json", encoding="utf-8")
        with pytest.raises(ConfigError) as ei:
            Config.load(cfg_path)
        assert str(cfg_path) in ei.value.message


# ---------------------------------------------------------------------------
# Immutability + purity
# ---------------------------------------------------------------------------


class TestImmutability:
    def test_config_is_frozen(self, fake_home):
        cfg = Config.load(VALID_FIXTURE)
        with pytest.raises((FrozenInstanceError, AttributeError)):
            cfg.schedule_time = "07:00"  # type: ignore[misc]

    def test_two_loads_produce_equal_configs(self, fake_home):
        """Requirement 1.5 — every load is a fresh read of the file."""
        a = Config.load(VALID_FIXTURE)
        b = Config.load(VALID_FIXTURE)
        assert a == b

    def test_edit_between_loads_is_picked_up(self, fake_home, tmp_path):
        """Requirement 1.5 — edits take effect on the next invocation."""
        cfg_path = _write_json(
            tmp_path / "cfg.json",
            {
                "schedule_time": "05:30",
                "remarkable_folder": "/News",
                "stories": [{"provider": "rss", "config": {"rss_path": "https://x/y"}}],
            },
        )
        first = Config.load(cfg_path)
        assert first.schedule_time == "05:30"

        _write_json(
            cfg_path,
            {
                "schedule_time": "07:45",
                "remarkable_folder": "/News",
                "stories": [{"provider": "rss", "config": {"rss_path": "https://x/y"}}],
            },
        )
        second = Config.load(cfg_path)
        assert second.schedule_time == "07:45"


# ---------------------------------------------------------------------------
# Path expansion / log_dir override
# ---------------------------------------------------------------------------


class TestPathExpansion:
    def test_log_dir_override_is_expanded(self, fake_home, tmp_path):
        cfg_path = _write_json(
            tmp_path / "cfg.json",
            {
                "schedule_time": "05:30",
                "remarkable_folder": "/News",
                "log_dir": "~/some/logs",
                "stories": [{"provider": "rss", "config": {"rss_path": "https://x/y"}}],
            },
        )
        cfg = Config.load(cfg_path)
        assert cfg.log_dir == fake_home / "some" / "logs"
        assert cfg.log_dir.is_absolute()

    def test_relative_output_dir_resolved_to_absolute(self, fake_home, tmp_path, monkeypatch):
        cfg_path = _write_json(
            tmp_path / "cfg.json",
            {
                "schedule_time": "05:30",
                "remarkable_folder": "/News",
                "output_dir": "relative/out",
                "stories": [{"provider": "rss", "config": {"rss_path": "https://x/y"}}],
            },
        )
        cfg = Config.load(cfg_path)
        assert cfg.output_dir.is_absolute()


# ---------------------------------------------------------------------------
# Path argument may also be a string
# ---------------------------------------------------------------------------


class TestPathArgumentForms:
    def test_load_accepts_string_path(self, fake_home):
        cfg = Config.load(str(VALID_FIXTURE))
        assert cfg.schedule_time == "06:15"


# ---------------------------------------------------------------------------
# Device profiles (Task 2.1)
# ---------------------------------------------------------------------------


class TestDeviceProfiles:
    """Requirements 1.1, 1.3, 4.1, 4.2, 4.3 — four input shapes for profiles."""

    def test_no_profile_key_defaults_to_rm2(self, fake_home):
        cfg = Config.load(VALID_FIXTURE)
        assert cfg.device_profiles == [BUILTIN_PROFILES["rm2"]]

    def test_profile_string_shorthand(self, fake_home):
        cfg = Config.load(PROFILE_STRING_FIXTURE)
        assert cfg.device_profiles == [BUILTIN_PROFILES["paper_pro_move"]]

    def test_profile_object_with_overrides(self, fake_home):
        cfg = Config.load(PROFILE_OBJECT_FIXTURE)
        assert len(cfg.device_profiles) == 1
        p = cfg.device_profiles[0]
        assert p.name == "paper_pro_move"
        assert p.remarkable_folder == "/News-Move"
        # other fields preserved from the built-in
        assert p.page_width_in == BUILTIN_PROFILES["paper_pro_move"].page_width_in

    def test_profiles_list(self, fake_home):
        cfg = Config.load(PROFILES_LIST_FIXTURE)
        assert len(cfg.device_profiles) == 2
        assert cfg.device_profiles[0] == BUILTIN_PROFILES["rm2"]
        assert cfg.device_profiles[1].name == "paper_pro_move"
        assert cfg.device_profiles[1].remarkable_folder == "/News-Move"

    def test_both_keys_rejected(self, fake_home, tmp_path):
        cfg_path = _write_json(
            tmp_path / "cfg.json",
            {
                "schedule_time": "05:30",
                "remarkable_folder": "/News",
                "stories": [{"provider": "rss", "config": {"rss_path": "https://x/y"}}],
                "device_profile": "rm2",
                "device_profiles": [{"name": "rm2"}],
            },
        )
        with pytest.raises(ConfigError) as ei:
            Config.load(cfg_path)
        assert "device_profile" in ei.value.message
        assert "device_profiles" in ei.value.message
        assert str(cfg_path) in ei.value.message

    def test_debug_log_emitted_when_defaulting(self, fake_home, caplog):
        caplog.set_level(logging.DEBUG, logger="renewsable.config")
        Config.load(VALID_FIXTURE)
        matching = [
            r for r in caplog.records
            if r.levelno == logging.DEBUG
            and "device profile" in r.getMessage()
            and "rm2" in r.getMessage()
        ]
        assert matching, f"expected DEBUG default-profile log, got {caplog.records!r}"

    def test_no_debug_log_when_profile_declared(self, fake_home, caplog):
        caplog.set_level(logging.DEBUG, logger="renewsable.config")
        Config.load(PROFILE_STRING_FIXTURE)
        matching = [
            r for r in caplog.records
            if r.levelno == logging.DEBUG
            and "device profile" in r.getMessage()
            and "defaulting" in r.getMessage()
        ]
        assert not matching, (
            f"expected no default-profile DEBUG log when profile declared, "
            f"got {[r.getMessage() for r in matching]!r}"
        )


# ---------------------------------------------------------------------------
# Profile-related validation errors (Task 2.2)
# ---------------------------------------------------------------------------


_BASE_VALID_PAYLOAD: dict = {
    "schedule_time": "05:30",
    "remarkable_folder": "/News",
    "stories": [{"provider": "rss", "config": {"rss_path": "https://x/y"}}],
}


def _payload_with(**extra) -> dict:
    """Return a deep-ish copy of the valid base payload merged with ``extra``."""
    data = {
        "schedule_time": _BASE_VALID_PAYLOAD["schedule_time"],
        "remarkable_folder": _BASE_VALID_PAYLOAD["remarkable_folder"],
        "stories": [dict(s) for s in _BASE_VALID_PAYLOAD["stories"]],
    }
    data.update(extra)
    return data


# Each case: (id, payload, expected substrings in the error message).
# The file path is asserted separately in the test body.
_PROFILE_VALIDATION_CASES = [
    pytest.param(
        _payload_with(device_profile="no_such_profile"),
        ["no_such_profile", "rm2", "paper_pro_move"],
        id="unknown_profile_name",
    ),
    pytest.param(
        _payload_with(
            device_profiles=[{"name": "rm2"}, {"name": "rm2"}],
        ),
        ["device_profiles", "rm2", "duplicate"],
        id="duplicate_profile_names",
    ),
    pytest.param(
        _payload_with(device_profile=42),
        ["device_profile"],
        id="non_string_profile_name",
    ),
    pytest.param(
        _payload_with(
            device_profile={"name": "rm2", "color": "yes"},
        ),
        ["color"],
        id="non_bool_color_override",
    ),
    pytest.param(
        _payload_with(
            device_profile={"name": "rm2", "remarkable_folder": 123},
        ),
        ["remarkable_folder"],
        id="non_string_remarkable_folder_override",
    ),
    pytest.param(
        _payload_with(
            device_profile={"name": "rm2", "remarkable_folder": "News-no-slash"},
        ),
        ["remarkable_folder", "/"],
        id="remarkable_folder_override_missing_leading_slash",
    ),
    pytest.param(
        _payload_with(device_profiles="rm2"),
        ["device_profiles", "list"],
        id="device_profiles_not_a_list",
    ),
    pytest.param(
        _payload_with(device_profile=42),
        ["device_profile"],
        id="device_profile_neither_string_nor_object_int",
    ),
]


@pytest.mark.parametrize("payload,expected_substrings", _PROFILE_VALIDATION_CASES)
def test_profile_validation_errors(
    fake_home, tmp_path, payload, expected_substrings
):
    """Requirement 1.4, 9.1, 9.2, 9.3, 9.4.

    Every ``ConfigError`` raised from profile validation must name the
    config file path (so operators know which file to edit) AND the
    offending key/value. The unknown-profile case additionally lists the
    supported profile names.
    """
    cfg_path = _write_json(tmp_path / "cfg.json", payload)
    with pytest.raises(ConfigError) as ei:
        Config.load(cfg_path)
    message = ei.value.message
    assert str(cfg_path) in message, (
        f"error message must name the config file path; got {message!r}"
    )
    for sub in expected_substrings:
        assert sub in message, (
            f"error message must mention {sub!r}; got {message!r}"
        )


def test_profile_validation_surfaces_before_side_effects(
    fake_home, tmp_path, monkeypatch
):
    """Requirement 9.4: validation errors surface before any fetch/upload work.

    ``Config.load`` is purely declarative (no subprocess calls), so this is
    naturally satisfied. Guard against regression by asserting that no
    ``feed_fetch`` / ``upload`` side-effect hooks would have been called;
    the simplest observable is that the error is raised before ``Config.load``
    returns, which the parametrised tests above already verify. This test
    double-checks that the exception type is ``ConfigError`` (exit 2 path)
    rather than a build/upload error that would imply later-stage failure.
    """
    cfg_path = _write_json(
        tmp_path / "cfg.json",
        _payload_with(device_profile="no_such_profile"),
    )
    with pytest.raises(ConfigError):
        Config.load(cfg_path)
