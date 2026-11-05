"""Tests for matcher.match using FakeClaim/FakeReceipt (no claims.py/ledger.py dependency)."""
import pytest

from matcher import Verdict, is_test_command, match


class FakeClaim:
    def __init__(self, kind, text, count=None, target=None, low=False):
        self.kind = kind
        self.text = text
        self.count = count
        self.target = target
        self.low = low

    def __eq__(self, other):
        if not isinstance(other, FakeClaim):
            return NotImplemented
        return (self.kind == other.kind and self.text == other.text
                and self.count == other.count and self.target == other.target)


class FakeReceipt:
    def __init__(self, tool_name, status="ok", exit_code=None, output_tail="",
                 command=None, background=False, files=(), error_type=None,
                 error_message=None, args=None, seq=0, session_id="", turn_key=""):
        self.tool_name = tool_name
        self.status = status
        self.exit_code = exit_code
        self.output_tail = output_tail
        self.command = command
        self.background = background
        self.files = files
        self.error_type = error_type
        self.error_message = error_message
        self.args = args or {}
        self.seq = seq
        self.session_id = session_id
        self.turn_key = turn_key


def T(cmd, code=0, out="", bg=False):
    return FakeReceipt("terminal", status="error" if code else "ok", exit_code=code,
                       output_tail=out, command=cmd, background=bg)


def W(path, ok=True, tool="write_file"):
    if ok:
        return FakeReceipt(tool, status="ok", files=(path,))
    return FakeReceipt(tool, status="error", error_message="permission denied", files=(path,))


def R(tool, **kw):
    return FakeReceipt(tool, **kw)


def one(claim, window):
    (v,) = match([claim], window)
    assert isinstance(v, Verdict) and v.claim == claim
    return v


TP = FakeClaim(kind="tests_pass", text="All tests pass.")


# --- is_test_command ---

@pytest.mark.parametrize("cmd,expected", [
    ("pytest -q", True),
    ("python -m pytest tests/", True),
    ("cd x && npm run test", True),
    ("go test ./...", True),
    ("make test", True),
    ("cargo test --all", True),
    ("uv run pytest", True),
    ("ls -la", False),
    ("cat pytest.ini", False),
    ("echo testing", False),
    ("grep -r test .", False),
    (None, False),
])
def test_is_test_command(cmd, expected):
    assert is_test_command(cmd) is expected


# --- tests_pass ---

def test_tests_pass_unbacked_is_medium():
    v = one(TP, [(0, [T("ls")])])
    assert (v.severity, v.label, v.evidence, v.phrase) == ("medium", "unbacked", "0 test commands run", "tests pass")


def test_tests_pass_failed_run_is_high():
    v = one(TP, [(0, [T("pytest -q", 1, "3 failed, 14 passed")])])
    assert (v.severity, v.label) == ("high", "contradicted")
    assert v.evidence == "`pytest -q` exited 1"


def test_tests_pass_backed_current_turn():
    v = one(TP, [(0, [T("pytest -q", 0, "17 passed in 3.1s")])])
    assert (v.severity, v.label) == (None, "backed (turn 0)")


def test_later_success_overrides_earlier_failure_in_turn():
    v = one(TP, [(0, [T("pytest -q", 1, "1 failed"), T("pytest -q", 0, "18 passed")])])
    assert v.severity is None


def test_later_failure_overrides_earlier_success_in_turn():
    v = one(TP, [(0, [T("pytest -q", 0, "18 passed"), T("pytest -q", 2, "1 error")])])
    assert v.severity == "high"


def test_count_mismatch_is_high():
    c = FakeClaim(kind="tests_pass", text="All 42 tests pass.", count=42)
    v = one(c, [(0, [T("pytest", 0, "40 passed in 1s")])])
    assert (v.severity, v.label, v.evidence, v.phrase) == ("high", "contradicted", "ledger shows 40 passed, not 42", "42 tests pass")


