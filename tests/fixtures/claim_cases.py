"""Frozen claim-extraction oracle (v1 taxonomy). All cases are invented; none come from real sessions.

DO NOT EDIT to make tests pass. Changes require orchestrator review.

POSITIVE: (reply, user_message, expected) — expected is a list of dicts; every non-low claim
  extracted must match one dict by kind, and count/target when the dict specifies them.
EXCLUDED: (reply, user_message, reason) — extract_claims must return no claims at all.
LOW: (reply, user_message) — extract_claims returns only claims with kind "vague" and low=True.
"""

POSITIVE = [
    ("All tests pass.", None, [{"kind": "tests_pass"}]),
    ("All 42 tests pass now.", None, [{"kind": "tests_pass", "count": 42}]),
    ("17 passed, 0 failed.", None, [{"kind": "tests_pass", "count": 17}]),
    ("The test suite is green.", None, [{"kind": "tests_pass"}]),
    ("Tests passed on the second run.", None, [{"kind": "tests_pass"}]),
    ("17/17 tests pass and the build is clean.", None, [{"kind": "tests_pass", "count": 17}]),
    ("Re-ran the full suite: 120 passed.", None, [{"kind": "tests_pass", "count": 120}]),
    ("I ran the tests and they pass.", None, [{"kind": "tests_pass"}]),
    ("I ran the tests.", None, [{"kind": "tests_ran"}]),
    ("I ran `make build` and it finished cleanly.", None, [{"kind": "ran_command", "target": "make build"}]),
    ("I've executed the backfill script.", None, [{"kind": "ran_command"}]),
    ("I re-ran the linter.", None, [{"kind": "ran_command"}]),
    ("I wrote `config.yaml` with the new port.", None, [{"kind": "file_written", "target": "config.yaml"}]),
    ("Created `scripts/sync.py`.", None, [{"kind": "file_written", "target": "scripts/sync.py"}]),
    ("The config file is updated.", None, [{"kind": "file_written"}]),
    ("I've saved the report to /tmp/out/report.md.", None, [{"kind": "file_written", "target": "/tmp/out/report.md"}]),
    ("I updated `app.py` and all tests pass.", None,
     [{"kind": "file_written", "target": "app.py"}, {"kind": "tests_pass"}]),
    ("Deployed to staging.", None, [{"kind": "deployed"}]),
    ("I ran the migration against staging.", None, [{"kind": "deployed"}]),
    ("Migration applied successfully.", None, [{"kind": "deployed"}]),
    ("I restarted the service and it came back healthy.", None, [{"kind": "deployed"}]),
    ("The service has been restarted.", None, [{"kind": "deployed"}]),
    ("The fix is rolled out to production.", None, [{"kind": "deployed"}]),
    ("I verified the endpoint returns 200.", None, [{"kind": "verified"}]),
    ("Confirmed working on my end.", None, [{"kind": "verified"}]),
    ("I've confirmed the cron job fires every hour.", None, [{"kind": "verified"}]),
    ("Running it now.", None, [{"kind": "underway"}]),
    ("Kicking that off now, results shortly.", None, [{"kind": "underway"}]),
    ("Extracting now.", None, [{"kind": "underway"}]),
    ("Done.", "Run the migration on staging", [{"kind": "done"}]),
    ("All done!", "restart the gateway please", [{"kind": "done"}]),
    ("Summary below.\n\n- I wrote `notes.md`.\n- Tests pass.", None,
     [{"kind": "file_written", "target": "notes.md"}, {"kind": "tests_pass"}]),
]

EXCLUDED = [
    ("Here's the output:\n```\n17 passed in 2.1s\nAll tests pass\n```", None, "fenced code"),
    ("> All tests pass\n> Deployed to staging", None, "quoted output"),
    ("Captured output:\n\n    17 passed in 3.11s\n    deployed ok", None, "indented output"),
    ("If the tests pass, merge it.", None, "hypothetical"),
    ("Once you run the migration, the table will exist.", None, "hypothetical"),
    ("When you deploy, watch the logs.", None, "hypothetical"),
    ("To deploy, run `make release`.", None, "instruction"),
    ("Yesterday I ran the tests and they passed.", None, "past session"),
    ("Last week we deployed the hotfix.", None, "past session"),
    ("In the previous session I verified the endpoint.", None, "past session"),
    ("Run `pytest -q` to check.", None, "instruction"),
    ("Restart the service after editing the config.", None, "instruction"),
    ("You should verify the cron fires before Friday.", None, "instruction"),
    ("Please deploy to staging when ready.", None, "instruction"),
    ("Did the tests pass?", None, "question"),
    ("Could you run the tests?", None, "question"),
    ("The tests don't pass yet.", None, "negation"),
    ("I haven't run the migration.", None, "negation"),
    ("Not verified: the endpoint still returns 500.", None, "negation"),
    ("Tests failed with 3 errors.", None, "failure report, not a success claim"),
    ("I'll run the tests next.", None, "future intent"),
    ("I will deploy once you approve.", None, "future intent"),
    ("Done.", "thanks, that's all for today", "done without an action request"),
    ("Done.", None, "done without a user message"),
    ("Done.", "What does the ledger store?", "done after a question"),
]

LOW = [
    ("That's fixed.", None),
    ("It works now.", None),
    ("All set.", None),
    ("Everything is in place.", None),
]
