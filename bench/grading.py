"""Everything computed from a finished run: outcome checks on its workspace, and transcript fields. Stdlib only."""
from __future__ import annotations

from pathlib import Path

import checks
import quality
from prep import Subject

BENCH = Path(__file__).resolve().parent


def grade(subject: Subject, workspace: Path, events: list[dict], skill_dirs: list[str]) -> dict:
    calls = checks.tool_calls(events)
    results = quality.quality_checks(
        workspace, fixture=subject.cache / subject.fixture, source=subject.source,
        test_command=list(subject.test_command),
        mutants={name: subject.dir / path for name, path in subject.mutants.items()},
        hidden_test=subject.dir / subject.hidden_test, wrapper=subject.dir / subject.mutant_wrapper,
        sentinel=subject.dir / subject.sentinel,
    )
    return {
        **results,
        "red_before_fix": checks.red_before_fix(calls, subject.source),
        "suite_run_after_fix": checks.suite_run_after_fix(calls, subject.source),
        "skill_loaded": checks.skill_loaded(events, subject.command, skill_dirs),
    }


def transcript_fields(events: list[dict], subject: Subject, auth: str) -> dict:
    """Validity fields: did this run measure the skill, in a clean environment, without touching secrets?"""
    calls = checks.tool_calls(events)
    return {
        "leak_suspect": checks.leak_suspect(calls, [str(BENCH)]),
        "env_isolated": checks.env_isolated(events, subject.plugin, auth),
        "api_key_source": checks.api_key_source(events),
        "degenerate_run": checks.degenerate_run(events),
        "no_tool_answer": checks.no_tool_answer(events),
        "credential_exposed": checks.credential_exposed(calls),
    }