def test_count_match_is_supported():
    c = FakeClaim(kind="tests_pass", text="All 17 tests pass.", count=17)
    assert one(c, [(0, [T("pytest", 0, "== 17 passed in 2s ==")])]).severity is None


def test_count_unparseable_is_low():
    c = FakeClaim(kind="tests_pass", text="All 17 tests pass.", count=17)
    v = one(c, [(0, [T("pytest", 0, "OK")])])
    assert (v.severity, v.label) == ("low", "unverifiable")


def test_lookback_success_backs_claim():
    v = one(TP, [(0, [T("ls")]), (-1, [T("ls")]), (-2, [T("pytest -q", 0, "9 passed")])])
    assert (v.severity, v.label) == (None, "backed (turn -2)")


def test_lookback_failure_is_stale_success_claim():
    v = one(TP, [(0, []), (-1, [T("pytest -q", 1, "2 failed")])])
    assert (v.severity, v.label, v.evidence) == ("high", "contradicted", "`pytest -q` exited 1 in turn -1")


def test_lookback_nearest_turn_decides():
    v = one(TP, [(0, []), (-1, [T("pytest", 0, "5 passed")]), (-2, [T("pytest", 1)])])
    assert v.label == "backed (turn -1)"


def test_beyond_lookback_is_unbacked():
    # Window only has 4 entries (0, -1, -2, -3), success is at -4 which is beyond lookback
    v = one(TP, [(0, []), (-1, []), (-2, []), (-3, [])])
    assert v.severity == "medium"


def test_background_only_is_low():
    v = one(TP, [(0, [T("pytest -q", 0, "", bg=True)])])
    assert (v.severity, v.label) == ("low", "unverifiable")


def test_tests_ran_supported_even_if_failed():
    c = FakeClaim(kind="tests_ran", text="I ran the tests.")
    v = one(c, [(0, [T("pytest", 1, "1 failed")])])
    assert v.severity is None


# --- file_written ---

def test_file_written_path_suffix_match():
    c = FakeClaim(kind="file_written", text="I wrote `config.yaml`.", target="config.yaml")
    assert one(c, [(0, [W("/home/u/proj/config.yaml")])]).severity is None


def test_file_written_failed_is_high():
    c = FakeClaim(kind="file_written", text="I wrote `config.yaml`.", target="config.yaml")
    v = one(c, [(0, [W("/home/u/proj/config.yaml", ok=False)])])
    assert (v.severity, v.label) == ("high", "contradicted")


def test_file_written_no_matching_file():
    c = FakeClaim(kind="file_written", text="I wrote `config.yaml`.", target="config.yaml")
    v = one(c, [(0, [W("/home/u/proj/other.yaml")])])
    assert (v.severity, v.evidence, v.phrase) == ("medium", "no write or patch receipt for config.yaml", "wrote config.yaml")


def test_file_written_shell_redirect_is_low():
    c = FakeClaim(kind="file_written", text="I wrote `config.yaml`.", target="config.yaml")
    v = one(c, [(0, [T("cat > config.yaml <<EOF\nport: 1\nEOF")])])
    assert (v.severity, v.label) == ("low", "unverifiable")


def test_file_written_suffix_on_path_boundary():
    c = FakeClaim(kind="file_written", text="I wrote `config.yaml`.", target="config.yaml")
    assert one(c, [(0, [W("/x/myconfig.yaml")])]).severity == "medium"


def test_file_written_patch_tool():
    c = FakeClaim(kind="file_written", text="I wrote `config.yaml`.", target="config.yaml")
    assert one(c, [(0, [W("/x/config.yaml", tool="patch")])]).severity is None


def test_file_written_no_target_any_write():
    c = FakeClaim(kind="file_written", text="The config file is updated.")
    assert one(c, [(0, [W("/x/a.toml")])]).severity is None
    assert one(c, [(0, [T("ls")])]).severity == "medium"


# --- ran_command ---

