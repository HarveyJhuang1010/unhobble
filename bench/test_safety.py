"""Round-3 safety: quota stops, credential handling, redaction, symlinks and archive limits."""
from __future__ import annotations

import base64
import json
import os
import shutil
import sys
import tarfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import archive  # noqa: E402
import prep  # noqa: E402
import proc  # noqa: E402
import quality  # noqa: E402
import redaction  # noqa: E402
import run  # noqa: E402
from fakes import *  # noqa: F401,F403
from test_pipeline import PipelineCase  # noqa: E402

TOKEN = 'sk-ant-oat01-FAKE"quoted\\slashed-SECRET'  # quote and backslash: JSON escaping changes it
B64 = base64.b64encode(TOKEN.encode()).decode()
URLSAFE = base64.urlsafe_b64encode(TOKEN.encode()).decode().rstrip("=")
FRAGMENT = "sk-ant-oat01-FAKEtruncat"  # what a line-wrapped or cut-off copy leaves behind


def result(text: str, is_error: bool, num_turns: int = 1, output_tokens: int = 0) -> dict:
    return {**RESULT, "is_error": is_error, "result": text, "num_turns": num_turns,
            "usage": {**RESULT["usage"], "output_tokens": output_tokens}}


class Redaction(unittest.TestCase):
    def test_raw_and_json_escaped_forms_are_replaced(self) -> None:
        escaped = json.dumps(TOKEN)[1:-1]
        self.assertNotEqual(escaped, TOKEN)
        text = f"raw {TOKEN} json {escaped} double {json.dumps(json.dumps(TOKEN))[3:-3]}"
        clean = redaction.redact(text, [TOKEN])
        self.assertNotIn("SECRET", clean)
        self.assertEqual(clean.count(redaction.MARK), 3)
        self.assertEqual(redaction.redact("nothing", [TOKEN, ""]), "nothing")

    def test_base64_forms_and_anthropic_prefixed_fragments_are_replaced(self) -> None:
        raw = TOKEN.encode()
        encoded = [base64.b64encode(raw).decode(), base64.b64encode(raw).decode().rstrip("="),
                   base64.urlsafe_b64encode(raw).decode(), base64.urlsafe_b64encode(raw).decode().rstrip("=")]
        clean = redaction.redact(" | ".join(encoded) + " | cut: sk-ant-oat01-ABCDEFGH12", [TOKEN])
        for form in encoded + ["sk-ant-oat01-ABCDEFGH12"]:
            self.assertNotIn(form, clean)
        self.assertEqual(redaction.redact("sk-ant-short", []), "sk-ant-short")  # under 8 characters after the prefix

    def test_short_values_are_never_redacted(self) -> None:
        self.assertEqual(redaction.redact("npm test && node --test", ["test"]), "npm test && node --test")
        self.assertEqual(redaction.MIN_LENGTH, 20)

    def test_credentials_are_taken_from_every_credential_variable(self) -> None:
        self.assertEqual(sorted(redaction.credentials({"ANTHROPIC_API_KEY": "a", "CLAUDE_CODE_OAUTH_TOKEN": "b",
                                                       "ANTHROPIC_AUTH_TOKEN": "c", "HOME": "/h"})), ["a", "b", "c"])


class CheckEnv(unittest.TestCase):
    def test_tests_written_by_the_model_never_see_credentials(self) -> None:
        planted = {"ANTHROPIC_API_KEY": "k", "CLAUDE_CODE_OAUTH_TOKEN": "t", "ANTHROPIC_BASE_URL": "u",
                   "CLAUDE_CODE_ENTRYPOINT": "cli", "GITHUB_TOKEN": "g", "AWS_SECRET_ACCESS_KEY": "s", "PATH": "/usr/bin"}
        with mock.patch.dict(os.environ, planted):
            env = quality.check_env()
        self.assertEqual(env["PATH"], "/usr/bin")
        for name in planted:
            if name != "PATH":
                self.assertNotIn(name, env)


