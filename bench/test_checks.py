"""Transcript parsing and transcript-based checks."""
from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import checks  # noqa: E402
from fakes import *  # noqa: F401,F403


# ------------------------------------------------------------------ transcript


class Transcript(unittest.TestCase):
    def test_metrics_come_from_the_result_event_when_init_is_not_the_first_line(self) -> None:
        hook = {"type": "system", "subtype": "hook_response", "output": ""}
        events = checks.parse_events(jsonl(hook, init(), assistant({"type": "text", "text": "hi"}), RESULT))
        self.assertEqual(checks.init_event(events)["slash_commands"][0], COMMAND)
        m = checks.metrics(events)
        self.assertEqual((m["duration_ms"], m["num_turns"], m["output_tokens"]), (1234, 7, 500))
        self.assertEqual((m["total_cost_usd"], m["cache_read_input_tokens"], m["is_error"]), (0.25, 2000, False))

    def test_metrics_are_empty_without_a_result_event(self) -> None:
        m = checks.metrics(checks.parse_events(jsonl(init())))
        self.assertTrue(all(m[name] is None for name in checks.METRIC_FIELDS))
        self.assertEqual((m["api_retries"], m["retry_delay_ms"], m["first_turn_input_tokens"]), (0, 0, None))

    def test_truncated_lines_are_skipped_not_fatal(self) -> None:
        events = checks.parse_events(jsonl(init()) + '{"type": "assist')
        self.assertEqual(len(events), 1)

    def test_tool_results_pair_with_their_calls_in_either_content_shape(self) -> None:
        found = calls(assistant(bash("a", "npm test")), reply("a", [{"type": "text", "text": FAILING}], True))
        self.assertEqual((found[0].name, found[0].is_error), ("Bash", True))
        self.assertIn("ℹ fail 1", found[0].output)


class RedBeforeFix(unittest.TestCase):
    def test_failing_run_before_the_first_source_edit(self) -> None:
        found = calls(assistant(bash("a", "npm test")), reply("a", "Exit code 1\n" + FAILING, True),
                      assistant(edit("b", "/w/src/split.js")), reply("b", "ok"))
        self.assertIs(checks.red_before_fix(found, "src/split.js"), True)

    def test_editing_first_then_seeing_red_does_not_count(self) -> None:
        found = calls(assistant(edit("a", "src/split.js")), reply("a", "ok"),
                      assistant(bash("b", "node --test")), reply("b", FAILING, True))
        self.assertIs(checks.red_before_fix(found, "src/split.js"), False)

    def test_a_passing_run_before_the_edit_is_not_red(self) -> None:
        found = calls(assistant(bash("a", "npm test 2>&1 | tail -5")), reply("a", PASSING),
                      assistant(edit("b", "src/split.js", "Write")), reply("b", "ok"))
        self.assertIs(checks.red_before_fix(found, "src/split.js"), False)

    def test_a_permission_denial_is_not_a_failing_test(self) -> None:
        denied = "Claude requested permissions to use Bash, but you haven't granted it yet."
        found = calls(assistant(bash("a", "npm test")), reply("a", denied, True),
                      assistant(edit("b", "src/split.js")), reply("b", "ok"))
        self.assertIs(checks.red_before_fix(found, "src/split.js"), False)

    def test_tap_output_and_test_file_edits(self) -> None:
        found = calls(assistant(edit("a", "/w/test/split.test.js")), reply("a", "ok"),
                      assistant(bash("b", "cd /w && node --test test/split.test.js")),
                      reply("b", "not ok 2 - lost cent\n# pass 1\n# fail 1\n"),
                      assistant(edit("c", "/w/src/split.js", "MultiEdit")), reply("c", "ok"))
        self.assertIs(checks.red_before_fix(found, "src/split.js"), True)

    def test_unknown_when_the_source_was_never_edited_with_a_file_tool(self) -> None:
        found = calls(assistant(bash("a", "npm test")), reply("a", FAILING, True))
        self.assertIsNone(checks.red_before_fix(found, "src/split.js"))
        self.assertIsNone(checks.suite_run_after_fix(found, "src/split.js"))

    def test_similar_file_names_are_not_the_source(self) -> None:
        found = calls(assistant(edit("a", "/w/src/split.js.bak")), reply("a", "ok"))
        self.assertIsNone(checks.red_before_fix(found, "src/split.js"))


