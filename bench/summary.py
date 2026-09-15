"""Summarise results.jsonl: cost per task and variant, and quality pass rates. Stdlib only.

Quality comes first. If `after` passes a quality check less often than
`before`, the summary says so above every cost number, and says whether the
drop is significant (Fisher exact, two-sided).

Cost and quality exclude different runs. Cost only uses runs that finished
cleanly. Quality drops only runs that say nothing about the skill (warmups,
harness crashes, aborts, leak suspects); a run that timed out or ended in an
error counts as a failure, or the variant that fails more would look better.
"""
from __future__ import annotations

import json
from pathlib import Path

from stats import bootstrap_median_diff, describe, fisher_exact

METRICS = ("duration_ms", "duration_ms_net", "num_turns", "output_tokens", "total_cost_usd")
VARIANTS = ("before", "after")
VALIDITY_CHECKS = ("skill_loaded",)  # whether the run measured the skill at all, not its quality
SIGNIFICANCE = 0.05

# Why a run is left out of the cost numbers. The first matching reason is the one reported.
EXCLUSIONS = (
    ("crashed", lambda r: "error" in r),
    ("warmup", lambda r: bool(r.get("warmup"))),
    ("env not isolated", lambda r: r.get("env_isolated") is not True),
    ("degenerate run", lambda r: r.get("degenerate_run") is True),
    ("timed out", lambda r: bool(r.get("timed_out"))),
    ("aborted", lambda r: bool(r.get("abort_reason"))),
    ("non-zero exit", lambda r: r.get("exit_code") != 0),
    ("is_error", lambda r: r.get("is_error") is True),
    ("non-success result", lambda r: r.get("result_subtype") != "success"),
    ("leak suspect", lambda r: r.get("leak_suspect") is True),
)


QUALITY_EXCLUDED = ("crashed", "warmup", "env not isolated", "degenerate run", "aborted", "leak suspect")  # no verdict on the skill at all
UNFINISHED = ("timed out", "non-zero exit", "is_error", "non-success result")  # a verdict: it failed
OUTCOME_CHECKS = ("hidden_pass", "suite_pass")  # graded from the workspace; missing means failed
COMPLETION = "clean_completion"
NO_TOOL = "no_tool_answer"  # rate of answering without acting: lower is better
LOWER_IS_BETTER = (NO_TOOL,)


def first_reason(row: dict, reasons: tuple) -> str | None:
    return next((reason for reason, applies in EXCLUSIONS if reason in reasons and applies(row)), None)


def exclusion(row: dict) -> str | None:
    return first_reason(row, tuple(reason for reason, _ in EXCLUSIONS))


def quality_exclusion(row: dict) -> str | None:
    return first_reason(row, QUALITY_EXCLUDED)


def unfinished(row: dict) -> str | None:
    return first_reason(row, UNFINISHED)


def quality_value(row: dict, name: str):
    """A check's verdict for the pass rate: an unfinished run never passes; undecidable stays undecidable."""
    if name == COMPLETION:
        return unfinished(row) is None
    if name == NO_TOOL:
        return row.get(NO_TOOL)
    found = row.get("checks", {}).get(name)
    if unfinished(row) is None:
        return found
    return None if found is None and name not in OUTCOME_CHECKS else False


def value(row: dict, metric: str):
    if metric == "duration_ms_net":  # API retry waits taken out
        duration = row.get("duration_ms")
        return None if duration is None else duration - (row.get("retry_delay_ms") or 0)
    return row.get(metric)


def counted(rows: list[dict], task, variant: str) -> list[dict]:
    return [r for r in rows if r.get("task") == task and r.get("variant") == variant and exclusion(r) is None]


def acted(rows: list[dict]) -> list[dict]:
    """Runs that used tools. A run that only answered ends in seconds and would make its variant look fast."""
    return [r for r in rows if r.get(NO_TOOL) is not True]


def cost_caution(groups: dict) -> list[str]:
    rates = {v: (sum(1 for r in groups[v] if r.get(NO_TOOL) is True), len(groups[v])) for v in VARIANTS}
    (bn, bt), (an, at) = rates["before"], rates["after"]
    if (bn / bt if bt else 0) == (an / at if at else 0):
        return []
    return [f"> **Cost caution**: no_tool_answer rates differ (before {pct(bn, bt)}, after {pct(an, at)}). "
            "Cost differences may come from runs that did not act rather than from the skill's efficiency.", ""]


def tasks(rows: list[dict]) -> list[int]:
    return sorted({r["task"] for r in rows if r.get("task") is not None})


def check_names(rows: list[dict]) -> list[str]:
    return [COMPLETION, NO_TOOL] + list(dict.fromkeys(name for r in rows for name in r.get("checks", {})))


def quality_rows(rows: list[dict], task, variant: str) -> list[dict]:
    return [r for r in rows if r.get("task") == task and r.get("variant") == variant and quality_exclusion(r) is None]