class CredentialPreflight(PipelineCase):
    def test_implausibly_short_credentials_are_refused(self) -> None:
        prep.prepare(self.subject, log=lambda _: None)
        errors = "\n".join(prep.preflight(self.subject, {"ANTHROPIC_API_KEY": "sk-test"}, which=lambda n: "/bin/" + n))
        self.assertIn("at least 20 characters", errors)
        self.assertEqual(prep.preflight(self.subject, self.env, which=lambda n: "/bin/" + n), [])

    def test_other_anthropic_routes_must_be_unset_first(self) -> None:
        prep.prepare(self.subject, log=lambda _: None)
        for name in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
            errors = "\n".join(prep.preflight(self.subject, {**self.env, name: "x"}, which=lambda n: "/bin/" + n))
            self.assertIn(f"unset {name}", errors)

    def test_child_env_never_carries_an_auth_token(self) -> None:
        base = {"ANTHROPIC_AUTH_TOKEN": "x", "ANTHROPIC_API_KEY": "k", "CLAUDE_CODE_OAUTH_TOKEN": "t"}
        for auth in ("oauth", "api-key"):
            self.assertNotIn("ANTHROPIC_AUTH_TOKEN", run.child_env(base, Path("/c"), auth))

    def test_environment_record_shows_no_base_url_value(self) -> None:
        record = run.env_record({"ANTHROPIC_MODEL": "x"})
        self.assertEqual(record["ANTHROPIC_BASE_URL"], {"set": False})
        self.assertEqual(record["ANTHROPIC_AUTH_TOKEN"], {"set": False})
        self.assertEqual(run.env_record({"ANTHROPIC_BASE_URL": "https://u:p@gw"})["ANTHROPIC_BASE_URL"], {"set": True})


@unittest.skipIf(shutil.which("npm") is None, "npm is required for the quality checks")
class BatchCase(PipelineCase):
    def batch(self, runner, runs: int = 3, env: dict | None = None, auth: str = "api-key") -> tuple:
        prep.prepare(self.subject, log=lambda _: None)
        out = self.root / "results"
        status = run.run_batch(self.subject, run.schedule(runs, [1], seed=1), model="m", seed=1, out_dir=out,
                               runner=runner, timeout=5, env=env or self.env, log=lambda _: None, auth=auth)
        return status, run.read_rows(out / "results.jsonl"), out


