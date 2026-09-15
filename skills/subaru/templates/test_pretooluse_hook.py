"""Tests for the hook template, run the way Claude Code runs it: JSON on stdin.

    python3 test_pretooluse_hook.py

When you adapt the template for a `mechanize` row, replace these cases with at least
one input the new rule must block and one it must allow.
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent / "pretooluse_hook.py"


def exit_code(stdin: str) -> int:
    return subprocess.run([sys.executable, str(HOOK)], input=stdin, capture_output=True, text=True).returncode


def bash(command: object) -> str:
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})


class ForcePushRule(unittest.TestCase):
    def test_blocks_every_force_push_form(self) -> None:
        for command in (
            "git push --force origin main",
            "git push -f",
            "git push -uf origin main",
            "git push origin +main",
            "git -C repo push --force",
            "cd x && git push --force",
        ):
            with self.subTest(command=command):
                self.assertEqual(exit_code(bash(command)), 2)

    def test_allows_what_is_not_a_force_push(self) -> None:
        for command in (
            "git push -u origin feat/x",
            "git push --force-with-lease",
            "git push origin main && rm -f x",
            "git push origin main; grep -f patterns log",
            "git commit -m 'about --force'",
        ):
            with self.subTest(command=command):
                self.assertEqual(exit_code(bash(command)), 0)

    def test_ignores_other_tools(self) -> None:
        payload = json.dumps({"tool_name": "Write", "tool_input": {"command": "git push -f"}})
        self.assertEqual(exit_code(payload), 0)

    def test_fails_open_on_anything_unexpected(self) -> None:
        for stdin in ("not json", "[]", '"text"', json.dumps({"tool_name": "Bash", "tool_input": "x"}), bash(42)):
            with self.subTest(stdin=stdin):
                self.assertEqual(exit_code(stdin), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