def rate(values: list) -> tuple[int, int, int]:
    """(passed, decided, undecidable)."""
    decided = [v for v in values if v is not None]
    return sum(1 for v in decided if v is True), len(decided), len(values) - len(decided)


def check_rates(rows: list[dict], task, name: str) -> tuple:
    return tuple(rate([quality_value(r, name) for r in quality_rows(rows, task, v)
                       if name in (COMPLETION, NO_TOOL) or name in r.get("checks", {}) or unfinished(r)])
                 for v in VARIANTS)


def pct(passed: int, decided: int, undecidable: int = 0) -> str:
    counts = f"{passed}/{decided}" + (f", {undecidable} undecidable" if undecidable else "")
    return f"{100 * passed / decided:.0f}% ({counts})" if decided else f"n/a ({counts})"


def regressions(rows: list[dict]) -> list[tuple[bool, str]]:
    """(significant, description) for every quality check `after` passes less often than `before`."""
    flags = []
    for task in tasks(rows):
        for name in check_names(rows):
            if name in VALIDITY_CHECKS:
                continue
            (bp, bd, _), (ap, ad, _) = check_rates(rows, task, name)
            if not (bd and ad):
                continue
            worse = ap / ad > bp / bd if name in LOWER_IS_BETTER else ap / ad < bp / bd
            if worse:
                p = fisher_exact(bp, bd - bp, ap, ad - ap)
                sign = ">" if name in LOWER_IS_BETTER else "<"
                flags.append((p < SIGNIFICANCE,
                              f"task {task} `{name}`: after {pct(ap, ad)} {sign} before {pct(bp, bd)} (p={p:.4f})"))
    return flags


def fmt(number) -> str:
    if number is None:
        return "-"
    return f"{number:.4f}" if abs(number) < 10 and number != int(number) else f"{number:,.0f}"


def signed(number) -> str:
    if number is None:
        return "-"
    return f"{number:+.4f}" if abs(number) < 10 and number != int(number) else f"{number:+,.0f}"


def change(before, after) -> str:
    if before in (None, 0) or after is None:
        return "n/a"
    return f"{100 * (after - before) / before:+.1f}%"


def banner(rows: list[dict]) -> list[str]:
    flags = regressions(rows)
    significant = [text for sig, text in flags if sig]
    minor = [text for sig, text in flags if not sig]
    lines = []
    if significant:
        lines += ["> **SIGNIFICANT QUALITY REGRESSION** (Fisher exact, p < 0.05). The cost numbers below are void:"]
        lines += [f"> - {text}" for text in significant] + [""]
    if minor:
        lines += ["> **QUALITY DROP (not significant)**. With small N this cannot rule out a real drop; add runs:"]
        lines += [f"> - {text}" for text in minor] + [""]
    if not flags:
        lines += ["No quality check passes less often in after than in before.", ""]
    polluted = sum(1 for r in rows if exclusion(r) == "env not isolated")
    if polluted:
        lines += [f"**env_isolated: {polluted} run(s)** loaded a plugin or MCP server other than the subject, or "
                  "showed no init event. They are excluded from every number below; fix the environment and rerun them.",
                  ""]
    degenerate = sum(1 for r in rows if exclusion(r) == "degenerate run")
    if degenerate:
        lines += [f"**degenerate_run: {degenerate} run(s)** made no tool call and produced nothing (no output "
                  "tokens, nothing billed, or an error): the environment ended them, not the model. Excluded from every number below.", ""]
    exposed = sum(1 for r in rows if r.get("credential_exposed") is True)
    if exposed:
        lines += [f"**credential_exposed: {exposed} run(s)** ran a command that may print the environment. "
                  "Known credential values were redacted from every file; check raw/ anyway.", ""]
    leaks = sum(1 for r in rows if r.get("leak_suspect") is True)
    if leaks:
        lines += [f"**leak_suspect: {leaks} run(s)** touched the bench directory or grading material. "
                  "They are excluded from every number below; inspect raw/ and rerun them.", ""]
    return lines


def validity_lines(rows: list[dict]) -> list[str]:
    lines = []
    for variant in VARIANTS:
        mine = [r for r in rows if r.get("variant") == variant]
        if not mine:
            continue
        cost = [exclusion(r) for r in mine]
        quality = [quality_exclusion(r) for r in mine]
        failed = [unfinished(r) for r, q in zip(mine, quality) if q is None]
        kept = [r for r, reason in zip(mine, cost) if reason is None]
        passed, decided, unknown = rate([r.get("checks", {}).get("skill_loaded") for r in kept])
        lines.append(f"- `{variant}`: {len(mine)} runs; cost excludes {tally(cost)}; "
                     f"quality excludes {tally(quality)}, counted as failed: {tally(failed)}; "
                     f"skill_loaded (clean runs) true {passed} / false {decided - passed} / unknown {unknown}")
    return lines