class SuiteRunAfterFix(unittest.TestCase):
    def test_full_suite_after_the_last_source_edit(self) -> None:
        found = calls(assistant(edit("a", "src/split.js")), reply("a", "ok"),
                      assistant(bash("b", "npm test")), reply("b", PASSING))
        self.assertIs(checks.suite_run_after_fix(found, "src/split.js"), True)

    def test_a_single_test_file_is_not_the_full_suite(self) -> None:
        found = calls(assistant(edit("a", "src/split.js")), reply("a", "ok"),
                      assistant(bash("b", "node --test test/split.test.js")), reply("b", PASSING))
        self.assertIs(checks.suite_run_after_fix(found, "src/split.js"), False)

    def test_a_run_between_two_source_edits_is_not_after_the_fix(self) -> None:
        found = calls(assistant(edit("a", "src/split.js")), reply("a", "ok"),
                      assistant(bash("b", "node --test")), reply("b", PASSING),
                      assistant(edit("c", "src/split.js")), reply("c", "ok"))
        self.assertIs(checks.suite_run_after_fix(found, "src/split.js"), False)


class SkillLoaded(unittest.TestCase):
    def check(self, *events: dict):
        return checks.skill_loaded(checks.parse_events(jsonl(*events)), COMMAND, [SKILL_DIR])

    def test_false_when_the_command_never_registered(self) -> None:
        self.assertIs(self.check(init(slash_commands=["compact"]), RESULT), False)

    def test_true_with_registration_and_evidence_of_the_skill_directory(self) -> None:
        read = {"type": "tool_use", "id": "r", "name": "Read",
                "input": {"file_path": SKILL_DIR + "/../../references/testing-patterns.md"}}
        self.assertIs(self.check(init(), assistant(read), RESULT), True)

    def test_true_when_a_skill_tool_call_names_it(self) -> None:
        skill = {"type": "tool_use", "id": "s", "name": "Skill", "input": {"skill": COMMAND}}
        self.assertIs(self.check(init(), assistant(skill), RESULT), True)

    def test_unknown_when_registered_without_evidence(self) -> None:
        # The init event itself carries the plugin path; it must not count as evidence.
        self.assertIsNone(self.check(init(), assistant({"type": "text", "text": "done"}), RESULT))

    def test_unknown_without_an_init_event(self) -> None:
        self.assertIsNone(self.check(RESULT))


def retry(status: int, delay: int) -> dict:
    return {"type": "system", "subtype": "api_retry", "attempt": 1, "max_retries": 10,
            "retry_delay_ms": delay, "error_status": status, "error": "x", "session_id": "s", "uuid": "u"}


class RetriesAndFirstTurn(unittest.TestCase):
    def test_api_retries_and_their_waiting_time_are_counted(self) -> None:
        m = checks.metrics(checks.parse_events(jsonl(init(), retry(529, 500), retry(429, 1200), RESULT)))
        self.assertEqual((m["api_retries"], m["retry_delay_ms"]), (2, 1700))

    def test_authentication_retries_abort_but_overload_retries_do_not(self) -> None:
        self.assertIn("401", checks.auth_failure(json.dumps(retry(401, 577))))
        self.assertIn("403", checks.auth_failure(json.dumps(retry(403, 577))))
        self.assertIsNone(checks.auth_failure(json.dumps(retry(429, 577))))
        self.assertIsNone(checks.auth_failure('{"type": "assistant", "error_status": 401}'))
        self.assertIsNone(checks.auth_failure("not json"))

    def test_first_turn_input_counts_every_input_token_kind_of_the_first_assistant_event(self) -> None:
        first = assistant({"type": "text", "text": "a"})
        first["message"]["usage"] = {"input_tokens": 3, "cache_creation_input_tokens": 4000, "cache_read_input_tokens": 9000,
                                     "output_tokens": 1}
        later = assistant({"type": "text", "text": "b"})
        later["message"]["usage"] = {"input_tokens": 99999}
        m = checks.metrics(checks.parse_events(jsonl(init(), first, later, RESULT)))
        self.assertEqual(m["first_turn_input_tokens"], 13003)


