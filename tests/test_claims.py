"""Focused unit tests for claims.extract_claims edge cases not covered by the oracle."""
import pytest
from claims import Claim, KINDS, extract_claims


class TestNegationWordBoundaries:
    def test_note_is_not_not(self):
        claims = extract_claims("I noted this issue.")
        # "noted" contains "not" but should not trigger negation (word boundary)
        assert not any("noted" in c.text for c in claims if not c.low)

    def test_not_in_note_not_excluded(self):
        claims = extract_claims("I noted the change.")
        # No negation exclusion for "noted"
        # Should have no claims (no kind matched), but should not crash
        assert isinstance(claims, list)

    def test_notification_not_negated(self):
        claims = extract_claims("The notification was sent.")
        assert all(not c.low for c in claims)


class TestCaseInsensitivity:
    def test_uppercase_tests_pass(self):
        claims = extract_claims("TESTS PASSED.")
        assert any(c.kind == "tests_pass" for c in claims)

    def test_mixed_case_deployed(self):
        claims = extract_claims("DePlOyEd to prod.")
        assert any(c.kind == "deployed" for c in claims)

    def test_uppercase_ran_command(self):
        claims = extract_claims("I RAN `make test`.")
        assert any(c.kind == "ran_command" for c in claims)


class TestTargetExtraction:
    def test_backtick_target_stripped(self):
        claims = extract_claims("I wrote `config.yaml` today.")
        written = [c for c in claims if c.kind == "file_written"]
        assert written and written[0].target == "config.yaml"

    def test_backtick_target_with_quotes(self):
        claims = extract_claims('I wrote "config.yaml" today.')
        written = [c for c in claims if c.kind == "file_written"]
        assert written and written[0].target == "config.yaml"

    def test_path_without_backticks(self):
        claims = extract_claims("I saved the file to /tmp/report.md.")
        written = [c for c in claims if c.kind == "file_written"]
        assert written and written[0].target == "/tmp/report.md"

    def test_dotted_extension_target(self):
        claims = extract_claims("I wrote app.py with the fix.")
        written = [c for c in claims if c.kind == "file_written"]
        assert written and written[0].target == "app.py"

    def test_command_in_backticks(self):
        claims = extract_claims("I ran `pytest -q tests/`.")
        ran = [c for c in claims if c.kind == "ran_command"]
        assert ran and ran[0].target == "pytest -q tests/"

    def test_no_target_for_non_file(self):
        claims = extract_claims("The config file is updated.")
        written = [c for c in claims if c.kind == "file_written"]
        assert written  # kind matches
        # No backtick, no path token -> target is None
        assert written[0].target is None


class TestCountExtraction:
    def test_all_n_tests(self):
        claims = extract_claims("All 42 tests pass now.")
        tp = [c for c in claims if c.kind == "tests_pass"]
        assert tp and tp[0].count == 42

    def test_n_of_n_pass(self):
        claims = extract_claims("17/17 tests pass.")
        tp = [c for c in claims if c.kind == "tests_pass"]
        assert tp and tp[0].count == 17

    def test_n_passed(self):
        claims = extract_claims("120 passed.")
        tp = [c for c in claims if c.kind == "tests_pass"]
        assert tp and tp[0].count == 120

    def test_no_count_when_absent(self):
        claims = extract_claims("Tests pass.")
        tp = [c for c in claims if c.kind == "tests_pass"]
        assert tp and tp[0].count is None


class TestSubsumption:
    def test_tests_pass_over_tests_ran(self):
        claims = extract_claims("I ran the tests and they pass.")
        kinds = [c.kind for c in claims]
        assert "tests_pass" in kinds
        assert "tests_ran" not in kinds

    def test_tests_pass_over_ran_command(self):
        claims = extract_claims("I ran the tests and all passed.")
        kinds = [c.kind for c in claims]
        assert "tests_pass" in kinds
        assert "ran_command" not in kinds

    def test_tests_ran_over_ran_command(self):
        claims = extract_claims("I ran the tests.")
        kinds = [c.kind for c in claims]
        assert "tests_ran" in kinds
        assert "ran_command" not in kinds

    def test_deployed_over_ran_command(self):
        claims = extract_claims("I ran the migration.")
        kinds = [c.kind for c in claims]
        assert "deployed" in kinds
        assert "ran_command" not in kinds


