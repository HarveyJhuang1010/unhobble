# Bench results

Pooled with `python3 bench/run.py summarize bench/results/20260915-231611 bench/results/20260916-000752 --out ...`.
Task 1: two batches, 30 counted runs per version. Task 2: first batch only, 10 counted runs per version.
Model claude-sonnet-5, `--auth oauth`, 2026-09-15/16. Raw transcripts are not published.


Batches: 20260915-231611 (44 rows), 20260916-000752 (42 rows)

> **QUALITY DROP (not significant)**. With small N this cannot rule out a real drop; add runs:
> - task 1 `fairness_test`: after 10% (3/30) < before 20% (6/30) (p=0.4716)

Runs:
- `before`: 43 runs; cost excludes 3 warmup; quality excludes 3 warmup, counted as failed: none; skill_loaded (clean runs) true 0 / false 0 / unknown 40
- `after`: 43 runs; cost excludes 3 warmup; quality excludes 3 warmup, counted as failed: none; skill_loaded (clean runs) true 0 / false 0 / unknown 40

Cost numbers count only runs that finished cleanly. Pass rates leave out crashes, warmups, aborts and leak
suspects; a run that timed out, exited non-zero, hit is_error or a non-success result counts as failed on
every check it could not pass (undecidable results stay out, with their count shown).
clean_completion is the share of those runs that finished cleanly.
duration_ms_net is duration_ms minus API retry waits. CIs: bootstrap, 2000 resamples, fixed seed.

## Task 1

### Cost: runs that used tools (no_tool_answer runs excluded)

| metric | before n | before median [IQR] | after n | after median [IQR] | change | median diff [95% CI] |
|---|---|---|---|---|---|---|
| duration_ms | 30 | 26,447 [23,295–29,353] | 30 | 26,606 [21,724–30,549] | +0.6% | +158 [-4,484, +3,436] |
| duration_ms_net | 30 | 26,447 [23,295–29,353] | 30 | 26,606 [21,724–30,549] | +0.6% | +158 [-4,484, +3,436] |
| num_turns | 30 | 10 [10–10] | 30 | 10 [10–11] | +0.0% | +0 [+0, +1] |
| output_tokens | 30 | 1,638 [1,584–1,812] | 30 | 1,724 [1,635–1,848] | +5.3% | +86 [-5.0375, +160] |
| total_cost_usd | 30 | 0.1542 [0.1535–0.1569] | 30 | 0.1314 [0.1263–0.1400] | -14.8% | -0.0228 [-0.0271, -0.0164] |

### Cost including no_tool_answer runs

| metric | before n | before median [IQR] | after n | after median [IQR] | change | median diff [95% CI] |
|---|---|---|---|---|---|---|
| duration_ms | 30 | 26,447 [23,295–29,353] | 30 | 26,606 [21,724–30,549] | +0.6% | +158 [-4,484, +3,436] |
| duration_ms_net | 30 | 26,447 [23,295–29,353] | 30 | 26,606 [21,724–30,549] | +0.6% | +158 [-4,484, +3,436] |
| num_turns | 30 | 10 [10–10] | 30 | 10 [10–11] | +0.0% | +0 [+0, +1] |
| output_tokens | 30 | 1,638 [1,584–1,812] | 30 | 1,724 [1,635–1,848] | +5.3% | +86 [-5.0375, +160] |
| total_cost_usd | 30 | 0.1542 [0.1535–0.1569] | 30 | 0.1314 [0.1263–0.1400] | -14.8% | -0.0228 [-0.0271, -0.0164] |

- first_turn_input_tokens median: before 35,512, after 31,189, diff -4,323 tokens vs SKILL.md -11,772 bytes. Same sign expected; a diff near 0 means the skill may not have been expanded and the comparison is void.
- api retries: before 0 (0 ms), after 0 (0 ms)

| position | before median duration_ms (n) | after median duration_ms (n) |
|---|---|---|
| ran first | 27,102 (15) | 27,083 (15) |
| ran second | 26,115 (15) | 22,099 (15) |

