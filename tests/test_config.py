"""Test config.resolve_mode: normalize and validate mode strings."""
import logging
import pytest


def test_default_returns_observe():
    from config import resolve_mode
    assert resolve_mode(None) == "observe"


def test_string_observe_returns_observe():
    from config import resolve_mode
    assert resolve_mode("observe") == "observe"


def test_string_annotate_returns_annotate():
    from config import resolve_mode
    assert resolve_mode("annotate") == "annotate"


def test_whitespace_and_case_normalized():
    from config import resolve_mode
    assert resolve_mode(" Annotate ") == "annotate"
    assert resolve_mode("OBSERVE") == "observe"
    assert resolve_mode(" OBSERVE ") == "observe"
    assert resolve_mode("ANNOTATE") == "annotate"


def test_unknown_string_returns_observe_with_warning(caplog):
    from config import resolve_mode
    with caplog.at_level(logging.WARNING, logger="say_do_gate"):
        result = resolve_mode("bogus")
    assert result == "observe"
    assert any("bogus" in r.message for r in caplog.records)


def test_non_string_returns_observe_with_warning(caplog):
    from config import resolve_mode
    with caplog.at_level(logging.WARNING, logger="say_do_gate"):
        result = resolve_mode(42)
    assert result == "observe"

    with caplog.at_level(logging.WARNING, logger="say_do_gate"):
        result = resolve_mode(["annotate"])
    assert result == "observe"