class RedBeforeFixAcrossSeveralEdits(unittest.TestCase):
    def test_red_between_the_first_and_last_edit_is_not_before_the_fix(self) -> None:
        found = calls(assistant(edit("a", "src/split.js")), reply("a", "ok"),
                      assistant(bash("b", "npm test")), reply("b", FAILING, True),
                      assistant(edit("c", "src/split.js")), reply("c", "ok"))
        self.assertIs(checks.red_before_fix(found, "src/split.js"), False)


class FullSuiteForms(unittest.TestCase):
    def test_redirections_still_run_everything(self) -> None:
        for command in ("node --test 2>/dev/null", "node --test > /tmp/out.txt", "node --test --test-reporter spec",
                        "npm test >/tmp/o.txt 2>&1", "cd /w && node --test 2>&1 | tail -3"):
            self.assertTrue(checks.is_full_suite_command(command), command)

    def test_filters_paths_and_globs_do_not(self) -> None:
        # Node 25 exits 1 with "Cannot find module" on a directory argument: nothing ran.
        # A glob may or may not cover the suite, so it does not count either.
        for command in ("node --test --test-name-pattern=lost", "node --test --test-name-pattern lost",
                        "node --test --test-only", "node --test test/split.test.js 2>/dev/null",
                        "npm test -- --test-name-pattern=x", "node --test ./test/", "node --test test",
                        "node --test 'test/**/*.test.js'"):
            self.assertFalse(checks.is_full_suite_command(command), command)


class LeakSuspect(unittest.TestCase):
    MARKERS = ["/repo/bench"]

    def suspect(self, tool_input: dict, name: str = "Bash") -> bool:
        use = {"type": "tool_use", "id": "x", "name": name, "input": tool_input}
        return checks.leak_suspect(calls(assistant(use)), self.MARKERS)

    def test_any_look_at_grading_material_is_suspect(self) -> None:
        self.assertTrue(self.suspect({"file_path": "/repo/bench/run.py"}, "Read"))
        for command in ("find / -name hidden.test.js", "cat ../mutants/dump_last.js", "ls ~/.cache",
                        "grep -r lost evals/cases", "ls results/raw"):
            self.assertTrue(self.suspect({"command": command}), command)

    def test_ordinary_work_is_not(self) -> None:
        self.assertFalse(self.suspect({"command": "npm test"}))
        self.assertFalse(self.suspect({"file_path": "/tmp/bench-run-1/workspace/src/split.js"}, "Edit"))


class LeakSuspectLooksAtPathsOnly(unittest.TestCase):
    def suspect(self, tool_input: dict, name: str) -> bool:
        use = {"type": "tool_use", "id": "x", "name": name, "input": tool_input}
        return checks.leak_suspect(calls(assistant(use)), ["/repo/bench"])

    def test_words_inside_written_code_are_not_a_leak(self) -> None:
        self.assertFalse(self.suspect({"file_path": "/w/test/split.test.js",
                                       "content": "// survives common mutants, unlike hidden.test tricks"}, "Write"))
        self.assertFalse(self.suspect({"file_path": "/w/src/split.js", "old_string": "mutants",
                                       "new_string": "evals/cases"}, "Edit"))

    def test_path_fields_of_search_tools_are(self) -> None:
        self.assertTrue(self.suspect({"pattern": "**/hidden.test.js"}, "Glob"))
        self.assertTrue(self.suspect({"pattern": "splitCents", "path": "/repo/bench"}, "Grep"))
        self.assertTrue(self.suspect({"notebook_path": "/x/.cache/n.ipynb"}, "NotebookEdit"))


def result_with(text: str, is_error: bool) -> str:
    return json.dumps({**RESULT, "is_error": is_error, "result": text})


