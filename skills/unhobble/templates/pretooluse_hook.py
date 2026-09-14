#!/usr/bin/env python3
"""PreToolUse hook draft for a `mechanize` row. Replace RULE, TOOL and REASON.

A prose rule becomes a hook when a deterministic check can tell a violation from
a non-violation. If the check needs judgment, the rule stays prose.

Register it (draft for the user to add; unhobble never edits settings.json):

    {
      "hooks": {
        "PreToolUse": [
          {
            "matcher": "Bash",
            "hooks": [{ "type": "command", "command": "/absolute/path/to/this_hook.py" }]
          }
        ]
      }
    }

Try it before proposing it:

    echo '{"tool_name":"Bash","tool_input":{"command":"git push --force origin main"}}' | python3 this_hook.py; echo $?

Exit codes: 0 allows the call; 2 blocks it and sends stderr to Claude.
Anything the hook cannot parse is allowed: a guard that blocks by accident gets
switched off, and then it guards nothing.
"""
from __future__ import annotations

import json
import re
import sys

TOOL = "Bash"
# Example rule: "never force-push". Replace with the rule being mechanized.
RULE = re.compile(r"\bgit\s+push\b.*\s(--force|-f)(\s|$)")
REASON = "Blocked: force-push is not allowed. Push normally or open a PR."


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    if payload.get("tool_name") != TOOL:
        return 0
    command = (payload.get("tool_input") or {}).get("command") or ""
    if RULE.search(command):
        print(REASON, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