| check | before | after | Fisher p |
|---|---|---|---|
| clean_completion | 100% (30/30) | 100% (30/30) | 1.0000 |
| no_tool_answer | 0% (0/30) | 0% (0/30) | 1.0000 |
| hidden_pass | 100% (30/30) | 100% (30/30) | 1.0000 |
| suite_pass | 100% (30/30) | 100% (30/30) | 1.0000 |
| repro_test | 100% (30/30) | 100% (30/30) | 1.0000 |
| fairness_test | 20% (6/30) | 10% (3/30) | 0.4716 |
| rejects_dump_last | 100% (30/30) | 100% (30/30) | 1.0000 |
| red_before_fix | 97% (29/30) | 100% (30/30) | 1.0000 |
| suite_run_after_fix | 100% (30/30) | 100% (30/30) | 1.0000 |
| skill_loaded | n/a (0/0, 30 undecidable) | n/a (0/0, 30 undecidable) | - |

## Task 2

### Cost: runs that used tools (no_tool_answer runs excluded)

| metric | before n | before median [IQR] | after n | after median [IQR] | change | median diff [95% CI] |
|---|---|---|---|---|---|---|
| duration_ms | 10 | 44,102 [42,443–47,210] | 10 | 43,656 [41,044–50,092] | -1.0% | -446 [-5,891, +6,887] |
| duration_ms_net | 10 | 44,102 [42,443–47,210] | 10 | 43,656 [41,044–50,092] | -1.0% | -446 [-5,891, +6,887] |
| num_turns | 10 | 10 [10–11] | 10 | 11 [11–12] | +4.8% | +0.5000 [+0, +2] |
| output_tokens | 10 | 3,013 [2,930–3,268] | 10 | 3,000 [2,690–3,224] | -0.4% | -13 [-477, +343] |
| total_cost_usd | 10 | 0.1807 [0.1757–0.1919] | 10 | 0.1676 [0.1609–0.1739] | -7.3% | -0.0131 [-0.0289, -0.0038] |

### Cost including no_tool_answer runs

| metric | before n | before median [IQR] | after n | after median [IQR] | change | median diff [95% CI] |
|---|---|---|---|---|---|---|
| duration_ms | 10 | 44,102 [42,443–47,210] | 10 | 43,656 [41,044–50,092] | -1.0% | -446 [-5,891, +6,887] |
| duration_ms_net | 10 | 44,102 [42,443–47,210] | 10 | 43,656 [41,044–50,092] | -1.0% | -446 [-5,891, +6,887] |
| num_turns | 10 | 10 [10–11] | 10 | 11 [11–12] | +4.8% | +0.5000 [+0, +2] |
| output_tokens | 10 | 3,013 [2,930–3,268] | 10 | 3,000 [2,690–3,224] | -0.4% | -13 [-477, +343] |
| total_cost_usd | 10 | 0.1807 [0.1757–0.1919] | 10 | 0.1676 [0.1609–0.1739] | -7.3% | -0.0131 [-0.0289, -0.0038] |

- first_turn_input_tokens median: before 35,612, after 31,284, diff -4,328 tokens vs SKILL.md -11,772 bytes. Same sign expected; a diff near 0 means the skill may not have been expanded and the comparison is void.
- api retries: before 0 (0 ms), after 0 (0 ms)

| position | before median duration_ms (n) | after median duration_ms (n) |
|---|---|---|
| ran first | 43,812 (5) | 41,667 (5) |
| ran second | 44,393 (5) | 45,645 (5) |

| check | before | after | Fisher p |
|---|---|---|---|
| clean_completion | 100% (10/10) | 100% (10/10) | 1.0000 |
| no_tool_answer | 0% (0/10) | 0% (0/10) | 1.0000 |
| hidden_pass | 100% (10/10) | 100% (10/10) | 1.0000 |
| suite_pass | 100% (10/10) | 100% (10/10) | 1.0000 |
| repro_test | 100% (10/10) | 100% (10/10) | 1.0000 |
| fairness_test | 50% (5/10) | 70% (7/10) | 0.6499 |
| rejects_dump_last | 100% (10/10) | 100% (10/10) | 1.0000 |
| red_before_fix | 60% (6/10) | 90% (9/10) | 0.3034 |
| suite_run_after_fix | 80% (8/10) | 90% (9/10) | 1.0000 |
| skill_loaded | n/a (0/0, 10 undecidable) | n/a (0/0, 10 undecidable) | - |

