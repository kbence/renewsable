"""Unit tests for :mod:`renewsable.config`.

Design reference: the "Config" component block, the "Data Models" persistent-
file table, and the "Stories Schema (closed set, validated by ``Config.load``)"
section in ``.kiro/specs/epub-output/design.md``.

Requirements covered: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 2.4.

Each scenario probes one acceptance criterion of Requirement 1 or 2.4:

* 1.1 — load via explicit path argument.
* 1.2 — fields exposed: feeds (``stories``), reMarkable folder, schedule time,
  output directory.
* 1.3 — missing config file: error names the expected path.
* 1.4 — present file with missing or malformed required field: error names the
  field and the path.
* 1.5 — every load is fresh (no caching that would survive an edit).
* 1.6 — defaults applied for omitted optional fields.
* 2.4 — goosepaper-shaped keys (``goosepaper_bin``, ``font_size``,
  ``subprocess_timeout_s``, ``device_profile``, ``device_profiles``) are
  rejected; the Stories Schema is enforced (closed set per entry and per
  ``entry.config``, ``provider == "rss"``, http(s) ``rss_path``, optional
  positive int ``limit``).
"""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from renewsable.config import Config
from renewsable.errors import ConfigError


FIXTURES_DIR = Path(__file__).parent / "fixtures"
VALID_FIXTURE = FIXTURES_DIR / "config.valid.json"
MISSING_FIELD_FIXTURE = FIXTURES_DIR / "config.missing_field.json"
BAD_SCHEDULE_FIXTURE = FIXTURES_DIR / "config.bad_schedule.json"


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
        assert cfg.user_agent == "renewsable/0.1 (+https://github.com/bnc/renewsable)"
        assert cfg.rmapi_bin == "rmapi"
        assert cfg.feed_fetch_retries == 3
        assert cfg.feed_fetch_backoff_s == 1.0
        assert cfg.upload_retries == 3
        assert cfg.upload_backoff_s == 2.0

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
        cfg_path = _write_json(tmp_path / "cfg.json", _payload_with())
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
            tmp_path / "cfg.json", _payload_with(schedule_time=bad_value)
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
        cfg_path = _write_json(tmp_path / "cfg.json", _payload_with(foo=42))
        with pytest.raises(ConfigError) as ei:
            Config.load(cfg_path)
        assert "foo" in ei.value.message
        assert str(cfg_path) in ei.value.message


# ---------------------------------------------------------------------------
# Goosepaper / device-profile keys are rejected (Requirement 2.4)
# ---------------------------------------------------------------------------


