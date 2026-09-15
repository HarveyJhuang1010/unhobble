"""Transcript parsing and transcript-based checks for one bench run. Stdlib only.

Every check returns True, False, or None. None means "this run cannot tell",
and it is never folded into a pass or a fail.
"""
from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass

# Where each recorded metric lives in the final `"type": "result"` event.
# If a Claude Code release renames a field, change it here and nowhere else.
METRIC_FIELDS = {
    "duration_ms": ("duration_ms",),
    "duration_api_ms": ("duration_api_ms",),
    "num_turns": ("num_turns",),
    "total_cost_usd": ("total_cost_usd",),
    "input_tokens": ("usage", "input_tokens"),
    "output_tokens": ("usage", "output_tokens"),
    "cache_creation_input_tokens": ("usage", "cache_creation_input_tokens"),
    "cache_read_input_tokens": ("usage", "cache_read_input_tokens"),
    "is_error": ("is_error",),
    "result_subtype": ("subtype",),
}

FIRST_TURN_INPUT = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
AUTH_STATUSES = {401, 403}
LOGIN_FAILURE = re.compile(r"not logged in|/login|API Error: 40[13]\b|authentication_error", re.I)
QUOTA_WORDS = re.compile(r"limit|resets", re.I)  # only for runs that produced nothing
MID_RUN_QUOTA = re.compile(
    r"usage limit|rate[ _]limit|limit reached|hit your [^\n]*limit|resets|quota exceeded|credit balance is too low|API Error:?\s*429|\(429\)",
    re.I)  # only matched against runs that failed (is_error or a non-success subtype)
KEY_SOURCES = {"oauth": ("none",), "api-key": ("ANTHROPIC_API_KEY",)}  # init apiKeySource per auth mode
ENV_DUMP = re.compile(r"\$\{?(?:CLAUDE|ANTHROPIC)|/proc/[^\s]*/environ|process\.env\b")

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
NPM_TEST = re.compile(r"\bnpm\s+(?:run\s+)?(?:test|t)\b")
NODE_TEST = re.compile(r"\bnode\b(?P<args>.*?)\s--test(?![\w-])(?P<rest>.*)")  # not --test-reporter
FAIL_COUNT = re.compile(r"^\s*(?:#|ℹ)\s*fail\s+(\d+)", re.M)  # TAP and spec reporters
NOT_OK = re.compile(r"^\s*not ok\b", re.M)
EXIT_CODE = re.compile(r"\bExit code [1-9]\d*")  # how Claude Code reports a non-zero Bash exit
FILTER_FLAGS = {"--test-name-pattern", "--test-skip-pattern", "--test-only"}
VALUE_FLAGS = {"--test-reporter", "--test-reporter-destination", "--test-concurrency", "--test-timeout",
               "--test-shard", "--import", "--require", "-r"}
REDIRECT = re.compile(r"^\d*(?:&>|>&|>>?|<)")
# Tool input that touches any of these means the model may have seen grading material.
LEAK_MARKERS = (".cache", "hidden.test", "mutants", "evals/cases", "results/raw")

# ----------------------------------------------------------------- transcript


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict
    output: str | None  # None when no result came back (e.g. the run was killed)
    is_error: bool | None


def parse_events(text: str) -> list[dict]:
    """One event per stream-json line; a line cut off by a timeout is skipped."""
    events = []
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def init_event(events: list[dict]) -> dict | None:
    return next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), None)


def result_event(events: list[dict]) -> dict | None:
    return next((e for e in reversed(events) if e.get("type") == "result"), None)


def metrics(events: list[dict]) -> dict:
    """Result-event fields, API retries, and the input size of the first model request."""
    result = result_event(events) or {}
    out = {}
    for name, path in METRIC_FIELDS.items():
        value = result
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        out[name] = value
    retries = [e for e in events if e.get("type") == "system" and e.get("subtype") == "api_retry"]
    out["api_retries"] = len(retries)
    out["retry_delay_ms"] = sum(e.get("retry_delay_ms") or 0 for e in retries)
    out["first_turn_input_tokens"] = first_turn_input_tokens(events)
    return out


def first_turn_input_tokens(events: list[dict]) -> int | None:
    """Everything sent in the first request: the skill's text lands here if it was expanded."""
    for event in events:
        message = event.get("message") if event.get("type") == "assistant" else None
        usage = message.get("usage") if isinstance(message, dict) else None
        if isinstance(usage, dict):
            return sum(usage.get(key) or 0 for key in FIRST_TURN_INPUT)
    return None


def auth_failure(line: str) -> str | None:
    """A reason to stop the whole batch at once, if this stream-json line shows an authentication failure.

    A retry after HTTP 401/403 (the event's error_status), or a final result with
    is_error whose text is Claude Code's own login or API-auth error. Bare numbers
    are not matched: `npm ERR! 403` in a failed run is the model's problem, not ours."""
    try:
        event = json.loads(line)
    except ValueError:
        return None
    if not isinstance(event, dict):
        return None
    if event.get("type") == "system" and event.get("subtype") == "api_retry":
        status = event.get("error_status")
        if status in AUTH_STATUSES:
            return (f"api_retry with error_status {status} ({event.get('error')}): authentication failed, "
                    "check ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN")
        return None
    if event.get("type") != "result" or event.get("is_error") is not True:
        return None
    text = str(event.get("result") or "")[:300]
    return f"authentication failed: {text}" if LOGIN_FAILURE.search(text) else None


