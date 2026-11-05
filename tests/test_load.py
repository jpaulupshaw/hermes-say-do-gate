"""Integration tests: real upstream plugin discovery in isolated HERMES_HOME."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
UPSTREAM = Path((Path(os.environ["SAYDO_UPSTREAM"]) if "SAYDO_UPSTREAM" in os.environ else PLUGIN_ROOT.parent / "upstream"))

# Files to copy into the temp plugin dir: plugin.yaml + every root-level .py (picks up new modules)
PLUGIN_FILES = ["plugin.yaml", *sorted(p.name for p in PLUGIN_ROOT.glob("*.py"))]


def _run_discovery(hermes_home: Path, plugin_dir: Path, config_yaml: str) -> subprocess.CompletedProcess:
    """Write config, copy plugin, run discovery in a subprocess."""
    # Write config.yaml
    config_path = hermes_home / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(config_yaml)

    # Copy plugin files
    dest = hermes_home / "plugins" / "say-do-gate"
    dest.mkdir(parents=True, exist_ok=True)
    for fname in PLUGIN_FILES:
        src = PLUGIN_ROOT / fname
        if src.exists():
            shutil.copy2(src, dest / fname)

    # Run discovery in subprocess to isolate plugin state
    code = (
        "import sys; sys.path.insert(0,'{upstream}'); "
        "from hermes_cli.plugins import discover_plugins, get_plugin_manager, has_hook; "
        "discover_plugins(force=True); m=get_plugin_manager(); "
        "print(has_hook('post_tool_call'), has_hook('pre_llm_call'), has_hook('transform_llm_output'), m.holds_stream(), "
        "getattr(m._plugins.get('say-do-gate'), 'enabled', False))"
    ).format(upstream=UPSTREAM)

    return subprocess.run(
        [sys.executable, "-I", "-c", code],
        env={**os.environ, "HERMES_HOME": str(hermes_home)},
        capture_output=True,
        text=True,
        timeout=30,
    )


def parse_discovery_output(proc):
    """Parse 'True True True False' style output into bools."""
    if proc.returncode != 0:
        raise AssertionError(f"discovery subprocess failed (rc={proc.returncode}):\n{proc.stderr}")
    parts = proc.stdout.strip().split()
    return [p == "True" for p in parts]


def test_enabled_no_mode_all_hooks_no_hold(tmp_path):
    """Plugin enabled, no mode setting -> all 3 hooks, no stream hold."""
    config = """
plugins:
  enabled:
    - say-do-gate
"""
    result = _run_discovery(tmp_path, PLUGIN_ROOT, config)
    has_post, has_pre, has_transform, holds, enabled = parse_discovery_output(result)
    assert has_post, f"post_tool_call not registered (stderr: {result.stderr})"
    assert has_pre, f"pre_llm_call not registered (stderr: {result.stderr})"
    assert has_transform, f"transform_llm_output not registered (stderr: {result.stderr})"
    assert not holds, "stream should not be held in default (observe) mode"
    assert enabled, "say-do-gate not loaded+enabled"


def test_enabled_mode_annotate_holds_stream(tmp_path):
    """Plugin enabled with mode=annotate -> all 3 hooks, stream held."""
    config = """
plugins:
  enabled:
    - say-do-gate
  entries:
    say-do-gate:
      settings:
        mode: annotate
"""
    result = _run_discovery(tmp_path, PLUGIN_ROOT, config)
    has_post, has_pre, has_transform, holds, enabled = parse_discovery_output(result)
    assert has_post, f"post_tool_call not registered (stderr: {result.stderr})"
    assert has_pre, f"pre_llm_call not registered (stderr: {result.stderr})"
    assert has_transform, f"transform_llm_output not registered (stderr: {result.stderr})"
    assert holds, "stream should be held in annotate mode"
    assert enabled, "say-do-gate not loaded+enabled"


def test_enabled_mode_observe_no_hold(tmp_path):
    """Plugin enabled with mode=observe -> all 3 hooks, no stream hold."""
    config = """
plugins:
  enabled:
    - say-do-gate
  entries:
    say-do-gate:
      settings:
        mode: observe
"""
    result = _run_discovery(tmp_path, PLUGIN_ROOT, config)
    has_post, has_pre, has_transform, holds, enabled = parse_discovery_output(result)
    assert has_post, f"post_tool_call not registered (stderr: {result.stderr})"
    assert has_pre, f"pre_llm_call not registered (stderr: {result.stderr})"
    assert has_transform, f"transform_llm_output not registered (stderr: {result.stderr})"
    assert not holds, "stream should not be held in observe mode"
    assert enabled, "say-do-gate not loaded+enabled"


def test_not_enabled_no_hooks(tmp_path):
    """Plugin not enabled -> none of the 3 hooks registered."""
    config = """
plugins:
  enabled: []
"""
    result = _run_discovery(tmp_path, PLUGIN_ROOT, config)
    has_post, has_pre, has_transform, holds, enabled = parse_discovery_output(result)
    assert not has_post, "post_tool_call should not be registered when plugin is not enabled"
    assert not has_pre, "pre_llm_call should not be registered when plugin is not enabled"
    assert not has_transform, "transform_llm_output should not be registered when plugin is not enabled"
    assert not holds, "stream should not be held when plugin is not enabled"
    assert not enabled, "say-do-gate must not be enabled"


def test_doctor_exits_zero(tmp_path):
    """hermes plugins doctor exits 0 on the plugin root."""
    proc = subprocess.run(
        [sys.executable, "-I", "-m", "hermes_cli.main", "plugins", "doctor", str(PLUGIN_ROOT), "--ci"],
        env={**os.environ, "HERMES_HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"doctor failed (rc={proc.returncode}):\nstdout: {proc.stdout}\nstderr: {proc.stderr}"


def test_uses_upstream_not_live_install(tmp_path):
    """Guard: discovery subprocess must import hermes from the isolated upstream, not ~/.hermes."""
    config = """
plugins:
  enabled:
    - say-do-gate
"""
    # Verify config file gets written and plugin copied
    config_path = tmp_path / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(config)
    dest = tmp_path / "plugins" / "say-do-gate"
    dest.mkdir(parents=True, exist_ok=True)

    # Run a subprocess that prints where hermes_cli is loaded from
    # No sys.path insert: this proves the venv's own install resolves to the isolated upstream.
    code = "import hermes_cli; print(hermes_cli.__file__)"

    proc = subprocess.run(
        [sys.executable, "-I", "-c", code],
        env={**os.environ, "HERMES_HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 0, f"subprocess failed: {proc.stderr}"
    hermes_cli_path = proc.stdout.strip()
    assert hermes_cli_path.startswith(str(UPSTREAM)), (
        f"hermes_cli loaded from {hermes_cli_path}, expected under {UPSTREAM}. "
        "The isolated upstream is not being used!"
    )
