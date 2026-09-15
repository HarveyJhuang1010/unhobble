"""prepare, preflight and the run pipeline with a fake claude."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tarfile
import time
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import checks  # noqa: E402
import prep  # noqa: E402
import proc  # noqa: E402
import quality  # noqa: E402
import run  # noqa: E402
from fakes import *  # noqa: F401,F403


# ----------------------------------------------------------------- run pipeline

ORIGINAL_SKILL = "---\nname: test-driven-development\ndescription: long\n---\nORIGINAL SKILL\n"
SLIM_SKILL = "---\nname: test-driven-development\ndescription: short\n---\nSLIM\n"


class Subject(TempDirCase):
    def test_real_subject_file_uses_the_eval_prompts_verbatim(self) -> None:
        subject = prep.load_subject(SUBJECT, self.root)
        self.assertEqual(subject.command, COMMAND)
        self.assertEqual(subject.tasks[1], "Finance filed the reconciliation bug written up in BUG.md. Fix it.")
        self.assertTrue(subject.tasks[2].startswith("The tech lead says the lost-cent bug in BUG.md is a one-liner"))
        self.assertEqual(subject.commit, "be4e44a9fbc5e8df0beaefadbb28bd22ee61cc39")


class PipelineCase(TempDirCase):
    def setUp(self) -> None:
        super().setUp()
        origin = self.root / "origin"
        write(origin, "skills/test-driven-development/SKILL.md", ORIGINAL_SKILL)
        write(origin, "references/testing-patterns.md", "patterns\n")
        write_fixture(origin / "evals/fixtures/test-driven-development")
        git(origin, "init", "-q")
        git(origin, "add", "-A")
        git(origin, "commit", "-qm", "c1")
        self.commit = git(origin, "rev-parse", "HEAD")
        subject_dir = self.root / "subjects" / SUBJECT.name
        shutil.copytree(SUBJECT, subject_dir, ignore=shutil.ignore_patterns("after"))
        spec = json.loads((subject_dir / "subject.json").read_text(encoding="utf-8"))
        spec.update(repo=str(origin), commit=self.commit)
        write(subject_dir, "subject.json", json.dumps(spec))
        write(subject_dir, "after/SKILL.md", SLIM_SKILL)
        self.subject = prep.load_subject(subject_dir, self.root / "cache")
        self.env = {"ANTHROPIC_API_KEY": "test-api-key-0123456789abcdef", "PATH": "/usr/bin", "CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "cli"}

    def fake_model(self, seen: list, crash_on: int | None = None):
        def runner(cmd, cwd, env, timeout):
            plugin = Path(cmd[cmd.index("--plugin-dir") + 1])
            seen.append({"cmd": cmd, "cwd": cwd, "env": env, "timeout": timeout, "plugin": {
                p.relative_to(plugin).as_posix(): p.read_text(encoding="utf-8")
                for p in sorted(plugin.rglob("*")) if p.is_file()}})
            if len(seen) == crash_on:
                raise RuntimeError("claude crashed")
            write(cwd, "test/split.test.js", TEST_HEAD + LOST_CENT + FAIRNESS)
            write(cwd, "src/split.js", CORRECT)
            write(cwd, "node_modules/big/index.js", "junk")
            (cwd / "BUG.md").unlink()  # a deletion must survive the round trip
            skill = plugin / "skills/test-driven-development"
            source = "none" if env.get("CLAUDE_CODE_OAUTH_TOKEN") else "ANTHROPIC_API_KEY"
            stdout = jsonl(init(api_key_source=source), assistant({"type": "tool_use", "id": "r", "name": "Read",
                                              "input": {"file_path": str(skill / "SKILL.md")}}),
                           assistant(bash("a", "npm test")), reply("a", "Exit code 1\n" + FAILING, True),
                           assistant(edit("b", str(cwd / "src/split.js"))), reply("b", "ok"),
                           assistant(bash("c", "npm test")), reply("c", PASSING), RESULT)
            return proc.Outcome(stdout=stdout, stderr="", exit_code=0, timed_out=False)
        return runner


class Pipeline(PipelineCase):
    def test_prepare_clones_the_pinned_commit_once(self) -> None:
        prep.prepare(self.subject, log=lambda _: None)
        self.assertEqual(prep.git_head(self.subject.cache), self.commit)
        (self.subject.cache / "marker").write_text("kept")
        prep.prepare(self.subject, log=lambda _: None)
        self.assertTrue((self.subject.cache / "marker").exists())

    def test_preflight_lists_every_missing_prerequisite(self) -> None:
        (self.subject.dir / "after/SKILL.md").unlink()
        errors = prep.preflight(self.subject, {}, which=lambda name: None)
        joined = "\n".join(errors)
        for needle in ("ANTHROPIC_API_KEY", "after/SKILL.md", "node", "claude", "prepare"):
            self.assertIn(needle, joined)

    def test_preflight_rejects_a_cache_at_the_wrong_commit(self) -> None:
        prep.prepare(self.subject, log=lambda _: None)
        git(self.subject.cache, "commit", "-q", "--allow-empty", "-m", "drift")
        errors = prep.preflight(self.subject, self.env, which=lambda name: "/bin/" + name)
        self.assertEqual(len(errors), 1)
        self.assertIn(self.commit, errors[0])

    @unittest.skipIf(shutil.which("node") is None, "node is required for the quality checks")
    def test_batch_records_every_run_and_survives_a_crash(self) -> None:
        prep.prepare(self.subject, log=lambda _: None)
        seen: list = []
        out = self.root / "results"
        slots = run.schedule(runs=2, tasks=[1], seed=1)
        run.run_batch(self.subject, slots, model="m", seed=1, out_dir=out,
                      runner=self.fake_model(seen, crash_on=2), timeout=5, env=self.env, log=lambda _: None)

        rows = [json.loads(line) for line in (out / "results.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 4)
        self.assertIn("claude crashed", rows[1]["error"])
        good = rows[0]
        self.assertEqual((good["subject"], good["task"], good["model"], good["timeout"]), ("test-driven-development", 1, "m", 5))
        self.assertEqual((good["duration_ms"], good["exit_code"], good["timed_out"]), (1234, 0, False))
        self.assertEqual(good["checks"], {"hidden_pass": True, "suite_pass": True, "repro_test": True,
                                          "fairness_test": True, "rejects_dump_last": True,
                                          "red_before_fix": True, "suite_run_after_fix": True, "skill_loaded": True})
        self.assertTrue((out / good["raw"]).is_file())
        self.assertIn("src/split.js", (out / good["diff"]).read_text(encoding="utf-8"))

        cmd = seen[0]["cmd"]
        self.assertEqual(cmd[:3], ["claude", "--bare", "-p"])
        self.assertEqual(cmd[3], "/" + COMMAND + " " + self.subject.tasks[1])
        for flag in ("--add-dir", "--model", "--no-session-persistence", "--verbose"):
            self.assertIn(flag, cmd)
        self.assertEqual(cmd[cmd.index("--output-format") + 1], "stream-json")
        self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "acceptEdits")
        allowed = cmd[cmd.index("--allowedTools") + 1:]
        self.assertIn("Edit", allowed)
        self.assertNotIn("Bash", allowed)  # only prefixed Bash
        self.assertFalse(any(tool.startswith("Bash(sed") for tool in allowed))

        env = seen[0]["env"]
        self.assertNotIn("CLAUDECODE", env)
        self.assertNotIn("CLAUDE_CODE_ENTRYPOINT", env)
        self.assertEqual(env["ANTHROPIC_API_KEY"], "test-api-key-0123456789abcdef")
        self.assertEqual(Path(env["CLAUDE_CONFIG_DIR"]).parent, Path(seen[0]["cwd"]).parent)  # per-run, not ~/.claude
        self.assertNotEqual(seen[0]["env"]["CLAUDE_CONFIG_DIR"], seen[2]["env"]["CLAUDE_CONFIG_DIR"])

    @unittest.skipIf(shutil.which("node") is None, "node is required for the quality checks")
    def test_variants_differ_only_in_skill_md(self) -> None:
        prep.prepare(self.subject, log=lambda _: None)
        seen: list = []
        slots = run.schedule(runs=1, tasks=[1], seed=1)
        runner = self.fake_model(seen)
        run.run_batch(self.subject, slots, model="m", seed=1, out_dir=self.root / "r",
                      runner=runner, timeout=5, env=self.env, log=lambda _: None)
        by_variant = {}
        for entry, slot in zip(seen, slots):
            by_variant[slot.variant] = entry
        before, after = by_variant["before"], by_variant["after"]
        skill = "skills/test-driven-development/SKILL.md"
        self.assertEqual((before["plugin"][skill], after["plugin"][skill]), (ORIGINAL_SKILL, SLIM_SKILL))
        self.assertEqual({k: v for k, v in before["plugin"].items() if k != skill},
                         {k: v for k, v in after["plugin"].items() if k != skill})
        self.assertIn("references/testing-patterns.md", before["plugin"])
        self.assertEqual(json.loads(before["plugin"][".claude-plugin/plugin.json"]), {"name": "subject"})

        def normalise(entry: dict) -> list:
            root = str(Path(entry["cwd"]).parent)
            return [part.replace(root, "<tmp>") for part in entry["cmd"]]
        self.assertEqual(normalise(before), normalise(after))


class PreflightIntegrity(PipelineCase):
    def errors(self) -> str:
        return "\n".join(prep.preflight(self.subject, self.env, which=lambda name: "/bin/" + name))

    def test_a_clean_pinned_cache_passes(self) -> None:
        prep.prepare(self.subject, log=lambda _: None)
        self.assertEqual(self.errors(), "")

    def test_a_dirty_cache_is_rejected(self) -> None:
        prep.prepare(self.subject, log=lambda _: None)
        write(self.subject.cache, "stray.txt", "x")
        self.assertIn("uncommitted", self.errors())

    def test_an_edited_original_skill_is_rejected(self) -> None:
        prep.prepare(self.subject, log=lambda _: None)
        write(self.subject.cache, "skills/test-driven-development/SKILL.md", SLIM_SKILL + "edited\n")
        self.assertIn("differs from the pinned commit", self.errors())

    def test_an_after_identical_to_before_is_rejected(self) -> None:
        prep.prepare(self.subject, log=lambda _: None)
        write(self.subject.dir, "after/SKILL.md", ORIGINAL_SKILL)
        self.assertIn("identical", self.errors())

    def test_an_after_with_another_skill_name_is_rejected(self) -> None:
        prep.prepare(self.subject, log=lambda _: None)
        write(self.subject.dir, "after/SKILL.md", SLIM_SKILL.replace("name: test-driven-development", "name: tdd"))
        self.assertIn("name", self.errors())


FAKE_CLAUDE = r"""
import json, os, sys, time
open(sys.argv[1], "w").write(str(os.getpid()))
print(json.dumps({"type": "system", "subtype": "init"}), flush=True)
if sys.argv[2] == "auth":
    print(json.dumps({"type": "system", "subtype": "api_retry", "attempt": 1, "max_retries": 10,
                      "retry_delay_ms": 577, "error_status": 401, "error": "authentication_failed"}), flush=True)