def produced_nothing(result: dict, calls: list[ToolCall]) -> bool:
    """No tool call, and nothing came out of the model: the environment ended the run.

    With output tokens reported: none, nothing billed, or an error. Without a usage
    field, only the bill can tell: nothing billed, or no bill at all and an error.
    A missing usage field on a billed answer is not "no output"."""
    if calls:
        return False
    output = (result.get("usage") or {}).get("output_tokens")
    cost, failed = result.get("total_cost_usd"), result.get("is_error") is True
    if output is None:
        return cost == 0 or (cost is None and failed)
    return output == 0 or cost == 0 or failed


def quota_exhausted(events: list[dict]) -> str | None:
    """A reason to stop the batch when a subscription quota or rate limit ended the run.

    A spent quota ends a session with or without is_error ("You've hit your
    5-hour limit · resets 3pm"). Before any work, the run produced nothing and
    its text mentions a limit or a reset. Mid-run, the run failed (is_error or a
    non-success subtype) with a usage-limit message. Every later run would end
    the same way and be counted as a failure of whichever variant it belonged to."""
    result = result_event(events)
    if result is None:
        return None
    calls = tool_calls(events)
    text = str(result.get("result") or "")[:200]
    if calls:
        failed = result.get("is_error") is True or result.get("subtype") != "success"
        if failed and MID_RUN_QUOTA.search(text):
            return f"subscription usage limit reached mid-run (after {len(calls)} tool calls), not a model failure: {text}"
        return None
    if produced_nothing(result, calls) and QUOTA_WORDS.search(text):
        output = (result.get("usage") or {}).get("output_tokens")
        return f"subscription usage limit (no tool call, {output} output tokens), not a bench failure: {text}"
    return None


def degenerate_run(events: list[dict]) -> bool | None:
    """The environment ended the run before the model did anything (see produced_nothing)."""
    result = result_event(events)
    return None if result is None else produced_nothing(result, tool_calls(events))


def no_tool_answer(events: list[dict]) -> bool | None:
    """The model answered without using a tool: real behaviour, and in these tasks a failure to deliver."""
    result = result_event(events)
    if result is None:
        return None
    calls = tool_calls(events)
    return not calls and not produced_nothing(result, calls)


def api_key_source(events: list[dict]) -> str | None:
    init = init_event(events)
    return None if init is None else init.get("apiKeySource")


def env_isolated(events: list[dict], plugin: str, auth: str) -> bool | None:
    """True when the session loaded the subject plugin and nothing else (no other plugin, no MCP server)
    and authenticated with the credential of its mode (apiKeySource)."""
    init = init_event(events)
    if init is None:
        return None
    plugins, servers = init.get("plugins"), init.get("mcp_servers")
    if not isinstance(plugins, list) or not isinstance(servers, list):
        return False
    if init.get("apiKeySource") not in KEY_SOURCES.get(auth, ()):
        return False
    names = [p if isinstance(p, str) else p.get("name") if isinstance(p, dict) else None for p in plugins]
    return len(names) == 1 and names[0] in (plugin, f"{plugin}@inline") and not servers


def content_blocks(event: dict) -> list[dict]:
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def tool_calls(events: list[dict]) -> list[ToolCall]:
    """Tool calls in the order the model made them, each paired with its result."""
    results = {
        b.get("tool_use_id"): (result_text(b.get("content")), b.get("is_error"))
        for e in events if e.get("type") == "user"
        for b in content_blocks(e) if b.get("type") == "tool_result"
    }
    return [
        ToolCall(b.get("id", ""), b.get("name", ""), b.get("input") or {}, *results.get(b.get("id"), (None, None)))
        for e in events if e.get("type") == "assistant"
        for b in content_blocks(e) if b.get("type") == "tool_use"
    ]


# ------------------------------------------------------------ process checks


def command_segments(command: str) -> list[str]:
    return [s for s in re.split(r"&&|\|\||;|\|", command) if s.strip()]


def is_test_command(command: str) -> bool:
    return any(NPM_TEST.search(s) or NODE_TEST.search(s) for s in command_segments(command))


def arguments(text: str) -> list[str]:
    """Shell words minus redirections (`2>/dev/null`, `> out.txt`, `2>&1`)."""
    try:
        words = shlex.split(text)
    except ValueError:
        words = text.split()
    kept, skip_next = [], False
    for word in words:
        operator = REDIRECT.match(word)
        if skip_next or operator:
            skip_next = not skip_next and operator is not None and operator.group(0) == word
            continue
        kept.append(word)
    return kept