class TestUnderway:
    def test_nothing_is_not_underway(self):
        claims = extract_claims("Nothing is happening now.")
        assert not any(c.kind == "underway" for c in claims)

    def test_something_is_not_underway(self):
        claims = extract_claims("Something is running now.")
        assert not any(c.kind == "underway" for c in claims)

    def test_anything_is_not_underway(self):
        claims = extract_claims("Is anything going on now?")
        assert claims == []

    def test_everything_is_not_underway(self):
        claims = extract_claims("Everything is going well now.")
        assert not any(c.kind == "underway" for c in claims)

    def test_on_it_now(self):
        claims = extract_claims("On it now.")
        assert any(c.kind == "underway" for c in claims)

    def test_kicking_it_off_now(self):
        claims = extract_claims("Kicking it off now.")
        assert any(c.kind == "underway" for c in claims)

    def test_starting_it_now(self):
        claims = extract_claims("Starting it now.")
        assert any(c.kind == "underway" for c in claims)


class TestInstructionVsPastTense:
    def test_rerun_imperative_excluded(self):
        claims = extract_claims("Rerun the tests.")
        assert claims == []

    def test_rerun_past_tense_not_excluded(self):
        claims = extract_claims("Rerun the tests last night.")
        # "last night" makes it past session -> excluded
        assert claims == []

    def test_re_ran_past_tense_is_claim(self):
        claims = extract_claims("Re-ran the linter.")
        assert any(c.kind == "ran_command" for c in claims)

    def test_ran_past_tense_is_claim(self):
        claims = extract_claims("Ran the tests just now.")
        assert any(c.kind == "tests_ran" for c in claims)

    def test_created_past_tense_is_claim(self):
        claims = extract_claims("Created `new_file.py`.")
        assert any(c.kind == "file_written" for c in claims)

    def test_created_imperative_excluded(self):
        claims = extract_claims("Create a new file.")
        assert claims == []


class TestSentenceSplitting:
    def test_multiple_sentences(self):
        claims = extract_claims("I wrote `a.py`. Tests pass.")
        kinds = sorted([c.kind for c in claims])
        assert kinds == ["file_written", "tests_pass"]

    def test_bullet_list(self):
        claims = extract_claims("- Wrote `a.py`\n- Ran the tests\n- Deployed")
        kinds = sorted([c.kind for c in claims])
        assert "file_written" in kinds
        assert "tests_ran" in kinds
        assert "deployed" in kinds

    def test_numbered_list(self):
        claims = extract_claims("1. Wrote `a.py`\n2. Tests pass")
        kinds = sorted([c.kind for c in claims])
        assert kinds == ["file_written", "tests_pass"]

    def test_no_split_inside_backticks(self):
        claims = extract_claims("I wrote `a.py` and it's ready.")
        # Should be one sentence, not split at the dot in `a.py`
        written = [c for c in claims if c.kind == "file_written"]
        assert written

    def test_filename_dot_not_sentence_boundary(self):
        claims = extract_claims("I updated app.py. It works.")
        # Two sentences: "I updated app.py" and "It works"
        kinds = [c.kind for c in claims]
        assert "file_written" in kinds


class TestZeroVsNonzeroFailures:
    def test_zero_failed_ok(self):
        claims = extract_claims("All 17 passed, 0 failed.")
        assert any(c.kind == "tests_pass" for c in claims)

    def test_zero_errors_ok(self):
        claims = extract_claims("I ran `make build` with 0 errors.")
        # "0 errors" should not trigger negation (review: "Build complete" alone is not a contract claim kind)
        assert [c.kind for c in claims] == ["ran_command"]

    def test_three_errors_excluded(self):
        claims = extract_claims("Build failed with 3 errors.")
        assert claims == []

    def test_zero_failed_explicit(self):
        claims = extract_claims("All 17 passed, zero failed.")
        tp = [c for c in claims if c.kind == "tests_pass"]
        assert tp and tp[0].count == 17


