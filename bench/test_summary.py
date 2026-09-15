"""Schedule and summary statistics."""
from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run  # noqa: E402
import stats  # noqa: E402
import summary  # noqa: E402
from fakes import *  # noqa: F401,F403


# ------------------------------------------------------------------ scheduling


class Schedule(unittest.TestCase):
    def test_variants_interleave_within_every_round_and_task(self) -> None:
        slots = run.schedule(runs=20, tasks=[1, 2], seed=7)
        self.assertEqual(len(slots), 20 * 2 * 2)
        pairs = [slots[i:i + 2] for i in range(0, len(slots), 2)]
        for pair in pairs:
            self.assertEqual({s.variant for s in pair}, {"before", "after"})
            self.assertEqual(len({(s.run_index, s.task) for s in pair}), 1)
            self.assertEqual([s.position for s in pair], [0, 1])
        firsts = {pair[0].variant for pair in pairs}
        self.assertEqual(firsts, {"before", "after"})  # order is randomised, not fixed
        self.assertEqual([s.sequence for s in slots], list(range(len(slots))))

    def test_same_seed_reproduces_the_schedule(self) -> None:
        self.assertEqual(run.schedule(10, [1, 2], seed=3), run.schedule(10, [1, 2], seed=3))
        self.assertNotEqual(run.schedule(10, [1, 2], seed=3), run.schedule(10, [1, 2], seed=4))


# --------------------------------------------------------------------- summary


def row(task: int, variant: str, duration: float, **check_values) -> dict:
    return {"task": task, "variant": variant, "duration_ms": duration, "num_turns": 5, "position": 0,
            "output_tokens": 100, "total_cost_usd": 0.1, "timed_out": False, "exit_code": 0, "is_error": False,
            "result_subtype": "success", "warmup": False, "leak_suspect": False, "api_retries": 0,
            "retry_delay_ms": 0, "first_turn_input_tokens": 1000, "skill_md_bytes": 16000, "env_isolated": True,
            "degenerate_run": False, "no_tool_answer": False, "credential_exposed": False, "checks": check_values}


class Summary(unittest.TestCase):
    def test_median_and_iqr_use_linear_interpolation(self) -> None:
        self.assertEqual(stats.describe([4, 1, 3, 2]), (4, 2.5, 1.75, 3.25))
        self.assertEqual(stats.describe([5, None]), (1, 5, 5, 5))
        self.assertEqual(stats.describe([None]), (0, None, None, None))

    def test_change_is_after_median_relative_to_before(self) -> None:
        rows = [row(1, "before", d, hidden_pass=True) for d in (100, 200, 300)]
        rows += [row(1, "after", d, hidden_pass=True) for d in (50, 100, 150)]
        text = summary.render(rows, "results/x")
        self.assertIn("-50.0%", text)
        self.assertIn("100% (3/3)", text)
        self.assertNotIn("QUALITY REGRESSION", text)

    def test_a_lower_after_pass_rate_is_flagged_at_the_top(self) -> None:
        rows = [row(1, "before", 100, fairness_test=True, skill_loaded=True) for _ in range(2)]
        rows += [row(1, "after", 50, fairness_test=True, skill_loaded=None),
                 row(1, "after", 50, fairness_test=False, skill_loaded=None)]
        text = summary.render(rows, "results/x")
        self.assertLess(text.index("QUALITY DROP (not significant)"), text.index("## Task 1"))
        self.assertNotIn("SIGNIFICANT", text)
        self.assertIn("fairness_test", text.split("## Task 1")[0])
        self.assertEqual([text for _, text in summary.regressions(rows)],
                         [t for _, t in summary.regressions(rows) if "skill_loaded" not in t])  # validity, not quality
        self.assertEqual(len(summary.regressions(rows)), 1)

    def test_nulls_are_excluded_from_rates_but_reported(self) -> None:
        rows = [row(1, "before", 1, red_before_fix=True), row(1, "before", 1, red_before_fix=None)]
        self.assertIn("100% (1/1, 1 undecidable)", summary.render(rows, "x"))