def narrows_selection(text: str) -> bool:
    """A test-name filter, --test-only, or any positional path, directory or glob.

    Node 25 fails on a directory argument without running anything, and a glob may not
    cover the suite, so only a bare `node --test` / `npm test` counts as the full suite.
    """
    words, value_next = arguments(text), False
    for word in words:
        if value_next:
            value_next = False
            continue
        flag = word.split("=", 1)[0]
        if flag in FILTER_FLAGS:
            return True
        if word.startswith("-"):
            value_next = flag in VALUE_FLAGS and "=" not in word
            continue
        return True
    return False


def is_full_suite_command(command: str) -> bool:
    """A bare `npm test` or `node --test`: no filter and no path, directory or glob argument."""
    for segment in command_segments(command):
        npm = NPM_TEST.search(segment)
        if npm and not narrows_selection(segment[npm.end():]):
            return True
        node = NODE_TEST.search(segment)
        if node and not narrows_selection(node.group("args") + " " + node.group("rest")):
            return True
    return False


def shows_failure(output: str | None, is_error: bool | None) -> bool:
    text = output or ""
    if any(int(n) > 0 for n in FAIL_COUNT.findall(text)) or NOT_OK.search(text):
        return True
    # A permission denial is also is_error, but it carries no exit code.
    return bool(is_error) and bool(EXIT_CODE.search(text))


def edits_source(call: ToolCall, source: str) -> bool:
    if call.name not in EDIT_TOOLS:
        return False
    path = str(call.input.get("file_path") or call.input.get("notebook_path") or "").replace("\\", "/")
    return path == source or path.endswith("/" + source)


def test_runs(calls: list[ToolCall]) -> list[tuple[int, ToolCall]]:
    return [(i, c) for i, c in enumerate(calls) if c.name == "Bash" and is_test_command(str(c.input.get("command", "")))]


def red_before_fix(calls: list[ToolCall], source: str) -> bool | None:
    """Heuristic: a test run that showed failures happened before the first file-tool edit of source.

    None when source was never edited with a file tool (it may have been
    rewritten through a shell command, which this cannot see)."""
    edits = [i for i, c in enumerate(calls) if edits_source(c, source)]
    if not edits:
        return None
    return any(i < edits[0] and shows_failure(c.output, c.is_error) for i, c in test_runs(calls))


def suite_run_after_fix(calls: list[ToolCall], source: str) -> bool | None:
    """Heuristic: the full suite ran after the last file-tool edit of source."""
    edits = [i for i, c in enumerate(calls) if edits_source(c, source)]
    if not edits:
        return None
    return any(i > edits[-1] and is_full_suite_command(str(c.input.get("command", ""))) for i, c in test_runs(calls))


def skill_loaded(events: list[dict], command: str, skill_dirs: list[str]) -> bool | None:
    """False if the skill never registered; True only with evidence its content was loaded."""
    init = init_event(events)
    if init is None:
        return None
    if command not in {str(c).lstrip("/") for c in init.get("slash_commands") or []}:
        return False
    markers = [d.rstrip("/") for d in skill_dirs] + [f"<command-name>/{command}</command-name>"]
    for event in events:
        if event is not init and any(m in json.dumps(event, ensure_ascii=False) for m in markers):
            return True
    if any(c.name == "Skill" and str(c.input.get("skill", "")).lstrip("/") == command for c in tool_calls(events)):
        return True
    return None


PATH_FIELDS = ("file_path", "notebook_path", "path", "glob")


def looked_at(call: ToolCall) -> list[str]:
    """Where a tool call looked: path fields, Glob's pattern, Bash's command. Never file contents."""
    fields = PATH_FIELDS + (("pattern",) if call.name == "Glob" else ()) + (("command",) if call.name == "Bash" else ())
    return [str(call.input[f]) for f in fields if isinstance(call.input.get(f), str)]


def leak_suspect(calls: list[ToolCall], markers: list[str]) -> bool:
    """True if any tool call looked at the bench directory or grading material.

    Content being written (Write content, Edit strings) is not scanned: a test
    comment that says "mutants" is not a leak. Deliberately broad otherwise: a
    hit means "inspect and rerun", not proof."""
    needles = [m for m in markers if m] + list(LEAK_MARKERS)
    return any(n in target for c in calls for target in looked_at(c) for n in needles)


def prints_environment(segment: str) -> bool:
    words = segment.split()
    if not words:
        return False
    head, rest = words[0], words[1:]
    return (head == "printenv"
            or (head == "env" and all(w.startswith("-") or "=" in w for w in rest))
            or (head == "set" and not rest)
            or (head == "export" and rest in ([], ["-p"]))
            or (head == "npm" and rest[:2] == ["run", "env"]))


def credential_exposed(calls: list[ToolCall]) -> bool:
    """True if a Bash command may have printed the environment, credentials included."""
    commands = [str(c.input.get("command", "")) for c in calls if c.name == "Bash"]
    return any(ENV_DUMP.search(cmd) or any(prints_environment(seg) for seg in command_segments(cmd)) for cmd in commands)