class TestRemovedKeysRejected:
    """Requirement 2.4 — goosepaper and device-profile keys no longer accepted.

    Each removed key must produce a ``ConfigError`` whose message names both
    the offending key and the config file path, and whose remediation steers
    the operator at the closed-set list of valid keys.
    """

    @pytest.mark.parametrize(
        "removed_key,bad_value",
        [
            ("goosepaper_bin", "/usr/local/bin/goosepaper"),
            ("font_size", 14),
            ("subprocess_timeout_s", 600),
            ("device_profile", "rm2"),
            ("device_profiles", [{"name": "rm2"}]),
        ],
    )
    def test_removed_key_rejected(self, fake_home, tmp_path, removed_key, bad_value):
        cfg_path = _write_json(
            tmp_path / "cfg.json", _payload_with(**{removed_key: bad_value})
        )
        with pytest.raises(ConfigError) as ei:
            Config.load(cfg_path)
        message = ei.value.message
        assert removed_key in message, (
            f"error must name the offending key {removed_key!r}; got {message!r}"
        )
        assert str(cfg_path) in message, (
            f"error must name the config file path; got {message!r}"
        )

    def test_dataclass_no_longer_carries_removed_fields(self):
        names = set(Config.__dataclass_fields__.keys())
        for removed in (
            "goosepaper_bin",
            "font_size",
            "subprocess_timeout_s",
            "device_profiles",
        ):
            assert removed not in names, (
                f"Config dataclass still carries removed field {removed!r}"
            )


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
            tmp_path / "cfg.json", _payload_with(remarkable_folder=42)
        )
        with pytest.raises(ConfigError) as ei:
            Config.load(cfg_path)
        assert "remarkable_folder" in ei.value.message

    def test_remarkable_folder_must_start_with_slash(self, fake_home, tmp_path):
        cfg_path = _write_json(
            tmp_path / "cfg.json", _payload_with(remarkable_folder="News")
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
        cfg_path = _write_json(tmp_path / "cfg.json", _payload_with())
        first = Config.load(cfg_path)
        assert first.schedule_time == "05:30"

        _write_json(cfg_path, _payload_with(schedule_time="07:45"))
        second = Config.load(cfg_path)
        assert second.schedule_time == "07:45"


# ---------------------------------------------------------------------------
# Path expansion / log_dir override
# ---------------------------------------------------------------------------


class TestPathExpansion:
    def test_log_dir_override_is_expanded(self, fake_home, tmp_path):
        cfg_path = _write_json(
            tmp_path / "cfg.json", _payload_with(log_dir="~/some/logs")
        )
        cfg = Config.load(cfg_path)
        assert cfg.log_dir == fake_home / "some" / "logs"
        assert cfg.log_dir.is_absolute()

    def test_relative_output_dir_resolved_to_absolute(self, fake_home, tmp_path):
        cfg_path = _write_json(
            tmp_path / "cfg.json", _payload_with(output_dir="relative/out")
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
# Stories Schema (Requirement 2.4 + design.md "Stories Schema (closed set,
# validated by Config.load)")
# ---------------------------------------------------------------------------


class TestStoriesSchemaHappyPath:
    def test_minimal_entry_loads(self, fake_home, tmp_path):
        cfg_path = _write_json(
            tmp_path / "cfg.json",
            _payload_with(
                stories=[
                    {
                        "provider": "rss",
                        "config": {"rss_path": "https://example.com/feed.xml"},
                    }
                ]
            ),
        )
        cfg = Config.load(cfg_path)
        assert cfg.stories[0]["config"]["rss_path"] == "https://example.com/feed.xml"

    def test_entry_with_optional_limit_loads(self, fake_home, tmp_path):
        cfg_path = _write_json(
            tmp_path / "cfg.json",
            _payload_with(
                stories=[
                    {
                        "provider": "rss",
                        "config": {
                            "rss_path": "https://example.com/feed.xml",
                            "limit": 5,
                        },
                    }
                ]
            ),
        )
        cfg = Config.load(cfg_path)
        assert cfg.stories[0]["config"]["limit"] == 5

    def test_http_scheme_accepted(self, fake_home, tmp_path):
        cfg_path = _write_json(
            tmp_path / "cfg.json",
            _payload_with(
                stories=[
                    {
                        "provider": "rss",
                        "config": {"rss_path": "http://example.com/feed.xml"},
                    }
                ]
            ),
        )
        cfg = Config.load(cfg_path)
        assert cfg.stories[0]["config"]["rss_path"].startswith("http://")


class TestStoriesSchemaRejections:
    """Each violation must name the file path and the offending key/value."""

    def _expect_error(self, fake_home, tmp_path, stories, *, must_contain):
        cfg_path = _write_json(tmp_path / "cfg.json", _payload_with(stories=stories))
        with pytest.raises(ConfigError) as ei:
            Config.load(cfg_path)
        message = ei.value.message
        assert str(cfg_path) in message, (
            f"error must name config file path; got {message!r}"
        )
        for sub in must_contain:
            assert sub in message, (
                f"error must mention {sub!r}; got {message!r}"
            )
        return ei.value

    def test_rejects_unknown_entry_top_level_key(self, fake_home, tmp_path):
        self._expect_error(
            fake_home,
            tmp_path,
            stories=[
                {
                    "provider": "rss",
                    "config": {"rss_path": "https://x/y"},
                    "style": "newspaper",
                }
            ],
            must_contain=["style", "stories[0]"],
        )

    def test_rejects_unknown_config_level_key(self, fake_home, tmp_path):
        self._expect_error(
            fake_home,
            tmp_path,
            stories=[
                {
                    "provider": "rss",
                    "config": {"rss_path": "https://x/y", "font_size": 14},
                }
            ],
            must_contain=["font_size", "stories[0].config"],
        )

    def test_rejects_provider_not_rss(self, fake_home, tmp_path):
        self._expect_error(
            fake_home,
            tmp_path,
            stories=[
                {"provider": "reddit", "config": {"rss_path": "https://x/y"}}
            ],
            must_contain=["provider", "reddit"],
        )

    @pytest.mark.parametrize(
        "rss_path",
        ["file:///tmp/feed.xml", "ftp://example.com/feed.xml", "example.com/feed.xml", ""],
    )
    def test_rejects_non_http_rss_path(self, fake_home, tmp_path, rss_path):
        self._expect_error(
            fake_home,
            tmp_path,
            stories=[{"provider": "rss", "config": {"rss_path": rss_path}}],
            must_contain=["rss_path"],
        )

    def test_rejects_missing_rss_path(self, fake_home, tmp_path):
        self._expect_error(
            fake_home,
            tmp_path,
            stories=[{"provider": "rss", "config": {}}],
            must_contain=["rss_path", "stories[0].config"],
        )

    def test_rejects_missing_provider(self, fake_home, tmp_path):
        self._expect_error(
            fake_home,
            tmp_path,
            stories=[{"config": {"rss_path": "https://x/y"}}],
            must_contain=["provider", "stories[0]"],
        )

    def test_rejects_missing_config(self, fake_home, tmp_path):
        self._expect_error(
            fake_home,
            tmp_path,
            stories=[{"provider": "rss"}],
            must_contain=["config", "stories[0]"],
        )

    def test_rejects_non_dict_config(self, fake_home, tmp_path):
        self._expect_error(
            fake_home,
            tmp_path,
            stories=[{"provider": "rss", "config": "https://x/y"}],
            must_contain=["stories[0].config"],
        )

    @pytest.mark.parametrize("bad_limit", [0, -1, True, "5"])
    def test_rejects_bad_limit(self, fake_home, tmp_path, bad_limit):
        self._expect_error(
            fake_home,
            tmp_path,
            stories=[
                {
                    "provider": "rss",
                    "config": {"rss_path": "https://x/y", "limit": bad_limit},
                }
            ],
            must_contain=["limit"],
        )

    def test_rejects_non_dict_entry(self, fake_home, tmp_path):
        self._expect_error(
            fake_home,
            tmp_path,
            stories=["https://x/y"],
            must_contain=["stories[0]"],
        )
