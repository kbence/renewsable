"""Tests for the renewsable.errors exception hierarchy (Task 1.2).

Covers design "Error Handling": RenewsableError base with five orchestration
subclasses, each carrying an optional remediation hint surfaced in str().
"""

from __future__ import annotations

import pytest

from renewsable.errors import (
    BuildError,
    ConfigError,
    PairingError,
    RenewsableError,
    ScheduleError,
    UploadError,
)


ALL_SUBCLASSES = [ConfigError, BuildError, UploadError, PairingError, ScheduleError]


def test_base_is_exception():
    assert issubclass(RenewsableError, Exception)


@pytest.mark.parametrize("cls", ALL_SUBCLASSES)
def test_subclass_inherits_from_base(cls):
    assert issubclass(cls, RenewsableError)
    # And still an Exception (transitively).
    assert issubclass(cls, Exception)


@pytest.mark.parametrize("cls", ALL_SUBCLASSES)
def test_subclass_importable_by_name(cls):
    # The parametrize import above would fail if any name were missing; this
    # just makes the "observable" check explicit per the task text.
    assert cls.__name__ in {
        "ConfigError",
        "BuildError",
        "UploadError",
        "PairingError",
        "ScheduleError",
    }


def test_base_message_only_str_contains_message():
    exc = RenewsableError("something broke")
    assert "something broke" in str(exc)


def test_base_with_remediation_str_contains_both():
    exc = RenewsableError("something broke", remediation="try turning it off")
    rendered = str(exc)
    assert "something broke" in rendered
    assert "try turning it off" in rendered


def test_base_stores_message_and_remediation_attributes():
    exc = RenewsableError("msg", remediation="fix it")
    assert exc.message == "msg"
    assert exc.remediation == "fix it"


def test_base_remediation_defaults_to_none():
    exc = RenewsableError("msg")
    assert exc.remediation is None
    assert exc.message == "msg"


def test_remediation_is_keyword_only():
    # Positional second arg should not silently land in `remediation`; it must
    # be passed as a keyword so the call-site is explicit. This also protects
    # us against signature drift.
    with pytest.raises(TypeError):
        RenewsableError("msg", "fix it")  # type: ignore[misc]


def test_config_error_message_and_remediation_in_str():
    # The exact acceptance example from tasks.md.
    exc = ConfigError("x", remediation="y")
    rendered = str(exc)
    assert "x" in rendered
    assert "y" in rendered
    assert exc.message == "x"
    assert exc.remediation == "y"


@pytest.mark.parametrize("cls", ALL_SUBCLASSES)
def test_every_subclass_roundtrips_message_and_remediation(cls):
    exc = cls("boom", remediation="do X")
    rendered = str(exc)
    assert "boom" in rendered
    assert "do X" in rendered
    assert exc.message == "boom"
    assert exc.remediation == "do X"


@pytest.mark.parametrize("cls", ALL_SUBCLASSES)
def test_every_subclass_message_only(cls):
    exc = cls("boom")
    assert "boom" in str(exc)
    assert exc.remediation is None


@pytest.mark.parametrize("cls", ALL_SUBCLASSES)
def test_every_subclass_is_raiseable(cls):
    with pytest.raises(cls) as ei:
        raise cls("nope", remediation="retry later")
    assert ei.value.message == "nope"
    assert ei.value.remediation == "retry later"
    # Also catchable as the base.
    with pytest.raises(RenewsableError):
        raise cls("nope")