class SubscriptionStops(unittest.TestCase):
    def test_not_logged_in_and_http_auth_errors_stop_everything(self) -> None:
        self.assertIn("authentication", checks.auth_failure(result_with("Not logged in · Please run /login", True)))
        self.assertIn("authentication", checks.auth_failure(result_with("API Error: 403 forbidden", True)))

    def test_npm_output_with_403_is_not_an_authentication_failure(self) -> None:
        self.assertIsNone(checks.auth_failure(result_with("npm ERR! 403 Forbidden - GET https://registry/x", True)))
        self.assertIsNone(checks.auth_failure(result_with("tests failed: expected 401 got 403", True)))
        self.assertIn("authentication", checks.auth_failure(result_with('API Error: 401 {"type":"authentication_error"}', True)))

    def test_a_successful_answer_that_mentions_limits_or_login_is_not_a_stop(self) -> None:
        self.assertIsNone(checks.auth_failure(result_with("Added a rate limit test; /login route untouched; 403 handled", False)))


class EnvIsolated(unittest.TestCase):
    def isolated(self, **fields):
        event = {"type": "system", "subtype": "init", "plugins": ["subject@inline"], "mcp_servers": [], **fields}
        return checks.env_isolated(checks.parse_events(jsonl({"apiKeySource": "ANTHROPIC_API_KEY", **event})),
                                   "subject", "api-key")

    def test_only_the_subject_plugin_and_no_mcp_servers(self) -> None:
        self.assertIs(self.isolated(), True)
        self.assertIs(self.isolated(plugins=[{"name": "subject", "path": "/tmp/p"}]), True)
        self.assertIs(self.isolated(plugins=["subject@inline", "superpowers@market"]), False)
        self.assertIs(self.isolated(mcp_servers=[{"name": "serena", "status": "connected"}]), False)
        self.assertIs(self.isolated(plugins=[]), False)
        self.assertIsNone(checks.env_isolated(checks.parse_events(jsonl(RESULT)), "subject", "api-key"))


def ended(text: str, is_error: bool, output_tokens=0, num_turns: int = 1, *extra: dict, cost: float = 0.25,
          subtype: str = "success") -> list:
    result = {**RESULT, "is_error": is_error, "result": text, "num_turns": num_turns, "total_cost_usd": cost,
              "subtype": subtype, "usage": {**RESULT["usage"], "output_tokens": output_tokens}}
    return checks.parse_events(jsonl(init(), *extra, result))


class QuotaAndDegenerateRuns(unittest.TestCase):
    def test_a_five_hour_limit_stops_the_batch_whether_or_not_is_error_is_set(self) -> None:
        for is_error in (True, False):
            reason = checks.quota_exhausted(ended("You've hit your 5-hour limit · resets 3pm", is_error))
            self.assertIn("subscription", reason, is_error)
        self.assertIn("subscription", checks.quota_exhausted(ended("usage limit reached", False, None, cost=0)))

    def test_real_work_that_mentions_a_limit_is_not_a_quota_stop(self) -> None:
        work = assistant(bash("a", "npm test"))
        self.assertIsNone(checks.quota_exhausted(ended("Added a rate limit guard", False, 40, 6, work)))
        self.assertIsNone(checks.quota_exhausted(ended("Summary of the limit handling ...", False, 900)))

    def test_degenerate_means_the_environment_produced_nothing(self) -> None:
        self.assertIs(checks.degenerate_run(ended("", True, 40)), True)  # is_error
        self.assertIs(checks.degenerate_run(ended("hi", False, 0)), True)  # no output
        self.assertIs(checks.degenerate_run(ended("hi", False, None, cost=0)), True)
        self.assertIs(checks.degenerate_run(ended("", True, None, cost=None)), True)
        self.assertIs(checks.degenerate_run(ended("hi", False, 12, cost=0)), True)  # nothing billed
        self.assertIs(checks.degenerate_run(ended("hi", False, 0, 1, assistant(bash("a", "ls")))), False)

    def test_an_answer_without_tools_is_model_behaviour_not_an_environment_problem(self) -> None:
        asked = ended("I won't ship the one-liner... Want me to apply the correct fix?", False, 40, 1)
        self.assertIs(checks.degenerate_run(asked), False)
        self.assertIs(checks.no_tool_answer(asked), True)
        self.assertIs(checks.no_tool_answer(ended("", True, 40)), False)  # degenerate, not an answer
        self.assertIs(checks.no_tool_answer(ended("done", False, 40, 5, assistant(bash("a", "npm test")))), False)
        self.assertIsNone(checks.no_tool_answer(checks.parse_events(jsonl(init()))))

    def test_a_quota_hit_after_real_work_still_stops_the_batch(self) -> None:
        work = assistant(bash("a", "npm test"))
        self.assertIn("usage limit", checks.quota_exhausted(ended("Claude AI usage limit reached", True, 300, 6, work)))
        self.assertIn("usage limit", checks.quota_exhausted(
            ended("You've hit your 5-hour limit · resets 3pm", False, 300, 6, work, subtype="error_during_execution")))
        self.assertIsNone(checks.quota_exhausted(ended("Added a rate limit guard", False, 300, 6, work)))

    def test_a_short_billed_answer_mentioning_a_limit_is_not_a_quota_stop(self) -> None:
        self.assertIsNone(checks.quota_exhausted(ended("The fairness limit is one cent.", False, 14, 1, cost=0.02)))


