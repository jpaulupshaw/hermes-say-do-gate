"""Orchestrator review regressions for matcher.py (found replaying the private corpus, not covered by the oracle)."""
import json

import pytest

from claims import Claim
from ledger import Ledger
from matcher import is_test_command, match


@pytest.mark.parametrize("cmd", [
    "python3 -m unittest discover -s scripts -p 'test_*.py'",
    "python3.12 -m pytest -q",
    "python -I -m pytest tests/",
    ".venv/bin/python -m unittest -v",
    "uv run pytest -q",
])
def test_python_launcher_variants_are_test_commands(cmd):
    assert is_test_command(cmd)


@pytest.mark.parametrize("cmd", ["python3 -m http.server", "python3 -m unittest_helpers_not_a_runner"])
def test_non_runner_modules_are_not_test_commands(cmd):
    assert not is_test_command(cmd)


def _window(*receipts):
    lg = Ledger(lookback_turns=3, max_sessions=4)
    lg.begin_turn(session_id="s", turn_id="t1", user_message="draw it")
    for tool, args, result in receipts:
        lg.record(tool_name=tool, args=args, result=json.dumps(result), session_id="s", turn_id="t1")
    return lg.window(session_id="s", turn_id="t1")


def test_phantom_pathless_write_failure_does_not_contradict_real_write():
    # Provider emits the real call and a phantom duplicate with arguments {}; Hermes executes both.
    w = _window(("write_file", {"path": "/w/pelican.svg", "content": "<svg/>"},
                 {"bytes_written": 6, "files_modified": ["/w/pelican.svg"]}),
                ("write_file", {}, {"error": "write_file: missing required field 'path'."}))
    (v,) = match([Claim(kind="file_written", text="Created the SVG:")], w)
    assert v.severity is None


def test_pathless_write_failure_alone_is_not_a_contradiction():
    w = _window(("write_file", {}, {"error": "write_file: missing required field 'path'."}))
    (v,) = match([Claim(kind="file_written", text="Created the SVG:")], w)
    assert v.severity != "high"
