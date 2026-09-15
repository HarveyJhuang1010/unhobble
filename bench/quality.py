"""Outcome checks: hidden acceptance test and mutation checks on copies of a workspace. Stdlib only."""
from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
import shutil
import tempfile
from pathlib import Path

from proc import Outcome, run_process

CHECK_TIMEOUT = 120  # seconds for one test-suite run
TEST_TOTAL = re.compile(r"^\s*(?:#|ℹ)\s*tests\s+(\d+)\s*$", re.M)  # node's TAP and spec reporters
FAIL_TOTAL = re.compile(r"^\s*(?:#|ℹ)\s*fail\s+(\d+)\s*$", re.M)
TEST_PATH = re.compile(r"(?:^|[/._-])(?:tests?|specs?|__tests__)(?:[/._-]|$)")
SCRIPT_EXT = re.compile(r"\.[cm]?[jt]sx?$")
SKIP_DIRS = {".git", "node_modules"}


def check_env() -> dict:
    """The environment for running tests the model wrote: nothing that can authenticate anywhere."""
    def secret(name: str) -> bool:
        return name.startswith(("ANTHROPIC_", "CLAUDE_CODE_")) or any(w in name.upper() for w in ("TOKEN", "KEY", "SECRET"))
    return {**{k: v for k, v in os.environ.items() if not secret(k)}, "npm_config_update_notifier": "false"}


def run_suite(cwd: Path, test_command: list[str], timeout: float = CHECK_TIMEOUT) -> Outcome:
    return run_process(list(test_command), cwd, check_env(), timeout)


def run_tests(cwd: Path, test_command: list[str], timeout: float = CHECK_TIMEOUT) -> int | None:
    return run_suite(cwd, test_command, timeout).exit_code


def total(pattern: re.Pattern, output: str) -> int | None:
    found = pattern.findall(output)
    return sum(int(n) for n in found) if found else None


def test_count(output: str) -> int | None:
    """Tests the runner reports; None when no node test runner summary is in the output."""
    return total(TEST_TOTAL, output)


def fail_count(output: str) -> int | None:
    return total(FAIL_TOTAL, output)


def hidden_passes(hidden_test: Path, target: Path, cwd: Path, timeout: float = CHECK_TIMEOUT) -> bool:
    env = {**check_env(), "BENCH_MODULE": str(target)}
    return run_process(["node", "--test", str(hidden_test)], cwd, env, timeout).exit_code == 0


# ------------------------------------------------------------- test changes


def tree(root: Path) -> dict[str, Path]:
    return {p.relative_to(root).as_posix(): p for p in root.rglob("*")
            if p.is_file() and not SKIP_DIRS & set(p.relative_to(root).parts)}


def script_patterns(workspace: Path) -> list[str]:
    """Paths and globs named by package.json's test script."""
    try:
        script = json.loads((workspace / "package.json").read_text(encoding="utf-8"))["scripts"]["test"]
        words = shlex.split(script)
    except (OSError, ValueError, KeyError, TypeError):
        return []
    return [re.sub(r"^\./", "", w) for w in words
            if not w.startswith("-") and ("/" in w or "*" in w or SCRIPT_EXT.search(w))]


def is_test_file(rel: str, patterns: list[str]) -> bool:
    return bool(TEST_PATH.search(rel) and SCRIPT_EXT.search(rel)) or any(fnmatch.fnmatch(rel, p) for p in patterns)


def changed_tests(workspace: Path, fixture: Path) -> list[str]:
    """Test files the model added or modified, relative to the fixture it started from."""
    before, after = tree(fixture), tree(workspace)
    patterns = script_patterns(workspace)
    return sorted(rel for rel, path in after.items()
                  if (rel not in before or path.read_bytes() != before[rel].read_bytes()) and is_test_file(rel, patterns))


# ---------------------------------------------------------------- mutation


