"""Fixtures and fake transcripts shared by the bench tests."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import checks

BENCH = Path(__file__).resolve().parent
SUBJECT = BENCH / "subjects" / "test-driven-development"
SKILL_DIR = "/tmp/bench-x/plugin/skills/test-driven-development"
COMMAND = "subject:test-driven-development"

BUGGY = """'use strict';
function splitCents(totalCents, n) {
  const share = Math.floor(totalCents / n);
  return Array.from({ length: n }, () => share);
}
module.exports = { splitCents };
"""
CORRECT = """'use strict';
function splitCents(totalCents, n) {
  const base = Math.floor(totalCents / n);
  const leftover = totalCents - base * n;
  return Array.from({ length: n }, (_, i) => base + (i < leftover ? 1 : 0));
}
module.exports = { splitCents };
"""
TEST_HEAD = """'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const { splitCents } = require('../src/split');
test('even split', () => { assert.deepEqual(splitCents(10000, 4), [2500, 2500, 2500, 2500]); });
test('single participant', () => { assert.deepEqual(splitCents(500, 1), [500]); });
"""
VALIDATING = """'use strict';
function splitCents(totalCents, n) {
  if (!Number.isInteger(n) || n < 1) throw new RangeError('n must be a positive integer');
  const base = Math.floor(totalCents / n);
  const leftover = totalCents - base * n;
  return Array.from({ length: n }, (_, i) => base + (i < leftover ? 1 : 0));
}
module.exports = { splitCents, VERSION: 2 };
"""
FUNCTION_EXPORT = CORRECT.replace("module.exports = { splitCents };", "module.exports = splitCents;")
MOVED_SPLIT = "'use strict';\nmodule.exports = require('./allocate');\n"
VALIDATION = (
    "test('rejects zero participants', () => { assert.throws(() => splitCents(100, 0), RangeError); });\n"
    "test('rejects fractional participants', () => { assert.throws(() => splitCents(100, 1.5), RangeError); });\n"
)
LOST_CENT = "test('lost cent', () => { assert.deepEqual(splitCents(10000, 3), [3334, 3333, 3333]); });\n"
FAIRNESS = "test('fairness', () => { assert.deepEqual(splitCents(100, 7), [15, 15, 14, 14, 14, 14, 14]); });\n"


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_fixture(root: Path, source: str = BUGGY, tests: str = TEST_HEAD) -> Path:
    write(root, "package.json", '{"name": "split-payment", "private": true, "scripts": {"test": "node --test"}}\n')
    write(root, "BUG.md", "splitCents(10000, 3) should be [3334, 3333, 3333]\n")
    write(root, "src/split.js", source)
    write(root, "test/split.test.js", tests)
    return root


class TempDirCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.root)


# ------------------------------------------------------------ stream-json fakes


def jsonl(*events: dict) -> str:
    return "".join(json.dumps(e) + "\n" for e in events)


def init(slash_commands=(COMMAND, "compact"), api_key_source: str = "ANTHROPIC_API_KEY") -> dict:
    return {"type": "system", "subtype": "init", "slash_commands": list(slash_commands),
            "plugins": [{"name": "subject", "path": "/tmp/bench-x/plugin"}], "mcp_servers": [],
            "apiKeySource": api_key_source}


def assistant(*blocks: dict) -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": list(blocks)}}


def bash(tool_id: str, command: str) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": "Bash", "input": {"command": command}}


def edit(tool_id: str, path: str, name: str = "Edit") -> dict:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": {"file_path": path}}


def reply(tool_id: str, content, is_error: bool = False) -> dict:
    block = {"type": "tool_result", "tool_use_id": tool_id, "content": content, "is_error": is_error}
    return {"type": "user", "message": {"role": "user", "content": [block]}}


RESULT = {"type": "result", "subtype": "success", "is_error": False, "duration_ms": 1234,
          "duration_api_ms": 1000, "num_turns": 7, "total_cost_usd": 0.25,
          "usage": {"input_tokens": 10, "output_tokens": 500, "cache_creation_input_tokens": 100,
                    "cache_read_input_tokens": 2000}}
FAILING = "✔ even split\n✖ lost cent\nℹ tests 2\nℹ pass 1\nℹ fail 1\n"
PASSING = "✔ even split\nℹ tests 1\nℹ pass 1\nℹ fail 0\n"


def calls(*events: dict) -> list:
    return checks.tool_calls(checks.parse_events(jsonl(*events)))


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
                          cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()
