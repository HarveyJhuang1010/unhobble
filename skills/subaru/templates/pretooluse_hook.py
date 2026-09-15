#!/usr/bin/env python3
"""PreToolUse hook draft for a `mechanize` row. Replace TOOL, REASON and violates().

A prose rule becomes a hook when a deterministic check can tell a violation from
a non-violation. If the check needs judgment, the rule stays prose.

Register it (a draft for the user to add; unhobble never edits settings.json):

    {
      "hooks": {
        "PreToolUse": [
          {
            "matcher": "Bash",
            "hooks": [{ "type": "command", "command": "python3 /absolute/path/to/this_hook.py" }]
          }
        ]
      }
    }

Call it through `python3` as above: a template copied without the executable bit fails
with exit 126, which does not block, so the guard would silently guard nothing.

Test it the way Claude Code runs it, then adapt test_pretooluse_hook.py:

    echo '{"tool_name":"Bash","tool_input":{"command":"git push --force origin main"}}' | python3 this_hook.py; echo $?

Exit codes: 0 allows the call; 2 blocks it and sends stderr to Claude.
Anything the hook cannot parse is allowed: a guard that blocks by accident gets
switched off, and then it guards nothing.
"""
from __future__ import annotations

import json
import re
import shlex
import sys

TOOL = "Bash"
REASON = "Blocked: force-push is not allowed. Push normally or open a PR."

# Example rule: "never force-push". Judge each shell segment on its own, so
# `git push && rm -f x` is not read as a force-push.
SEPARATORS = re.compile(r"&&|\|\||;|\||\n")
GIT_OPTIONS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}


def push_arguments(segment: str) -> list[str] | None:
    """Arguments after `push` when the segment is a git push, else None."""
    try:
        tokens = shlex.split(segment)
    except ValueError:
        tokens = segment.split()
    if not tokens or tokens[0] != "git":
        return None
    i = 1
    while i < len(tokens) and tokens[i].startswith("-"):
        i += 2 if tokens[i] in GIT_OPTIONS_WITH_VALUE else 1
    return tokens[i + 1 :] if i < len(tokens) and tokens[i] == "push" else None


def is_force(argument: str) -> bool:
    return (
        argument == "--force"
        or re.fullmatch(r"-[A-Za-z]*f[A-Za-z]*", argument) is not None  # -f, -uf
        or (argument.startswith("+") and len(argument) > 1)  # +main refspec
    )


def violates(command: str) -> bool:
    return any(
        any(is_force(arg) for arg in (push_arguments(segment) or []))
        for segment in SEPARATORS.split(command)
    )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    if not isinstance(payload, dict) or payload.get("tool_name") != TOOL:
        return 0
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if isinstance(command, str) and violates(command):
        print(REASON, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