time.sleep(60)
"""


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class Streaming(TempDirCase):
    def start(self, mode: str, **kwargs):
        pid_file = self.root / "pid"
        started = time.time()
        outcome = proc.stream_process([sys.executable, "-c", FAKE_CLAUDE, str(pid_file), mode], self.root,
                                      dict(os.environ), **kwargs)
        return outcome, int(pid_file.read_text()), time.time() - started

    def test_an_authentication_retry_kills_the_run_at_once(self) -> None:
        outcome, pid, elapsed = self.start("auth", timeout=30, should_abort=checks.auth_failure)
        self.assertLess(elapsed, 10)
        self.assertIn("401", outcome.abort_reason)
        self.assertIsNone(outcome.exit_code)
        self.assertIn("api_retry", outcome.stdout)
        self.assertFalse(alive(pid))

    def test_timeout_kills_a_silent_run(self) -> None:
        outcome, pid, elapsed = self.start("idle", timeout=1, should_abort=checks.auth_failure)
        self.assertTrue(outcome.timed_out)
        self.assertIsNone(outcome.abort_reason)
        self.assertLess(elapsed, 10)
        self.assertFalse(alive(pid))

    def test_ctrl_c_kills_the_child_and_propagates(self) -> None:
        def interrupt(line: str):
            raise KeyboardInterrupt
        pid_file = self.root / "pid"
        with self.assertRaises(KeyboardInterrupt):
            proc.stream_process([sys.executable, "-c", FAKE_CLAUDE, str(pid_file), "idle"], self.root,
                                dict(os.environ), timeout=30, should_abort=interrupt)
        time.sleep(0.2)
        self.assertFalse(alive(int(pid_file.read_text())))


def outcome_with(result: dict) -> "proc.Outcome":
    return proc.Outcome(stdout=jsonl(init(), result), stderr="", exit_code=0, timed_out=False)


@unittest.skipIf(shutil.which("npm") is None, "npm is required for the quality checks")
class BatchSafety(PipelineCase):
    def batch(self, runner, runs: int = 3) -> tuple:
        prep.prepare(self.subject, log=lambda _: None)
        out = self.root / "results"
        status = run.run_batch(self.subject, run.schedule(runs, [1], seed=1), model="m", seed=1, out_dir=out,
                               runner=runner, timeout=5, env=self.env, log=lambda _: None)
        lines = (out / "results.jsonl").read_text(encoding="utf-8").splitlines()
        return status, [json.loads(line) for line in lines]

    def test_an_authentication_failure_stops_the_whole_batch(self) -> None:
        def runner(cmd, cwd, env, timeout):
            return proc.Outcome(stdout="", stderr="", exit_code=None, timed_out=False,
                                abort_reason="api_retry with error_status 401: authentication failed")
        status, rows = self.batch(runner)
        self.assertEqual(len(rows), 1)
        self.assertIn("authentication", status)

    def test_two_consecutive_error_results_stop_the_batch(self) -> None:
        status, rows = self.batch(lambda cmd, cwd, env, timeout: outcome_with({**RESULT, "is_error": True}))
        self.assertEqual(len(rows), 2)
        self.assertIn("is_error", status)

    def test_paid_metrics_are_kept_when_a_check_crashes(self) -> None:
        with mock.patch.object(quality, "quality_checks", side_effect=RuntimeError("checker broke")):
            status, rows = self.batch(lambda cmd, cwd, env, timeout: outcome_with(RESULT), runs=1)
        self.assertIsNone(status)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["duration_ms"], 1234)
        self.assertIn("checker broke", rows[0]["error"])

    def test_ctrl_c_writes_the_summary_of_finished_runs_and_exits_non_zero(self) -> None:
        calls_made = []

        def runner(cmd, cwd, env, timeout):
            calls_made.append(cmd)
            if len(calls_made) == 2:
                raise KeyboardInterrupt
            return outcome_with(RESULT)
        prep.prepare(self.subject, log=lambda _: None)
        out = self.root / "results"
        code = run.run_and_summarize(self.subject, run.schedule(2, [1], seed=1), "m", 1, out, runner=runner,
                                     timeout=5, env=self.env, log=lambda _: None)
        self.assertEqual(code, 130)
        self.assertEqual(len((out / "results.jsonl").read_text(encoding="utf-8").splitlines()), 1)
        self.assertTrue((out / "summary.md").is_file())


class LeakGuards(PipelineCase):
    def test_reads_of_the_bench_directory_are_denied_by_rule(self) -> None:
        cmd = run.claude_command(self.subject, "p", Path("/tmp/plugin"), "m")
        denied = cmd[cmd.index("--disallowedTools") + 1:cmd.index("--allowedTools")]
        self.assertIn(f"Read(/{run.BENCH}/**)", denied)
        self.assertEqual(cmd[-len(run.ALLOWED_TOOLS):], run.ALLOWED_TOOLS)

    def test_environment_record_masks_secrets(self) -> None:
        record = run.env_record({"ANTHROPIC_API_KEY": "sk-secret", "ANTHROPIC_BASE_URL": "https://proxy.example",
                                 "ANTHROPIC_MODEL": "x", "MAX_THINKING_TOKENS": "8000", "HOME": "/h",
                                 "ANTHROPIC_AUTH_TOKEN": "tok"})
        self.assertEqual(record["ANTHROPIC_API_KEY"], {"set": True})
        self.assertEqual(record["ANTHROPIC_AUTH_TOKEN"], {"set": True})
        self.assertEqual(record["ANTHROPIC_BASE_URL"], {"set": True})
        self.assertEqual(record["MAX_THINKING_TOKENS"], {"set": True, "value": "8000"})
        self.assertNotIn("HOME", record)
        self.assertNotIn("sk-secret", json.dumps(record))


@unittest.skipIf(shutil.which("npm") is None, "npm is required for the quality checks")
class Regrade(PipelineCase):
    def test_regrading_the_archived_workspaces_reproduces_the_original_grades(self) -> None:
        prep.prepare(self.subject, log=lambda _: None)
        out = self.root / "results"
        run.run_batch(self.subject, run.schedule(1, [1], seed=2), model="m", seed=2, out_dir=out,
                      runner=self.fake_model([]), timeout=5, env=self.env, log=lambda _: None)
        original = [json.loads(l) for l in (out / "results.jsonl").read_text(encoding="utf-8").splitlines()]
        with tarfile.open(out / original[0]["workspace"]) as archive:
            names = archive.getnames()
        self.assertIn("src/split.js", names)
        self.assertFalse(any("node_modules" in n or n.startswith(".git") for n in names))
        self.assertNotIn("BUG.md", names)

        run.regrade(out, self.subject, log=lambda _: None)
        regraded = [json.loads(l) for l in (out / "results.regraded.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r["checks"] for r in regraded], [r["checks"] for r in original])
        self.assertEqual([r["leak_suspect"] for r in regraded], [r["leak_suspect"] for r in original])
        self.assertTrue(all(r["checks"]["hidden_pass"] for r in regraded))
        self.assertTrue((out / "summary.regraded.md").is_file())


FAKE_TOKEN = "sk-ant-oat01-FAKE-TOKEN-must-never-be-written"
BOTH = {"ANTHROPIC_API_KEY": "sk-ant-api03-FAKE-KEY", "CLAUDE_CODE_OAUTH_TOKEN": FAKE_TOKEN,
        "CLAUDE_CODE_ENTRYPOINT": "cli", "CLAUDECODE": "1", "PATH": "/usr/bin"}


class AuthModes(PipelineCase):
    def test_oauth_drops_bare_for_strict_mcp_config_and_nothing_else_changes(self) -> None:
        key = run.claude_command(self.subject, "p", Path("/tmp/plugin"), "m", auth="api-key")
        oauth = run.claude_command(self.subject, "p", Path("/tmp/plugin"), "m", auth="oauth")
        self.assertIn("--bare", key)
        self.assertNotIn("--strict-mcp-config", key)
        self.assertNotIn("--bare", oauth)
        self.assertIn("--strict-mcp-config", oauth)
        self.assertEqual([w for w in key if w != "--bare"], [w for w in oauth if w != "--strict-mcp-config"])

    def test_each_mode_passes_only_its_own_credential(self) -> None:
        oauth = run.child_env(BOTH, Path("/tmp/c"), auth="oauth")
        key = run.child_env(BOTH, Path("/tmp/c"), auth="api-key")
        self.assertEqual(oauth["CLAUDE_CODE_OAUTH_TOKEN"], FAKE_TOKEN)
        self.assertNotIn("ANTHROPIC_API_KEY", oauth)
        self.assertEqual(key["ANTHROPIC_API_KEY"], "sk-ant-api03-FAKE-KEY")
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", key)
        for env in (oauth, key):
            self.assertNotIn("CLAUDE_CODE_ENTRYPOINT", env)
            self.assertNotIn("CLAUDECODE", env)

    def test_auto_picks_the_only_credential_and_refuses_to_guess(self) -> None:
        self.assertEqual(prep.resolve_auth("auto", {"ANTHROPIC_API_KEY": "k"}), ("api-key", None))
        self.assertEqual(prep.resolve_auth("auto", {"CLAUDE_CODE_OAUTH_TOKEN": "t"}), ("oauth", None))
        mode, error = prep.resolve_auth("auto", BOTH)
        self.assertIsNone(mode)
        self.assertIn("--auth", error)
        mode, error = prep.resolve_auth("auto", {})
        self.assertIsNone(mode)
        for needle in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "claude setup-token"):
            self.assertIn(needle, error)
        self.assertIsNone(prep.resolve_auth("oauth", {"ANTHROPIC_API_KEY": "k"})[0])
        self.assertEqual(prep.resolve_auth("oauth", BOTH), ("oauth", None))

    def test_oauth_preflight_refuses_claude_files_above_the_temp_root(self) -> None:
        prep.prepare(self.subject, log=lambda _: None)
        temp_root = self.root / "a" / "b"
        temp_root.mkdir(parents=True)
        write(self.root, "CLAUDE.md", "memory")
        (self.root / "a" / ".claude").mkdir()
        self.assertEqual(prep.claude_files_above(temp_root), [self.root / "a" / ".claude", self.root / "CLAUDE.md"])
        env = {"CLAUDE_CODE_OAUTH_TOKEN": "t"}
        errors = "\n".join(prep.preflight(self.subject, env, which=lambda n: "/bin/" + n, temp_root=temp_root))
        self.assertIn(str(self.root / "CLAUDE.md"), errors)
        self.assertEqual(prep.preflight(self.subject, self.env, which=lambda n: "/bin/" + n, temp_root=temp_root), [])

    @unittest.skipIf(shutil.which("npm") is None, "npm is required for the quality checks")
    def test_no_credential_value_reaches_config_or_results(self) -> None:
        prep.prepare(self.subject, log=lambda _: None)
        out = self.root / "results"
        seen: list = []
        run.write_config(out, self.subject, model="m", runs=1, warmup=0, tasks=[1], seed=1, timeout=5,
                         auth="oauth", env=BOTH, versions={})
        run.run_batch(self.subject, run.schedule(1, [1], seed=1), model="m", seed=1, out_dir=out,
                      runner=self.fake_model(seen), timeout=5, env=BOTH, log=lambda _: None, auth="oauth")
        self.assertEqual(seen[0]["env"]["CLAUDE_CODE_OAUTH_TOKEN"], FAKE_TOKEN)  # the child still gets it
        self.assertNotIn("--bare", seen[0]["cmd"])
        config = json.loads((out / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(config["auth"], "oauth")
        self.assertNotIn("--bare", config["command"])
        for name in ("config.json", "results.jsonl"):
            text = (out / name).read_text(encoding="utf-8")
            for secret in (FAKE_TOKEN, "FAKE-TOKEN", "sk-ant-api03-FAKE-KEY", "sk-ant-oat01"):
                self.assertNotIn(secret, text, name)
        rows = [json.loads(l) for l in (out / "results.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertTrue(all(r["env_isolated"] is True for r in rows))

    @unittest.skipIf(shutil.which("npm") is None, "npm is required for the quality checks")
    def test_a_usage_limit_stops_the_batch_with_that_reason(self) -> None:
        prep.prepare(self.subject, log=lambda _: None)

        def runner(cmd, cwd, env, timeout):
            return proc.Outcome(stdout="", stderr="", exit_code=None, timed_out=False,
                                abort_reason="usage limit reached (subscription quota): Claude AI usage limit reached")
        status = run.run_batch(self.subject, run.schedule(3, [1], seed=1), model="m", seed=1,
                               out_dir=self.root / "r", runner=runner, timeout=5, env=self.env, log=lambda _: None)
        self.assertIn("usage limit", status)
        self.assertEqual(len((self.root / "r" / "results.jsonl").read_text(encoding="utf-8").splitlines()), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
