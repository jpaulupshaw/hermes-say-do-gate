"""Test fail-open decorator: exceptions swallowed, exceptions logged, return values pass through."""
import logging
import pytest


def test_raising_function_returns_none():
    from _failopen import fail_open

    @fail_open
    def broken(**kwargs):
        raise ValueError("boom")

    result = broken(tool_name="x")
    assert result is None


def test_raising_function_logs_warning(caplog):
    from _failopen import fail_open

    @fail_open
    def broken(hook_name="test_hook", **kwargs):
        raise RuntimeError("failure")

    with caplog.at_level(logging.WARNING, logger="say_do_gate"):
        broken(hook_name="test_hook")
    assert any("test_hook" in r.message for r in caplog.records)
    assert any("RuntimeError" in r.message for r in caplog.records)


def test_normal_function_passes_return_value():
    from _failopen import fail_open

    @fail_open
    def works(**kwargs):
        return "ok"

    assert works() == "ok"


def test_keyboard_interrupt_propagates():
    from _failopen import fail_open

    @fail_open
    def interruptible(**kwargs):
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        interruptible()


def test_preserves_function_name():
    from _failopen import fail_open

    @fail_open
    def my_special_handler(**kwargs):
        pass

    assert my_special_handler.__name__ == "my_special_handler"