def tally(reasons: list) -> str:
    counts = [f"{reasons.count(reason)} {reason}" for reason, _ in EXCLUSIONS if reasons.count(reason)]
    return ", ".join(counts) or "none"


def metric_table(groups: dict) -> list[str]:
    lines = ["| metric | before n | before median [IQR] | after n | after median [IQR] | change | "
             "median diff [95% CI] |", "|---|---|---|---|---|---|---|"]
    for metric in METRICS:
        values = {v: [x for x in (value(r, metric) for r in groups[v]) if x is not None] for v in VARIANTS}
        (bn, bm, bq1, bq3), (an, am, aq1, aq3) = (describe(values[v]) for v in VARIANTS)
        ci = bootstrap_median_diff(values["before"], values["after"])
        diff = None if bm is None or am is None else am - bm
        interval = "-" if ci is None else f"{signed(diff)} [{signed(ci[0])}, {signed(ci[1])}]"
        lines.append(f"| {metric} | {bn} | {fmt(bm)} [{fmt(bq1)}–{fmt(bq3)}] | {an} | "
                     f"{fmt(am)} [{fmt(aq1)}–{fmt(aq3)}] | {change(bm, am)} | {interval} |")
    return lines


def diagnostics(groups: dict) -> list[str]:
    tokens = {v: describe([r.get("first_turn_input_tokens") for r in groups[v]])[1] for v in VARIANTS}
    size = {v: describe([r.get("skill_md_bytes") for r in groups[v]])[1] for v in VARIANTS}
    token_diff = None if None in tokens.values() else tokens["after"] - tokens["before"]
    byte_diff = None if None in size.values() else size["after"] - size["before"]
    retries = {v: (sum(r.get("api_retries") or 0 for r in groups[v]),
                   sum(r.get("retry_delay_ms") or 0 for r in groups[v])) for v in VARIANTS}
    lines = [
        f"- first_turn_input_tokens median: before {fmt(tokens['before'])}, after {fmt(tokens['after'])}, "
        f"diff {signed(token_diff)} tokens vs SKILL.md {signed(byte_diff)} bytes. Same sign expected; "
        "a diff near 0 means the skill may not have been expanded and the comparison is void.",
        f"- api retries: before {retries['before'][0]} ({retries['before'][1]:,} ms), "
        f"after {retries['after'][0]} ({retries['after'][1]:,} ms)",
        "", "| position | before median duration_ms (n) | after median duration_ms (n) |", "|---|---|---|",
    ]
    for position, label in ((0, "ran first"), (1, "ran second")):
        cells = []
        for v in VARIANTS:
            n, med, _, _ = describe([r.get("duration_ms") for r in groups[v] if r.get("position") == position])
            cells.append(f"{fmt(med)} ({n})")
        lines.append(f"| {label} | {cells[0]} | {cells[1]} |")
    return lines


def task_section(rows: list[dict], task) -> list[str]:
    everything = {v: counted(rows, task, v) for v in VARIANTS}
    groups = {v: acted(everything[v]) for v in VARIANTS}
    lines = [f"## Task {task}", ""] + cost_caution(everything)
    lines += ["### Cost: runs that used tools (no_tool_answer runs excluded)", ""] + metric_table(groups) + [""]
    lines += ["### Cost including no_tool_answer runs", ""] + metric_table(everything) + [""] + diagnostics(groups)
    lines += ["", "| check | before | after | Fisher p |", "|---|---|---|---|"]
    for name in check_names(rows):
        (bp, bd, bu), (ap, ad, au) = check_rates(rows, task, name)
        p = f"{fisher_exact(bp, bd - bp, ap, ad - ap):.4f}" if bd and ad else "-"
        lines.append(f"| {name} | {pct(bp, bd, bu)} | {pct(ap, ad, au)} | {p} |")
    return lines + [""]


def render(rows: list[dict], label: str) -> str:
    lines = [f"# Bench summary: {label}", ""] + banner(rows)
    lines += ["Runs:"] + validity_lines(rows) + [""]
    lines += [
        "Cost numbers count only runs that finished cleanly. Pass rates leave out crashes, warmups, aborts and leak",
        "suspects; a run that timed out, exited non-zero, hit is_error or a non-success result counts as failed on",
        "every check it could not pass (undecidable results stay out, with their count shown).",
        "clean_completion is the share of those runs that finished cleanly.",
        "duration_ms_net is duration_ms minus API retry waits. CIs: bootstrap, 2000 resamples, fixed seed.",
        "",
    ]
    for task in tasks(rows):
        lines += task_section(rows, task)
    return "\n".join(lines)


def read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_summary(out_dir: Path, results: str = "results.jsonl", name: str = "summary.md") -> str:
    text = render(read_rows(out_dir / results), out_dir.name)
    (out_dir / name).write_text(text + "\n", encoding="utf-8")
    return text
