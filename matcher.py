"""Say-Do Gate claim-to-receipt matcher.

Takes a list of Claims and a ledger window and returns one Verdict per claim.
Duck-types claims and receipts (no imports from claims.py or ledger.py).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# --- Receipt category detectors ---

# Word-bounded, case-insensitive. Boundaries: not preceded or followed by [\w./-].
_TEST_PATTERNS = [
    r"(?<![\w./-])pytest(?![\w./-])",
    r"(?<![\w./-])py\.test(?![\w./-])",
    # any python launcher: python, python3, python3.12, .venv/bin/python, with flags before -m (python -I -m ...)
    r"(?<![\w.-])(?:\S*/)?python[\d.]*(?:\s+-[A-Za-z]+)*\s+-m\s+(?:pytest|unittest)(?![\w.-])",
    r"(?<![\w./-])tox(?![\w./-])",
    r"(?<![\w./-])nox(?![\w./-])",
    r"(?<![\w./-])npm\s+(?:run\s+)?test(?![\w./-])",
    r"(?<![\w./-])yarn\s+test(?![\w./-])",
    r"(?<![\w./-])pnpm\s+test(?![\w./-])",
    r"(?<![\w./-])go\s+test(?![\w./-])",
    r"(?<![\w./-])cargo\s+test(?![\w./-])",
    r"(?<![\w./-])mvn\s+test(?![\w./-])",
    r"(?<![\w./-])gradle\s+test(?![\w./-])",
    r"(?<![\w./-])\./gradlew\s+test(?![\w./-])",
    r"(?<![\w./-])make\s+test(?![\w./-])",
    r"(?<![\w./-])jest(?![\w./-])",
    r"(?<![\w./-])vitest(?![\w./-])",
    r"(?<![\w./-])rspec(?![\w./-])",
    r"(?<![\w./-])phpunit(?![\w./-])",
    r"(?<![\w./-])ctest(?![\w./-])",
    r"(?<![\w./-])dotnet\s+test(?![\w./-])",
]

_TEST_RE = re.compile("|".join(_TEST_PATTERNS), re.IGNORECASE)

_DEPLOY_PATTERNS = [
    r"(?<![\w./-])deploy(?![\w./-])",
    r"(?<![\w./-])migrate(?![\w./-])",
    r"(?<![\w./-])migration(?![\w./-])",
    r"(?<![\w./-])alembic\s+upgrade(?![\w./-])",
    r"(?<![\w./-])flyway(?![\w./-])",
    r"(?<![\w./-])liquibase(?![\w./-])",
    r"(?<![\w./-])kubectl\s+(?:apply|rollout)(?![\w./-])",
    r"(?<![\w./-])helm\s+(?:upgrade|install)(?![\w./-])",
    r"(?<![\w./-])systemctl\s+(?:--user\s+)?(?:restart|start|reload)(?![\w./-])",
    r"(?<![\w./-])docker\s+(?:compose\s+)?(?:up|restart)(?![\w./-])",
    r"(?<![\w./-])docker-compose\s+(?:up|restart)(?![\w./-])",
    r"(?<![\w./-])terraform\s+apply(?![\w./-])",
    r"(?<![\w./-])ansible-playbook(?![\w./-])",
    r"(?<![\w./-])git\s+push(?![\w./-])",
]

_DEPLOY_RE = re.compile("|".join(_DEPLOY_PATTERNS), re.IGNORECASE)


def is_test_command(cmd: str | None) -> bool:
    """Check if a terminal command runs a test suite."""
    if not isinstance(cmd, str) or not cmd:
        return False
    return _TEST_RE.search(cmd) is not None


def _is_deploy_command(cmd: str | None) -> bool:
    """Check if a terminal command is a deploy/migration."""
    if not isinstance(cmd, str) or not cmd:
        return False
    return _DEPLOY_RE.search(cmd) is not None


# --- Verdict ---

@dataclass(frozen=True)
class Verdict:
    claim: object
    severity: str | None
    label: str
    evidence: str
    phrase: str


# --- Helpers ---

def _truncate(s: str, max_len: int = 60) -> str:
    """Truncate a command for evidence strings."""
    if not isinstance(s, str):
        return ""
    if len(s) <= max_len:
        return s
    return s[:max_len - 1] + "…"


def _is_terminal(r) -> bool:
    return getattr(r, "tool_name", None) == "terminal"


def _is_write(r) -> bool:
    return getattr(r, "tool_name", None) in ("write_file", "patch")


def _foreground(receipts) -> list:
    return [r for r in receipts if not getattr(r, "background", False)]


def _has_bg_matching(receipts, matcher) -> bool:
    return any(getattr(r, "background", False) and matcher(r) for r in receipts)


def _find_decider(window, matcher):
    """
    Resolution order:
    1. Current turn (age 0): last matching foreground receipt.
    2. Lookback -1, -2, -3: first turn with matching fg receipt, its last.
    3. Else: matching bg receipt anywhere -> return ('bg', None).
    4. Else: return ('none', None).

    Returns (kind, receipt_or_None, age).
      kind='current'  -> (receipt, 0)
      kind='lookback' -> (receipt, age)
      kind='bg'       -> (None, None)
      kind='none'     -> (None, None)
    """
    # Current turn
    if window:
        age0 = window[0][1]
        matching_fg = [r for r in _foreground(age0) if matcher(r)]
        if matching_fg:
            return ("current", matching_fg[-1], 0)

    # Lookback
    for i in range(1, len(window)):
        age, recs = window[i]
        matching_fg = [r for r in _foreground(recs) if matcher(r)]
        if matching_fg:
            return ("lookback", matching_fg[-1], age)

    # Background anywhere
    for age, recs in window:
        if _has_bg_matching(recs, matcher):
            return ("bg", None, None)

    return ("none", None, None)


def _label(age: int) -> str:
    if age == 0:
        return "backed (turn 0)"
    return f"backed (turn {age})"


def _failed_evidence(cmd, exit_code, age=0) -> str:
    """Evidence for a failed command."""
    truncated = _truncate(cmd or "command")
    suffix = f" in turn {age}" if age < 0 else ""
    if exit_code is not None:
        return f"`{truncated}` exited {exit_code}{suffix}"
    return f"`{truncated}` failed{suffix}"


# --- Per-kind matchers ---

def _match_tests_pass(claim, window):
    count = getattr(claim, "count", None)
    phrase = f"{count} tests pass" if count else "tests pass"

    def matcher(r):
        return _is_terminal(r) and is_test_command(getattr(r, "command", None))

    kind, decider, age = _find_decider(window, matcher)

    if kind == "none":
        return Verdict(claim, "medium", "unbacked", "0 test commands run", phrase)

    if kind == "bg":
        return Verdict(claim, "low", "unverifiable", "started in background, result not seen", phrase)

    cmd = getattr(decider, "command", None) or "test"

    # Failed decider
    if getattr(decider, "status", None) == "error":
        exit_code = getattr(decider, "exit_code", None)
        evidence = _failed_evidence(cmd, exit_code, age)
        return Verdict(claim, "high", "contradicted", evidence, phrase)

    # Successful decider: check count if set
    if count is not None:
        output = getattr(decider, "output_tail", "") or ""
        passed_matches = list(re.finditer(r"(\d+)\s+passed", output))
        failed_matches = list(re.finditer(r"(\d+)\s+failed", output))
        errors_matches = list(re.finditer(r"(\d+)\s+errors?", output, re.IGNORECASE))

        parsed_passed = int(passed_matches[-1].group(1)) if passed_matches else None
        parsed_failed = int(failed_matches[-1].group(1)) if failed_matches else 0
        parsed_errors = int(errors_matches[-1].group(1)) if errors_matches else 0

        if parsed_failed > 0 or parsed_errors > 0:
            n = parsed_failed if parsed_failed > 0 else parsed_errors
            truncated = _truncate(cmd)
            return Verdict(claim, "high", "contradicted", f"`{truncated}` reported {n} failed", phrase)

        if parsed_passed is not None and parsed_passed != count:
            return Verdict(claim, "high", "contradicted", f"ledger shows {parsed_passed} passed, not {count}", phrase)

        if parsed_passed is None and parsed_failed == 0 and parsed_errors == 0:
            # Nothing parseable
            return Verdict(claim, "low", "unverifiable", "count not in output", phrase)

    # Supported
    return Verdict(claim, None, _label(age), "", phrase)


def _match_tests_ran(claim, window):
    phrase = "ran the tests"

    def matcher(r):
        return _is_terminal(r) and is_test_command(getattr(r, "command", None))

    kind, decider, age = _find_decider(window, matcher)

    if kind == "none":
        return Verdict(claim, "medium", "unbacked", "0 test commands run", phrase)

    if kind == "bg":
        return Verdict(claim, "low", "unverifiable", "started in background, result not seen", phrase)

    # Any decider supports (ok or failed)
    return Verdict(claim, None, _label(age), "", phrase)


def _match_ran_command(claim, window):
    target = getattr(claim, "target", None)

    if target:
        phrase = f"ran `{target}`"
        target_norm = " ".join(target.split())

        def matcher(r):
            if not _is_terminal(r):
                return False
            cmd = getattr(r, "command", None)
            if not isinstance(cmd, str):
                return False
            cmd_norm = " ".join(cmd.split())
            return target_norm in cmd_norm
    else:
        phrase = "ran a command"

        def matcher(r):
            return _is_terminal(r)

    kind, decider, age = _find_decider(window, matcher)

    if kind == "none":
        if target:
            return Verdict(claim, "medium", "unbacked", f"no command matching `{target}` ran", phrase)
        return Verdict(claim, "medium", "unbacked", "0 commands ran", phrase)

    if kind == "bg":
        return Verdict(claim, "low", "unverifiable", "started in background, result not seen", phrase)

    # Any decider supports
    return Verdict(claim, None, _label(age), "", phrase)


def _file_matches_target(file_path: str, target: str) -> bool:
    """Check if a file path matches a target (exact or path-boundary suffix)."""
    if file_path == target:
        return True
    # Suffix must be on a path boundary
    suffix = "/" + target.lstrip("./")
    return file_path.endswith(suffix)


def _match_file_written(claim, window):
    target = getattr(claim, "target", None)

    if target:
        phrase = f"wrote {target}"

        def matcher(r):
            if not _is_write(r):
                return False
            files = getattr(r, "files", ())
            return any(_file_matches_target(f, target) for f in files)
    else:
        phrase = "wrote a file"

        def matcher(r):
            # A write receipt with no path (malformed/phantom call: "missing required field 'path'") is not
            # evidence about any file, for or against. Ignore it.
            return _is_write(r) and bool(getattr(r, "files", ()))

    kind, decider, age = _find_decider(window, matcher)

    if kind == "none":
        # Check for shell redirect in current turn
        if target and window:
            age0 = window[0][1]
            for r in age0:
                if _is_terminal(r):
                    cmd = getattr(r, "command", None)
                    if isinstance(cmd, str) and target in cmd:
                        return Verdict(claim, "low", "unverifiable", "written via shell, not checked", phrase)
        if target:
            return Verdict(claim, "medium", "unbacked", f"no write or patch receipt for {target}", phrase)
        return Verdict(claim, "medium", "unbacked", "0 file writes", phrase)

    if kind == "bg":
        return Verdict(claim, "low", "unverifiable", "started in background, result not seen", phrase)

    # Failed decider
    if getattr(decider, "status", None) == "error":
        tool = getattr(decider, "tool_name", "write")
        files = getattr(decider, "files", ())
        file_ref = target or (files[0] if files else "file")
        return Verdict(claim, "high", "contradicted", f"{tool} on {file_ref} failed", phrase)

    # Success
    return Verdict(claim, None, _label(age), "", phrase)


def _match_deployed(claim, window):
    phrase = "deployed"

    def matcher(r):
        return _is_terminal(r) and _is_deploy_command(getattr(r, "command", None))

    kind, decider, age = _find_decider(window, matcher)

    if kind == "none":
        return Verdict(claim, "medium", "unbacked", "0 deploy or migration commands run", phrase)

    if kind == "bg":
        return Verdict(claim, "low", "unverifiable", "started in background, result not seen", phrase)

    # Failed decider
    if getattr(decider, "status", None) == "error":
        cmd = getattr(decider, "command", None)
        exit_code = getattr(decider, "exit_code", None)
        truncated = _truncate(cmd or "deploy")
        suffix = f" in turn {age}" if age < 0 else ""
        if exit_code is not None:
            evidence = f"`{truncated}` exited {exit_code}{suffix}"
        else:
            evidence = f"`{truncated}` failed{suffix}"
        return Verdict(claim, "high", "contradicted", evidence, phrase)

    return Verdict(claim, None, _label(age), "", phrase)


def _match_verified(claim, window):
    phrase = "verified"

    def matcher(r):
        return True  # Any receipt of any tool

    kind, decider, age = _find_decider(window, matcher)

    if kind == "none":
        return Verdict(claim, "medium", "unbacked", "0 tool calls", phrase)

    if kind == "bg":
        return Verdict(claim, "low", "unverifiable", "started in background, result not seen", phrase)

    # Failed decider -> low unverifiable
    if getattr(decider, "status", None) == "error":
        return Verdict(claim, "low", "unverifiable", "last check failed", phrase)

    return Verdict(claim, None, _label(age), "", phrase)


def _match_underway_or_done(claim, window):
    kind_name = getattr(claim, "kind", "")
    if kind_name == "done":
        phrase = "done"
    else:
        phrase = "work underway"

    # Current turn only, no lookback
    if window:
        age0 = window[0][1]
        if age0:
            # Any receipt supports (any tool, any status, background ok)
            return Verdict(claim, None, "backed (turn 0)", "", phrase)

    return Verdict(claim, "medium", "unbacked", "0 tool calls this turn", phrase)


def _match_vague(claim, window):
    return Verdict(claim, "low", "unverifiable", "claim too vague to check", "fixed")


# --- Dispatch ---

_KIND_HANDLERS = {
    "tests_pass": _match_tests_pass,
    "tests_ran": _match_tests_ran,
    "ran_command": _match_ran_command,
    "file_written": _match_file_written,
    "deployed": _match_deployed,
    "verified": _match_verified,
    "underway": _match_underway_or_done,
    "done": _match_underway_or_done,
    "vague": _match_vague,
}


def match(claims: list, window: list[tuple[int, list]]) -> list[Verdict]:
    """Match each claim against the ledger window. Never raises."""
    if not claims:
        return []
    return [_match_one(c, window) for c in claims]


def _match_one(claim, window) -> Verdict:
    try:
        kind = getattr(claim, "kind", None)
        handler = _KIND_HANDLERS.get(kind)
        if handler is None:
            return Verdict(claim, "low", "unverifiable", "matcher error", "")
        return handler(claim, window)
    except Exception:
        # Never raise; wrap any per-claim error
        return Verdict(claim, "low", "unverifiable", "matcher error", "")