@unittest.skipIf(shutil.which("npm") is None, "npm is required for the quality checks")
class BatchStops(BatchCase):
    def test_a_five_hour_limit_stops_the_batch_with_or_without_is_error(self) -> None:
        for is_error in (True, False):
            shutil.rmtree(self.root / "results", ignore_errors=True)
            text = "You've hit your 5-hour limit · resets 3pm"
            status, rows, _ = self.batch(lambda c, w, e, t: proc.Outcome(jsonl(init(), result(text, is_error)), "", 0, False))
            self.assertEqual(len(rows), 1, is_error)
            self.assertIn("subscription", status)

    def test_npm_403_in_a_failed_result_does_not_stop_the_batch(self) -> None:
        text = "npm ERR! 403 Forbidden"
        status, rows, _ = self.batch(lambda c, w, e, t: proc.Outcome(
            jsonl(init(), assistant(bash("a", "npm install")), reply("a", text, True), result(text, False, 4, 300)),
            "", 0, False))
        self.assertIsNone(status)
        self.assertEqual(len(rows), 6)
        self.assertIsNone(rows[0]["abort_reason"])

    def test_two_degenerate_runs_in_a_row_stop_the_batch(self) -> None:
        status, rows, _ = self.batch(lambda c, w, e, t: proc.Outcome(jsonl(init(), result("", False, 1, 0)), "", 0, False))
        self.assertEqual(len(rows), 2)
        self.assertIn("degenerate", status)
        self.assertTrue(all(r["degenerate_run"] for r in rows))

    def test_the_api_key_source_is_recorded_and_checked(self) -> None:
        _, rows, _ = self.batch(lambda c, w, e, t: proc.Outcome(jsonl(init(api_key_source="none"), result("x", False, 5, 300)),
                                                                "", 0, False), runs=1)
        self.assertEqual(rows[0]["api_key_source"], "none")
        self.assertIs(rows[0]["env_isolated"], False)  # api-key mode, but no API key in use

    def test_credentials_are_redacted_from_every_file_the_bench_writes(self) -> None:
        env = {"CLAUDE_CODE_OAUTH_TOKEN": TOKEN, "PATH": os.environ["PATH"]}

        def leaky(cmd, cwd, child, timeout):
            self.assertEqual(child["CLAUDE_CODE_OAUTH_TOKEN"], TOKEN)
            write(cwd, "test/split.test.js", TEST_HEAD + LOST_CENT + FAIRNESS + f"// {TOKEN}\n")
            write(cwd, "src/split.js", CORRECT)
            write(cwd, "env.txt", f"CLAUDE_CODE_OAUTH_TOKEN={TOKEN}\n{B64}\n")
            write(cwd, f"dump-{TOKEN}.txt", "named after the token")
            stdout = jsonl(init(api_key_source="none"), assistant(bash("a", "printenv")),
                           reply("a", f"CLAUDE_CODE_OAUTH_TOKEN={TOKEN} {B64} {URLSAFE}"), {**RESULT, "result": f"token {TOKEN}"})
            stdout += f"not json {TOKEN}\ntruncated {FRAGMENT}\n"
            return proc.Outcome(stdout, f"stderr {TOKEN} {json.dumps(TOKEN)}", 0, False,
                                abort_reason=None)
        _, rows, out = self.batch(leaky, runs=1, env=env, auth="oauth")
        self.assertTrue(rows[0]["credential_exposed"])
        self.assertTrue(rows[0]["archive_names_redacted"])
        run.regrade(out, self.subject, log=lambda _: None)
        forms = [TOKEN, json.dumps(TOKEN)[1:-1], "FAKE", "SECRET", B64, B64.rstrip("="), URLSAFE, FRAGMENT]
        for path in out.rglob("*"):
            if not path.is_file():
                continue
            self.assertNotIn("SECRET", str(path))
            blobs = [path.read_bytes()]
            if path.name.endswith(".tar.gz"):
                with tarfile.open(path) as tar:
                    blobs = [tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()]
                    blobs.append("\n".join(tar.getnames()).encode())
            for blob in blobs:
                text = blob.decode("utf-8", "replace")
                for form in forms:
                    self.assertNotIn(form, text, f"{path.name}: {form}")


@unittest.skipIf(shutil.which("npm") is None, "npm is required for the quality checks")
class ArchiveSafety(PipelineCase):
    def run_one(self, extra) -> tuple:
        prep.prepare(self.subject, log=lambda _: None)
        out = self.root / "results"

        def runner(cmd, cwd, env, timeout):
            write(cwd, "test/split.test.js", TEST_HEAD + LOST_CENT + FAIRNESS)
            write(cwd, "src/split.js", CORRECT)
            extra(cwd)
            return proc.Outcome(jsonl(init(), assistant(bash("a", "npm test")), reply("a", PASSING), RESULT), "", 0, False)
        run.run_batch(self.subject, run.schedule(1, [1], seed=4), model="m", seed=4, out_dir=out,
                      runner=runner, timeout=5, env=self.env, log=lambda _: None)
        return out, run.read_rows(out / "results.jsonl")

    def test_absolute_symlinks_are_recorded_not_followed_and_regrade_still_works(self) -> None:
        def links(cwd):
            os.symlink("/etc/hosts", cwd / "hosts-link")
            os.symlink("split.js", cwd / "src" / "alias.js")
        out, rows = self.run_one(links)
        row = rows[0]
        self.assertNotIn("error", row)
        self.assertIs(row["workspace_has_symlinks"], True)
        self.assertEqual(row["workspace_symlinks"], {"hosts-link": "/etc/hosts", "src/alias.js": "split.js"})
        with tarfile.open(out / row["workspace"]) as tar:
            self.assertFalse(any(m.issym() or m.islnk() for m in tar.getmembers()))
        run.regrade(out, self.subject, log=lambda _: None)
        regraded = run.read_rows(out / "results.regraded.jsonl")
        self.assertEqual([r["checks"] for r in regraded], [r["checks"] for r in rows])
        self.assertTrue(all("error" not in r for r in regraded))

    def test_an_oversized_workspace_is_not_archived_and_regrade_says_why(self) -> None:
        with mock.patch.object(archive, "ARCHIVE_LIMIT_BYTES", 1000):
            out, rows = self.run_one(lambda cwd: write(cwd, "big.txt", "x" * 5000))
        self.assertNotIn("workspace", rows[0])
        self.assertIn("limit", rows[0]["archive_skipped"])
        self.assertIn("checks", rows[0])
        run.regrade(out, self.subject, log=lambda _: None)
        regraded = run.read_rows(out / "results.regraded.jsonl")
        self.assertIn("limit", regraded[0]["regrade_skipped"])
        self.assertNotIn("error", regraded[0])
        self.assertEqual(regraded[0]["checks"], rows[0]["checks"])

    def test_regrade_refuses_a_dirty_cache(self) -> None:
        prep.prepare(self.subject, log=lambda _: None)
        out = self.root / "results"
        out.mkdir()
        (out / "config.json").write_text(json.dumps({"subject": self.subject.name, "commit": self.subject.commit}))
        self.assertEqual(run.regrade_preflight(out, self.subject), [])
        write(self.subject.cache, "stray.txt", "x")
        self.assertIn("uncommitted", "\n".join(run.regrade_preflight(out, self.subject)))