class TestDone:
    def test_done_with_action_verb(self):
        claims = extract_claims("Done.", "fix the bug")
        assert any(c.kind == "done" for c in claims)

    def test_done_without_action_verb(self):
        claims = extract_claims("Done.", "thanks")
        assert not any(c.kind == "done" for c in claims)

    def test_done_with_question(self):
        claims = extract_claims("Done.", "what should I fix?")
        assert not any(c.kind == "done" for c in claims)

    def test_done_no_user_message(self):
        claims = extract_claims("Done.", None)
        assert not any(c.kind == "done" for c in claims)

    def test_all_done_with_action(self):
        claims = extract_claims("All done!", "restart the service")
        assert any(c.kind == "done" for c in claims)

    def test_completed_with_action(self):
        claims = extract_claims("Completed.", "write the report")
        assert any(c.kind == "done" for c in claims)


class TestVague:
    def test_vague_when_no_other_kind(self):
        claims = extract_claims("It works.")
        vague = [c for c in claims if c.kind == "vague"]
        assert vague and all(c.low for c in vague)

    def test_not_vague_when_other_kind_matches(self):
        claims = extract_claims("I wrote `a.py` and it works.")
        # file_written matches, so no vague claim
        vague = [c for c in claims if c.kind == "vague"]
        assert not vague

    def test_thats_fixed_is_vague(self):
        claims = extract_claims("That's fixed.")
        vague = [c for c in claims if c.kind == "vague"]
        assert vague and all(c.low for c in vague)

    def test_all_set_is_vague(self):
        claims = extract_claims("All set.")
        vague = [c for c in claims if c.kind == "vague"]
        assert vague


class TestRobustness:
    def test_empty_string(self):
        assert extract_claims("") == []

    def test_whitespace_only(self):
        assert extract_claims("   \n  ") == []

    def test_binary_junk(self):
        claims = extract_claims("\x00\x01\x02")
        assert claims == []

    def test_unterminated_code_fence(self):
        claims = extract_claims("Here is output:\n```\n17 passed")
        assert claims == []

    def test_very_long_text_truncated(self):
        text = "I wrote `a.py` and " + "x" * 500 + "."
        claims = extract_claims(text)
        for c in claims:
            assert len(c.text) <= 200

    def test_target_truncated(self):
        long_name = "x" * 300
        text = f"I wrote `{long_name}`."
        claims = extract_claims(text)
        written = [c for c in claims if c.kind == "file_written"]
        assert written
        assert len(written[0].target) <= 200

    def test_claim_shape(self):
        (c,) = extract_claims("All 42 tests pass now.")
        assert isinstance(c, Claim)
        assert c.kind in KINDS
        assert len(c.text) <= 200

    def test_never_raises(self):
        for junk in ["", "   ", "```", "```\nunterminated", "\x00\x01",
                     "." * 5000, "?" * 10, None]:
            if junk is None:
                # extract_claims should handle None or it should be str only
                # Per spec, reply is str, but let's be robust
                pass
            else:
                result = extract_claims(junk, None)
                assert isinstance(result, list)


class TestMultipleKindsInOneSentence:
    def test_file_written_and_tests_pass(self):
        claims = extract_claims("I updated `app.py` and all tests pass.")
        kinds = sorted([c.kind for c in claims])
        assert kinds == ["file_written", "tests_pass"]

    def test_multiple_files_different_sentences(self):
        claims = extract_claims("I wrote `a.py`. Then I wrote `b.py`.")
        written = [c for c in claims if c.kind == "file_written"]
        assert len(written) == 2
        targets = {c.target for c in written}
        assert targets == {"a.py", "b.py"}


class TestHypotheticalEdgeCases:
    def test_if_in_middle_is_hypothetical(self):
        # "I'll run the tests if you approve" -> future intent + hypothetical
        claims = extract_claims("I'll run the tests if you approve.")
        assert claims == []

    def test_unless_is_hypothetical(self):
        claims = extract_claims("I won't deploy unless you approve.")
        assert claims == []

    def test_to_start_is_instruction(self):
        claims = extract_claims("To test, run pytest.")
        assert claims == []


class TestPastSessionEdgeCases:
    def test_earlier_this_week(self):
        claims = extract_claims("Earlier this week I ran the tests.")
        assert claims == []

    def test_the_other_day(self):
        claims = extract_claims("The other day I deployed.")
        assert claims == []

    def test_last_time(self):
        claims = extract_claims("Last time I ran the migration it worked.")
        assert claims == []