def test_ran_command_target_whitespace_normalized():
    c = FakeClaim(kind="ran_command", text="I ran `make build`.", target="make build")
    assert one(c, [(0, [T("cd app &&  make   build", 2)])]).severity is None


def test_ran_command_no_match():
    c = FakeClaim(kind="ran_command", text="I ran `make build`.", target="make build")
    v = one(c, [(0, [T("make test")])])
    assert (v.severity, v.evidence) == ("medium", "no command matching `make build` ran")


# --- deployed ---

def test_deployed_kubectl_apply():
    c = FakeClaim(kind="deployed", text="Deployed to staging.")
    assert one(c, [(0, [T("kubectl apply -f k8s/")])]).severity is None


def test_deployed_failed():
    c = FakeClaim(kind="deployed", text="Deployed to staging.")
    assert one(c, [(0, [T("alembic upgrade head", 1)])]).severity == "high"


def test_deployed_unbacked():
    c = FakeClaim(kind="deployed", text="Deployed to staging.")
    v = one(c, [(0, [T("ls")])])
    assert (v.severity, v.evidence) == ("medium", "0 deploy or migration commands run")


def test_deployed_systemctl():
    c = FakeClaim(kind="deployed", text="Deployed to staging.")
    assert one(c, [(0, [T("systemctl --user restart hermes-gateway")])]).severity is None


# --- verified ---

def test_verified_any_tool():
    c = FakeClaim(kind="verified", text="I verified the endpoint returns 200.")
    assert one(c, [(0, [T("curl -s localhost:8080/health")])]).severity is None


def test_verified_unbacked():
    c = FakeClaim(kind="verified", text="I verified the endpoint returns 200.")
    v = one(c, [(0, [])])
    assert (v.severity, v.evidence) == ("medium", "0 tool calls")


# --- underway / done ---

def test_underway_done_current_turn_only():
    for kind in ("underway", "done"):
        c = FakeClaim(kind=kind, text="Running it now.")
        v = one(c, [(0, []), (-1, [T("pytest", 0, "1 passed")])])
        assert (v.severity, v.evidence) == ("medium", "0 tool calls this turn"), kind
        assert one(c, [(0, [T("sleep 1", 0, "", bg=True)])]).severity is None


# --- vague ---

def test_vague_is_low():
    v = one(FakeClaim(kind="vague", text="That's fixed.", low=True), [(0, [])])
    assert (v.severity, v.label) == ("low", "unverifiable")


# --- edge cases ---

def test_order_preserved_and_never_raises():
    cs = [TP, FakeClaim(kind="verified", text="x"), FakeClaim(kind="nonsense-kind", text="?")]
    vs = match(cs, [(0, [])])
    assert [v.claim for v in vs] == cs
    assert vs[2].severity == "low" and vs[2].evidence == "matcher error"
    assert match([], [(0, [])]) == []


def test_long_command_truncated_in_evidence():
    v = one(TP, [(0, [T("pytest " + "x" * 200, 1)])])
    assert len(v.evidence) < 90 and "…" in v.evidence


def test_empty_window():
    v = one(TP, [])
    assert v.severity == "medium"


def test_none_command_is_test():
    assert is_test_command(None) is False
    assert is_test_command("") is False


def test_write_failed_evidence():
    c = FakeClaim(kind="file_written", text="I wrote `config.yaml`.", target="config.yaml")
    v = one(c, [(0, [W("/x/config.yaml", ok=False)])])
    assert v.severity == "high"
    assert v.label == "contradicted"


def test_ran_command_no_target():
    c = FakeClaim(kind="ran_command", text="I ran something.")
    assert one(c, [(0, [T("ls -la")])]).severity is None
    v = one(c, [(0, [W("/x/a.py")])])
    assert v.severity == "medium"


def test_verified_failed_is_low():
    c = FakeClaim(kind="verified", text="I checked.")
    v = one(c, [(0, [T("curl localhost", 7, "")])])
    assert (v.severity, v.label, v.evidence) == ("low", "unverifiable", "last check failed")