class ApiKeySource(unittest.TestCase):
    def isolated(self, source, auth: str):
        event = {"type": "system", "subtype": "init", "plugins": ["subject@inline"], "mcp_servers": []}
        if source is not None:
            event["apiKeySource"] = source
        return checks.env_isolated(checks.parse_events(jsonl(event)), "subject", auth)

    def test_the_credential_in_use_must_match_the_mode(self) -> None:
        self.assertIs(self.isolated("none", "oauth"), True)
        self.assertIs(self.isolated("ANTHROPIC_API_KEY", "oauth"), False)
        self.assertIs(self.isolated("ANTHROPIC_API_KEY", "api-key"), True)
        self.assertIs(self.isolated("none", "api-key"), False)
        self.assertIs(self.isolated(None, "oauth"), False)


class CredentialExposed(unittest.TestCase):
    def exposed(self, command: str) -> bool:
        return checks.credential_exposed(calls(assistant(bash("x", command))))

    def test_commands_that_print_the_environment(self) -> None:
        for command in ("env", "printenv", "env | sort", "cd /w && printenv HOME", "set", "export -p",
                        "echo $CLAUDE_CODE_OAUTH_TOKEN", "echo ${ANTHROPIC_API_KEY}", "cat /proc/self/environ",
                        "node -e 'console.log(process.env)'", "npm run env",
                        "node -e \"console.log(process.env.CLAUDE_CODE_OAUTH_TOKEN)\""):
            self.assertTrue(self.exposed(command), command)

    def test_ordinary_commands(self) -> None:
        for command in ("npm test", "env NODE_ENV=test npm test", "set -e; npm test", "git status", "echo $HOME"):
            self.assertFalse(self.exposed(command), command)


class MissingUsageAndApiErrorShapes(unittest.TestCase):
    def test_a_billed_answer_without_a_usage_field_is_still_an_answer(self) -> None:
        events = ended("I won't ship the one-liner... Want me to apply the correct fix?", False, None, cost=0.3)
        self.assertIs(checks.degenerate_run(events), False)
        self.assertIs(checks.no_tool_answer(events), True)
        self.assertIs(checks.degenerate_run(ended("x", True, None, cost=0.3)), False)  # billed: it did produce something

    def test_api_error_shapes_of_a_mid_run_quota_hit(self) -> None:
        work = assistant(bash("a", "npm test"))
        for text in ('API Error: {"type":"error","error":{"type":"rate_limit_error","message":"slow down"}}',
                     "API Error: 429 Too Many Requests", "Your credit balance is too low", "Quota exceeded"):
            self.assertIsNotNone(checks.quota_exhausted(ended(text, True, 300, 6, work)), text)
        self.assertIsNone(checks.quota_exhausted(ended("handled rate_limit_error and 429 retries", False, 300, 6, work)))

    def test_a_429_from_npm_or_the_network_is_not_the_subscription(self) -> None:
        work = assistant(bash("a", "npm install"))
        for text in ("npm ERR! 429 Too Many Requests - GET https://registry.npmjs.org/x",
                     "curl: (22) The requested URL returned error: 429", "retrying after 429"):
            self.assertIsNone(checks.quota_exhausted(ended(text, True, 300, 6, work)), text)
        self.assertIsNotNone(checks.quota_exhausted(ended("Request rejected (429)", True, 300, 6, work)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