class BalancedSchedule(unittest.TestCase):
    def test_each_task_runs_before_first_in_exactly_half_the_rounds(self) -> None:
        for runs, allowed in ((10, {(5, 5)}), (7, {(3, 4), (4, 3)})):
            slots = run.schedule(runs, [1, 2], seed=11)
            for task in (1, 2):
                firsts = [s.variant for s in slots if s.task == task and s.position == 0 and not s.warmup]
                self.assertIn((firsts.count("before"), firsts.count("after")), allowed)

    def test_warmups_come_first_are_flagged_and_do_not_shift_the_balance(self) -> None:
        slots = run.schedule(4, [1, 2], seed=5, warmup=1)
        self.assertEqual(len(slots), (4 + 1) * 2 * 2)
        self.assertTrue(all(s.warmup for s in slots[:4]))
        self.assertFalse(any(s.warmup for s in slots[4:]))
        self.assertEqual(len({s.run_id for s in slots}), len(slots))
        counted = [s for s in slots if not s.warmup]
        self.assertEqual(counted, [s for s in run.schedule(4, [1, 2], seed=5, warmup=1) if not s.warmup])
        for task in (1, 2):
            firsts = [s.variant for s in counted if s.task == task and s.position == 0]
            self.assertEqual(firsts.count("before"), 2)


class Statistics(unittest.TestCase):
    def test_fisher_exact_two_sided_matches_known_values(self) -> None:
        self.assertAlmostEqual(stats.fisher_exact(3, 1, 1, 3), 0.4857142857, places=9)  # R: fisher.test(TeaTasting)
        self.assertAlmostEqual(stats.fisher_exact(10, 0, 0, 10), 2 / 184756, places=12)
        self.assertAlmostEqual(stats.fisher_exact(5, 5, 5, 5), 1.0)

    def test_bootstrap_ci_of_the_median_difference_is_seeded(self) -> None:
        self.assertEqual(stats.bootstrap_median_diff([100] * 5, [50] * 5), (-50, -50))
        before, after = [120, 100, 130, 90, 110, 105], [60, 70, 55, 90, 65, 58]
        low, high = stats.bootstrap_median_diff(before, after)
        self.assertEqual((low, high), stats.bootstrap_median_diff(before, after))
        self.assertLess(low, -40)
        self.assertLess(high, 0)
        self.assertIsNone(stats.bootstrap_median_diff([], after))


def excluded(**changes) -> dict:
    return {**row(1, "after", 1_000_000, hidden_pass=False), **changes}


class SummaryExclusions(unittest.TestCase):
    def test_failed_warmup_and_leaky_runs_stay_out_of_costs_and_rates(self) -> None:
        rows = [row(1, "before", 100, hidden_pass=True), row(1, "after", 50, hidden_pass=True)]
        rows += [excluded(is_error=True), excluded(exit_code=1), excluded(result_subtype="error_max_turns"),
                 excluded(timed_out=True, exit_code=None), excluded(warmup=True), excluded(leak_suspect=True),
                 {"task": 1, "variant": "after", "error": "boom"}]
        text = summary.render(rows, "x")
        self.assertIn("-50.0%", text)  # cost: only the one clean after run counts
        after = text.split("`after`")[1].split("\n")[0]
        cost, quality_part = after.split("quality excludes")
        for reason in ("1 crashed", "1 timed out", "1 non-zero exit", "1 is_error", "1 non-success result",
                       "1 warmup", "1 leak suspect"):
            self.assertIn(reason, cost)
        self.assertIn("1 crashed, 1 warmup, 1 leak suspect", quality_part)
        self.assertIn("counted as failed: 1 timed out, 1 non-zero exit, 1 is_error, 1 non-success result", quality_part)
        hidden = next(l for l in text.splitlines() if l.startswith("| hidden_pass |"))
        self.assertIn("20% (1/5)", hidden)  # the four unfinished runs count as failures
        self.assertLess(text.index("leak_suspect: 1 run"), text.index("## Task 1"))

    def test_significant_regressions_are_named_as_such(self) -> None:
        rows = [row(1, "before", 100, fairness_test=True) for _ in range(10)]
        rows += [row(1, "after", 50, fairness_test=False) for _ in range(10)]
        text = summary.render(rows, "x")
        self.assertLess(text.index("SIGNIFICANT QUALITY REGRESSION"), text.index("## Task 1"))
        self.assertIn("p=0.0000", text)

    def test_undecidable_results_are_neither_pass_nor_fail(self) -> None:
        rows = [row(1, "before", 1, repro_test=True), row(1, "after", 1, repro_test=None),
                row(1, "after", 1, repro_test=None)]
        text = summary.render(rows, "x")
        self.assertIn("n/a (0/0, 2 undecidable)", text)
        self.assertNotIn("QUALITY", text)


