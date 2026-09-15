"""Outcome checks, run against real node."""
from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import quality  # noqa: E402
from fakes import *  # noqa: F401,F403


# ------------------------------------------------------ quality checks via node


@unittest.skipIf(shutil.which("node") is None, "node is required for the quality checks")
class QualityCase(TempDirCase):
    def quality(self, source: str, tests: str, workspace: Path | None = None) -> dict:
        workspace = workspace or write_fixture(self.root / "ws", source, tests)
        fixture = self.root / "fixture"
        if not fixture.exists():
            write_fixture(fixture)
        return quality.quality_checks(
            workspace, fixture=fixture, source="src/split.js", test_command=["npm", "test"],
            mutants={"fairness_test": SUBJECT / "mutants/dump_first.js",
                     "rejects_dump_last": SUBJECT / "mutants/dump_last.js"},
            hidden_test=SUBJECT / "hidden.test.js", wrapper=SUBJECT / "mutants/wrap.template.js",
            sentinel=SUBJECT / "mutants/sentinel.js",
        )

    def mutation(self, result: dict) -> tuple:
        return result["repro_test"], result["fairness_test"], result["rejects_dump_last"]

    def hidden(self, implementation: str) -> bool:
        target = write(self.root, "impl/split.js", implementation)
        return quality.hidden_passes(SUBJECT / "hidden.test.js", target, self.root)


@unittest.skipIf(shutil.which("node") is None, "node is required for the quality checks")
class QualityChecks(QualityCase):
    def test_hidden_test_rejects_the_bug_and_both_mutants_and_accepts_a_fix(self) -> None:
        self.assertFalse(self.hidden(BUGGY))
        self.assertFalse(self.hidden((SUBJECT / "mutants/dump_first.js").read_text(encoding="utf-8")))
        self.assertFalse(self.hidden((SUBJECT / "mutants/dump_last.js").read_text(encoding="utf-8")))
        self.assertTrue(self.hidden(CORRECT))

    def test_dump_first_is_right_on_the_reported_case_so_only_fairness_catches_it(self) -> None:
        workspace = write_fixture(self.root / "m", (SUBJECT / "mutants/dump_first.js").read_text(), TEST_HEAD + LOST_CENT)
        self.assertEqual(quality.run_tests(workspace, ["node", "--test"]), 0)

    def test_correct_fix_with_full_tests_passes_every_check(self) -> None:
        self.assertEqual(
            self.quality(CORRECT, TEST_HEAD + LOST_CENT + FAIRNESS),
            {"hidden_pass": True, "suite_pass": True, "repro_test": True,
             "fairness_test": True, "rejects_dump_last": True},
        )

    def test_lost_cent_test_alone_misses_the_fairness_invariant(self) -> None:
        result = self.quality(CORRECT, TEST_HEAD + LOST_CENT)
        self.assertEqual((result["repro_test"], result["rejects_dump_last"]), (True, True))
        self.assertIs(result["fairness_test"], False)

    def test_a_suite_that_fails_on_its_own_fix_proves_nothing(self) -> None:
        broken = TEST_HEAD + LOST_CENT + "test('wrong', () => { assert.equal(1, 2); });\n"
        result = self.quality(CORRECT, broken)
        self.assertEqual((result["suite_pass"], result["repro_test"], result["fairness_test"]), (False, False, False))

    def test_checks_leave_the_workspace_untouched(self) -> None:
        self.quality(CORRECT, TEST_HEAD + LOST_CENT)
        self.assertEqual((self.root / "ws/src/split.js").read_text(encoding="utf-8"), CORRECT)
        self.assertEqual(sorted(p.name for p in (self.root / "ws").iterdir()), ["BUG.md", "package.json", "src", "test"])


@unittest.skipIf(shutil.which("npm") is None, "npm is required for the quality checks")
class MutantsWrapTheModelsOwnCode(QualityCase):
    def test_a_suite_of_only_input_validation_tests_kills_no_mutant(self) -> None:
        result = self.quality(VALIDATING, TEST_HEAD + VALIDATION)
        self.assertIs(result["suite_pass"], True)
        self.assertEqual((result["repro_test"], result["fairness_test"], result["rejects_dump_last"]), (False, False, False))

    def test_validation_and_other_exports_survive_inside_the_mutant(self) -> None:
        exports = "test('keeps exports', () => { assert.equal(require('../src/split').VERSION, 2); });\n"
        result = self.quality(VALIDATING, TEST_HEAD + VALIDATION + exports + LOST_CENT + FAIRNESS)
        self.assertEqual((result["repro_test"], result["fairness_test"], result["rejects_dump_last"]), (True, True, True))

    def test_tests_outside_node_default_discovery_are_found_through_npm_test(self) -> None:
        workspace = write_fixture(self.root / "jest-style", CORRECT, TEST_HEAD)
        (workspace / "test/split.test.js").unlink()
        write(workspace, "__tests__/split.js", TEST_HEAD.replace("../src/split", "../src/split") + LOST_CENT + FAIRNESS)
        write(workspace, "package.json", '{"name": "split-payment", "private": true, "scripts": {"test": "node --test __tests__/*.js"}}\n')
        result = self.quality(CORRECT, "", workspace=workspace)
        self.assertEqual(result, {"hidden_pass": True, "suite_pass": True, "repro_test": True,
                                  "fairness_test": True, "rejects_dump_last": True})

    def test_fixing_the_code_without_touching_any_test_fails_every_mutation_check(self) -> None:
        result = self.quality(CORRECT, TEST_HEAD)
        self.assertEqual((result["suite_pass"], result["hidden_pass"]), (True, True))
        self.assertEqual(self.mutation(result), (False, False, False))

    def test_tests_nobody_runs_leave_the_mutation_checks_undecidable(self) -> None:
        workspace = write_fixture(self.root / "unrun", CORRECT, TEST_HEAD)
        (workspace / "test/split.test.js").unlink()
        write(workspace, "__tests__/split.js", TEST_HEAD + LOST_CENT + FAIRNESS)  # package.json still says node --test
        self.assertEqual(self.mutation(self.quality(CORRECT, "", workspace=workspace)), (None, None, None))

    def test_an_unreadable_test_count_means_the_mutant_checks_cannot_tell(self) -> None:
        workspace = write_fixture(self.root / "silent", CORRECT, TEST_HEAD + LOST_CENT + FAIRNESS)
        write(workspace, "package.json", '{"name": "s", "private": true, "scripts": {"test": "node --test >/dev/null"}}\n')
        result = self.quality(CORRECT, "", workspace=workspace)
        self.assertEqual((result["repro_test"], result["fairness_test"], result["rejects_dump_last"]), (None, None, None))