def wrap_with(copy: Path, source: str, wrong: Path, template: Path) -> None:
    """Keep the model's module as <name>.model.js and put a wrapper with a wrong allocation in its place."""
    target = copy / source
    model = target.with_name(target.stem + ".model" + target.suffix)
    target.rename(model)
    text = template.read_text(encoding="utf-8")
    text = text.replace("__MODEL__", json.dumps("./" + model.name)).replace("__WRONG__", json.dumps(str(wrong)))
    target.write_text(text, encoding="utf-8")


def fails_more(outcome: Outcome, reference: Outcome) -> bool:
    """More failing tests than on the model's own code; exit codes only when counts are missing."""
    mine, theirs = fail_count(outcome.stdout + outcome.stderr), fail_count(reference.stdout + reference.stderr)
    if mine is not None and theirs is not None:
        return mine > theirs
    return outcome.exit_code != 0 and reference.exit_code == 0


def mutation_checks(workspace: Path, copy, suite: Outcome, *, fixture: Path, source: str, test_command: list[str],
                    wrongs: dict[str, Path], wrapper: Path, sentinel: Path, timeout: float) -> dict:
    """Each check: does the model's test work fail more once the allocation is known-wrong?

    False  no test file was added or changed (nothing was written to catch the bug),
           or the suite fails on the model's own fix (it would fail on anything);
    None   the suite's output has no readable test count, or the model's tests do
           not fail more even when every share is -1 (they never reach the
           allocation through the source file, so a swap there proves nothing);
    True   the model's test work fails more under this wrong allocation.

    Only changed test files take part in the comparison: the fixture's own
    untouched tests are removed from the copies, so they cannot pass the probe
    on the model's behalf.
    """
    tests = changed_tests(workspace, fixture)
    if not tests:
        return dict.fromkeys(wrongs, False)
    if test_count(suite.stdout + suite.stderr) is None:
        return dict.fromkeys(wrongs, None)
    if suite.exit_code != 0:
        return dict.fromkeys(wrongs, False)
    patterns = script_patterns(workspace)
    untouched = [rel for rel in tree(fixture) if is_test_file(rel, patterns) and rel not in tests]

    def model_tests_only(label: str, wrong: Path | None = None) -> Path:
        dest = copy(label)
        for rel in untouched:
            if (dest / rel).is_file():
                (dest / rel).unlink()
        if wrong is not None:
            wrap_with(dest, source, wrong, wrapper)
        return dest

    reference = run_suite(model_tests_only("reference"), test_command, timeout)
    if not fails_more(run_suite(model_tests_only("sentinel", sentinel), test_command, timeout), reference):
        return dict.fromkeys(wrongs, None)
    return {name: fails_more(run_suite(model_tests_only(name, wrong), test_command, timeout), reference)
            for name, wrong in wrongs.items()}


def quality_checks(workspace: Path, *, fixture: Path, source: str, test_command: list[str], mutants: dict[str, Path],
                   hidden_test: Path, wrapper: Path, sentinel: Path, timeout: float = CHECK_TIMEOUT) -> dict:
    """Outcome checks, each on its own copy of the workspace. repro_test swaps in the fixture's original bug."""
    with tempfile.TemporaryDirectory(prefix="bench-check-") as tmp:
        base = Path(tmp)

        def copy(label: str) -> Path:
            dest = base / label
            shutil.copytree(workspace, dest, symlinks=True, ignore=shutil.ignore_patterns(".git"))
            return dest

        suite = run_suite(copy("suite"), test_command, timeout)
        results = {
            "hidden_pass": hidden_passes(hidden_test, copy("hidden") / source, base, timeout),
            "suite_pass": suite.exit_code == 0,
        }
        results.update(mutation_checks(
            workspace, copy, suite, fixture=fixture, source=source, test_command=test_command,
            wrongs={"repro_test": fixture / source, **mutants}, wrapper=wrapper, sentinel=sentinel, timeout=timeout,
        ))
        return results