class SummaryDiagnostics(unittest.TestCase):
    def rows(self) -> list:
        rows = []
        for i, duration in enumerate((100, 200, 300, 400)):
            rows.append({**row(1, "before", duration, hidden_pass=True), "position": i % 2,
                         "first_turn_input_tokens": 9000, "skill_md_bytes": 16000, "retry_delay_ms": 0})
            rows.append({**row(1, "after", duration / 2, hidden_pass=True), "position": (i + 1) % 2,
                         "first_turn_input_tokens": 6000, "skill_md_bytes": 6000, "api_retries": 1, "retry_delay_ms": 10})
        return rows

    def test_first_turn_tokens_are_compared_with_the_skill_size_difference(self) -> None:
        text = summary.render(self.rows(), "x")
        line = next(l for l in text.splitlines() if "first_turn_input_tokens" in l)
        self.assertIn("-3,000", line)
        self.assertIn("-10,000 bytes", line)

    def test_duration_by_position_retries_and_confidence_intervals(self) -> None:
        text = summary.render(self.rows(), "x")
        self.assertIn("| duration_ms_net |", text)
        self.assertIn("95% CI", text)
        position = next(l for l in text.splitlines() if l.startswith("| ran first |"))
        self.assertIn("200", position)  # before ran first in rounds 0 and 2: durations 100, 300
        self.assertIn("api retries: before 0 (0 ms), after 4 (40 ms)", text)


class SurvivorBias(unittest.TestCase):
    def rows(self) -> list:
        rows = [row(1, "before", 100, hidden_pass=True, repro_test=True) for _ in range(10)]
        rows += [row(1, "after", 50, hidden_pass=True, repro_test=True) for _ in range(6)]
        rows += [{**row(1, "after", 900_000, hidden_pass=False, repro_test=None), "timed_out": True,
                  "exit_code": None, "result_subtype": None, "is_error": None, "duration_ms": None} for _ in range(4)]
        return rows

    def test_timeouts_that_fail_the_hidden_test_show_up_as_a_quality_drop(self) -> None:
        text = summary.render(self.rows(), "x")
        top = text.split("## Task 1")[0]
        self.assertIn("QUALITY DROP", top)
        self.assertIn("`hidden_pass`: after 60% (6/10) < before 100% (10/10)", top)
        self.assertIn("`clean_completion`: after 60% (6/10) < before 100% (10/10)", top)
        self.assertIn("4 timed out", top)
        self.assertNotIn("No quality check", text)

    def test_an_unfinished_run_never_passes_but_undecidable_stays_undecidable(self) -> None:
        rows = [row(1, "before", 1, hidden_pass=True, repro_test=True),
                {**row(1, "after", 1, hidden_pass=True, repro_test=None), "is_error": True}]
        text = summary.render(rows, "x")
        self.assertIn("| hidden_pass | 100% (1/1) | 0% (0/1) |", text)
        self.assertIn("| repro_test | 100% (1/1) | n/a (0/0, 1 undecidable) |", text)
        self.assertIn("| clean_completion | 100% (1/1) | 0% (0/1) |", text)