class TestCount(unittest.TestCase):
    def test_reads_spec_and_tap_reporters(self) -> None:
        self.assertEqual(quality.test_count("ℹ tests 4\nℹ suites 0\n"), 4)
        self.assertEqual(quality.test_count("# tests 3\n# pass 3\n"), 3)
        self.assertIsNone(quality.test_count("PASS  __tests__/split.js\n"))


@unittest.skipIf(shutil.which("npm") is None, "npm is required for the quality checks")
class MutationEdgeCases(QualityCase):
    def test_a_function_export_keeps_its_shape_inside_the_wrapper(self) -> None:
        tests = TEST_HEAD.replace("const { splitCents } = require('../src/split');", "const splitCents = require('../src/split');")
        divisible = "test('thirds', () => { assert.deepEqual(splitCents(300, 3), [100, 100, 100]); });\n"
        result = self.quality(FUNCTION_EXPORT, tests + divisible)
        self.assertIs(result["suite_pass"], True)
        self.assertEqual(self.mutation(result), (False, False, False))
        self.assertEqual(self.mutation(self.quality(FUNCTION_EXPORT, tests + LOST_CENT + FAIRNESS,
                                                    workspace=write_fixture(self.root / "fn2", FUNCTION_EXPORT,
                                                                            tests + LOST_CENT + FAIRNESS))),
                         (True, True, True))

    def test_tests_that_bypass_the_source_file_make_the_mutation_checks_undecidable(self) -> None:
        workspace = write_fixture(self.root / "moved", MOVED_SPLIT, "")
        write(workspace, "src/allocate.js", CORRECT)
        write(workspace, "test/split.test.js",
              (TEST_HEAD + LOST_CENT + FAIRNESS).replace("require('../src/split')", "require('../src/allocate')"))
        result = self.quality(MOVED_SPLIT, "", workspace=workspace)
        self.assertIs(result["suite_pass"], True)
        self.assertEqual(self.mutation(result), (None, None, None))

    def test_an_assertion_added_inside_an_existing_test_still_counts(self) -> None:
        tests = TEST_HEAD.replace(
            "assert.deepEqual(splitCents(10000, 4), [2500, 2500, 2500, 2500]);",
            "assert.deepEqual(splitCents(10000, 4), [2500, 2500, 2500, 2500]); "
            "assert.deepEqual(splitCents(100, 7), [15, 15, 14, 14, 14, 14, 14]);")
        self.assertNotEqual(tests, TEST_HEAD)
        result = self.quality(CORRECT, tests)
        self.assertEqual(self.mutation(result), (True, True, True))


class ChangedTests(TempDirCase):
    def test_new_or_modified_test_files_count_and_other_files_do_not(self) -> None:
        fixture = write_fixture(self.root / "fixture")
        workspace = write_fixture(self.root / "ws", CORRECT)
        self.assertEqual(quality.changed_tests(workspace, fixture), [])
        write(workspace, "src/latest.js", "x")
        write(workspace, "README.md", "x")
        self.assertEqual(quality.changed_tests(workspace, fixture), [])
        write(workspace, "src/split.spec.ts", "x")
        write(workspace, "test/split.test.js", TEST_HEAD + "// more\n")
        write(workspace, "checks/run.js", "x")
        write(workspace, "package.json", '{"scripts": {"test": "node --test checks/*.js"}}')
        self.assertEqual(quality.changed_tests(workspace, fixture),
                         ["checks/run.js", "src/split.spec.ts", "test/split.test.js"])

    def test_fail_counts_from_both_reporters(self) -> None:
        self.assertEqual(quality.fail_count("ℹ tests 4\nℹ fail 2\n"), 2)
        self.assertEqual(quality.fail_count("# fail 0\n"), 0)
        self.assertIsNone(quality.fail_count("nothing"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