@unittest.skipIf(shutil.which("npm") is None, "npm is required for the quality checks")
class ModelBehaviourVersusEnvironment(BatchCase):
    def test_a_short_question_back_is_graded_not_excluded(self) -> None:
        text = "I won't ship the one-liner... Want me to apply the correct fix?"
        status, rows, _ = self.batch(lambda c, w, e, t: proc.Outcome(jsonl(init(), result(text, False, 1, 40)), "", 0, False), runs=2)
        self.assertIsNone(status)
        self.assertEqual(len(rows), 4)
        for r in rows:
            self.assertEqual((r["no_tool_answer"], r["degenerate_run"]), (True, False))
            self.assertEqual((r["checks"]["hidden_pass"], r["checks"]["repro_test"], r["checks"]["fairness_test"]),
                             (False, False, False))

    def test_a_quota_hit_after_tool_calls_aborts_the_batch(self) -> None:
        def runner(c, w, e, t):
            return proc.Outcome(jsonl(init(), assistant(bash("a", "npm test")), reply("a", FAILING, True),
                                      result("Claude AI usage limit reached|1758000000", True, 6, 300)), "", 0, False)
        status, rows, _ = self.batch(runner)
        self.assertEqual(len(rows), 1)
        self.assertIn("usage limit", status)
        self.assertIn("usage limit", rows[0]["abort_reason"])

    def test_a_rate_limit_error_after_tool_calls_aborts_the_batch(self) -> None:
        text = 'API Error: {"type":"error","error":{"type":"rate_limit_error","message":"rate limited"}}'
        status, rows, _ = self.batch(lambda c, w, e, t: proc.Outcome(
            jsonl(init(), assistant(bash("a", "npm test")), reply("a", FAILING, True), result(text, True, 5, 200)), "", 0, False))
        self.assertEqual(len(rows), 1)
        self.assertIn("mid-run", status)

    def test_a_short_billed_answer_mentioning_a_limit_does_not_stop_anything(self) -> None:
        status, rows, _ = self.batch(lambda c, w, e, t: proc.Outcome(
            jsonl(init(), {**result("The fairness limit is one cent per share.", False, 1, 14), "total_cost_usd": 0.02}),
            "", 0, False))
        self.assertIsNone(status)
        self.assertEqual(len(rows), 6)
        self.assertTrue(all(r["no_tool_answer"] and r["abort_reason"] is None for r in rows))


if __name__ == "__main__":
    unittest.main(verbosity=2)