class EnvIsolation(unittest.TestCase):
    def test_runs_that_were_not_isolated_are_left_out_of_everything_and_counted_at_the_top(self) -> None:
        rows = [row(1, "before", 100, hidden_pass=True), row(1, "after", 50, hidden_pass=True),
                {**row(1, "after", 9_999, hidden_pass=False), "env_isolated": False},
                {**row(1, "after", 9_999, hidden_pass=False), "env_isolated": None}]
        text = summary.render(rows, "x")
        top = text.split("## Task 1")[0]
        self.assertIn("env_isolated: 2 run(s)", top)
        self.assertIn("-50.0%", text)
        self.assertIn("| hidden_pass | 100% (1/1) | 100% (1/1) |", text)
        self.assertIn("quality excludes 2 env not isolated", top)


class DegenerateAndExposed(unittest.TestCase):
    def test_degenerate_runs_are_environment_problems_not_model_behaviour(self) -> None:
        rows = [row(1, "before", 100, hidden_pass=True), row(1, "after", 50, hidden_pass=True),
                {**row(1, "after", 3, hidden_pass=False), "degenerate_run": True},
                {**row(1, "after", 3, hidden_pass=False), "credential_exposed": True}]
        text = summary.render(rows, "x")
        top = text.split("## Task 1")[0]
        self.assertIn("degenerate_run: 1 run(s)", top)
        self.assertIn("credential_exposed: 1 run(s)", top)
        self.assertIn("quality excludes 1 degenerate run", top)
        self.assertIn("| hidden_pass | 100% (1/1) | 50% (1/2) |", text)


class NoToolAnswers(unittest.TestCase):
    def test_answering_instead_of_acting_counts_against_quality_and_is_flagged(self) -> None:
        rows = [row(1, "before", 100, hidden_pass=True, fairness_test=True) for _ in range(10)]
        rows += [{**row(1, "after", 5, hidden_pass=False, fairness_test=False), "no_tool_answer": True} for _ in range(10)]
        text = summary.render(rows, "x")
        top = text.split("## Task 1")[0]
        self.assertIn("SIGNIFICANT QUALITY REGRESSION", top)
        self.assertIn("`no_tool_answer`: after 100% (10/10) > before 0% (0/10)", top)
        self.assertIn("| hidden_pass | 100% (10/10) | 0% (0/10) |", text)
        self.assertIn("| no_tool_answer | 0% (0/10) | 100% (10/10) |", text)
        self.assertIn("| duration_ms | 10 |", text)  # cost still counts them: this is what the variant did
        self.assertNotIn("excludes 10", top)

    def test_fewer_no_tool_answers_is_not_a_regression(self) -> None:
        rows = [{**row(1, "before", 1, hidden_pass=True), "no_tool_answer": True}, row(1, "after", 1, hidden_pass=True)]
        self.assertNotIn("QUALITY", summary.render(rows, "x").split("## Task 1")[0])


class CostWithoutNoToolAnswers(unittest.TestCase):
    def rows(self) -> list:
        rows = [row(1, "before", 1000, hidden_pass=True) for _ in range(10)]
        rows += [row(1, "after", d, hidden_pass=True) for d in (400, 450, 500, 550, 600, 650)]
        rows += [{**row(1, "after", 10, hidden_pass=False), "no_tool_answer": True} for _ in range(4)]
        return rows

    def test_the_main_cost_table_ignores_runs_that_did_not_act_and_warns(self) -> None:
        text = summary.render(self.rows(), "x")
        main, including = text.split("### Cost including no_tool_answer runs")
        main = main.split("### Cost: runs that used tools")[1]
        self.assertIn("-47.5%", next(l for l in main.splitlines() if l.startswith("| duration_ms |")))  # 525 vs 1000
        self.assertIn("-57.5%", next(l for l in including.splitlines() if l.startswith("| duration_ms |")))  # 425 vs 1000
        caution = text.index("Cost caution")
        self.assertLess(caution, text.index("### Cost: runs that used tools"))
        self.assertIn("before 0% (0/10), after 40% (4/10)", text[caution:caution + 300])

    def test_no_caution_when_the_rates_match(self) -> None:
        rows = [row(1, "before", 100, hidden_pass=True), row(1, "after", 50, hidden_pass=True)]
        self.assertNotIn("Cost caution", summary.render(rows, "x"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
