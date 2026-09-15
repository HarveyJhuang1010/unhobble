# bench

A controlled comparison of one public skill in two versions: the original
(`before`) and the version unhobble slimmed (`after`), run on the same tasks.

The claim under test is the one the top-level README makes from personal
observation: a slimmed skill runs faster and cheaper. Cost only counts if
quality holds. Every run is graded by deterministic checks, and the summary
prints any quality drop above the cost numbers. **If `after` passes a quality
check significantly less often than `before`, its speed numbers are void.**

## Subject selection (fixed before any run)

These criteria were written down on 2026-09-15, before the first run and
before any result was seen. A subject must have:

1. **A quality signal a program can judge**: hidden tests, mutants, or checks
   on the transcript. An LLM grader alone does not qualify.
2. **Room to slim**: a SKILL.md large enough that unhobble has something to cut.
3. **Headless completion**: the task finishes under `claude -p` with no human
   in the loop.
4. **A licence that allows publishing a modified version**, because
   `after/SKILL.md` is a derivative work and is committed here.

The first subject is `test-driven-development` from
[addyosmani/agent-skills](https://github.com/addyosmani/agent-skills) (MIT),
pinned at `be4e44a9fbc5e8df0beaefadbb28bd22ee61cc39`. Its SKILL.md is 16,517
bytes, the repo ships a fixture with a planted bug, and its eval cases 1 and 2
supply the task prompts, used verbatim. Case 2 adds pressure: a tech lead
dictates a wrong one-line fix and says tests can wait.

## How `after` was made

`subjects/test-driven-development/after/SKILL.md` is the output of one ordinary
unhobble run (`/unhobble:subaru skill skills/test-driven-development`), and
`after/slim.diff` is its full diff against the pinned original.

- **Blind to the grading.** The run happened in a standalone copy holding only the
  skill, its `references/testing-patterns.md` (same relative layout, so the link
  resolved and was not "fixed") and the licence. No fixture, task prompt, hidden
  test, mutant or eval case was in reach.
- **No rows excluded.** unhobble proposed 17 rows; all 17 were confirmed as
  proposed. Rows were not kept or dropped because of what the tasks test: doing so
  would tune `after` to the exam.
- **Its own report**: 16,517 → 4,745 bytes (398 → 67 lines, −71%); it estimated
  about 5,200. Read-back: 15 strings that should be gone had 0 hits, 40 rule
  strings that should stay had 1 hit each.
- **Checked against `slim.diff`**: the frontmatter is byte-identical, and so are
  the `### Security Boundaries` heading and its rule paragraph (untrusted
  browser data, no navigation to extracted URLs, no cookie or token access).
  The sentence that followed that paragraph in the original, a pointer to
  `browser-testing-with-devtools`, was removed there and reworded into the
  browser-testing paragraph above it.
- Before the copy was slimmed, its SKILL.md was checked byte-identical (sha256)
  to the pinned upstream file.

## What differs between the variants

Only the skill's `SKILL.md`. Before spending anything, `run` refuses to start
if the cached checkout is dirty, if its SKILL.md is not byte-identical to
`git show <commit>:<skill path>`, if `after/SKILL.md` is byte-identical to the
original, or if the two frontmatter `name`s differ.

Each run gets a fresh temporary directory with:

- `workspace/`: the fixture, `git init`ed with one baseline commit. Claude runs here.
- `plugin/`: a one-skill plugin named `subject`. `skills/test-driven-development/SKILL.md`
  is the pinned original (`before`) or `subjects/test-driven-development/after/SKILL.md`
  (`after`). `references/testing-patterns.md` comes from the pinned checkout for
  both, so the skill's `../../references/...` link resolves in both.
- its own empty `CLAUDE_CONFIG_DIR`, so no user settings, memory or plugins leak in.

Both variants run the identical command for the chosen authentication mode.
The two modes differ in one flag each:

```
# --auth api-key (ANTHROPIC_API_KEY)
claude --bare -p "/subject:test-driven-development <task prompt>" \
  --plugin-dir <plugin> --add-dir <plugin> --model <model> \
  --output-format stream-json --verbose --no-session-persistence \
  --permission-mode acceptEdits \
  --disallowedTools "Read(//<abs>/bench/**)" "Edit(//<abs>/bench/**)" \
  --allowedTools Read Edit Write Glob Grep "Bash(node:*)" ...

# --auth oauth (CLAUDE_CODE_OAUTH_TOKEN, a subscription)
claude -p "/subject:test-driven-development <task prompt>" \
  --plugin-dir <plugin> --add-dir <plugin> --model <model> \
  --output-format stream-json --verbose --no-session-persistence --strict-mcp-config \
  --permission-mode acceptEdits \
  --disallowedTools ... --allowedTools ...
```

`--bare` skips hooks, plugin sync, auto-memory and CLAUDE.md discovery, but it
also reads no OAuth token or keychain: it accepts only `ANTHROPIC_API_KEY` or an
`apiKeyHelper`. A subscription run therefore cannot use it. In oauth mode the
same isolation comes from four things instead, each checkable:

1. an empty `CLAUDE_CONFIG_DIR` per run: no user settings, memory, plugins or login;
2. `--strict-mcp-config`: no MCP server unless one is passed on the command line;
3. preflight refuses to start if any directory from the temp root up to `/`
   holds a `CLAUDE.md` or `.claude/`, since without `--bare` Claude Code reads
   those upward from the working directory;
4. every run records `env_isolated` from its own init event (only the
   `subject` plugin, no MCP server); runs where it is not true are left out of
   every statistic and counted at the top of the summary.

Evidence, from a probe run by hand on 2026-09-15 (empty `CLAUDE_CONFIG_DIR`,
no `--bare`, `ANTHROPIC_API_KEY` unset, `CLAUDE_CODE_OAUTH_TOKEN` from
`claude setup-token`, with `--plugin-dir <plugin> --strict-mcp-config
--output-format stream-json --verbose --no-session-persistence`): the init
event showed `plugins = ['subject@inline']`, `mcp_servers = []`,
`apiKeySource = none`, and the plugin skill in `slash_commands`; the result had
`is_error=False`; asked whether its context contained a sentence from the
user's global CLAUDE.md, it answered "No". Controls: under the user's default
settings the same question got "yes" with 11 plugins loaded, and with an empty
`CLAUDE_CONFIG_DIR` and no token the result was `is_error=True`, "Not logged in
· Please run /login", `duration_ms` 69.

The shell allowlist leaves out in-place editors such as `sed`, so source edits go
through Edit/Write where the transcript checks can see them. Environment
variables from an enclosing Claude Code session (`CLAUDECODE`, `CLAUDE_CODE_*`)
are dropped, and git inside a run ignores the machine's git config.
`config.json` records which `ANTHROPIC_*`, `DISABLE_*` and other
behaviour-changing variables the runs saw: names always, values only for
non-secret ones such as model names and `MAX_THINKING_TOKENS`. Base URLs are
recorded as set or not, never by value, since a URL can carry a password.

### Order

For each task, both variants run back to back. Across the counted rounds,
`before` goes first in exactly half of them (one extra for either side when the
count is odd); a seeded shuffle picks which rounds. API latency drifts over
hours, and a fixed or unbalanced order would hand that drift, or any
first-or-second effect, to one variant. `--warmup N` (default 1) adds N
uncounted rounds first, both variants per task, so neither variant alone pays
for a cold start. Warmup runs are recorded and excluded. The seed is in
`config.json`; the summary splits duration medians by position so an order
effect is visible.

## Checks

The outcome checks run on copies of the finished workspace. None of them
touches the workspace itself. Every check is `true`, `false`, or `null`
(undecidable); the summary never counts `null` as a pass or a fail.

| check | kind | passes when |
|---|---|---|
| `hidden_pass` | outcome | `hidden.test.js` (never shown to the model) passes against the model's `src/split.js`: BUG.md case, README example, and exactness, fairness and earliest-first ordering for every total 0..300 and n 1..9 |
| `suite_pass` | outcome | the repo's own `npm test` exits 0 |
| `repro_test` | mutation | the suite fails once the allocation is replaced by the original bug (every share `Math.floor`) |
| `fairness_test` | mutation | the suite fails with the whole remainder on the first share. That is right on BUG.md's case, so only a fairness test with remainder ≥ 2 catches it |
| `rejects_dump_last` | mutation | the suite fails with the whole remainder on the last share, the one-liner task 2 dictates |
| `red_before_fix` | **heuristic** | before the first Edit/Write/MultiEdit of `src/split.js`, a Bash test run (`npm test` or `node --test`) showed failures: `is_error` with an exit code, `# fail N` / `ℹ fail N` with N ≥ 1, or `not ok` |
| `suite_run_after_fix` | **heuristic** | after the last edit of `src/split.js`, the full suite ran: `npm test`, or `node --test` with no `--test-name-pattern` / `--test-skip-pattern` / `--test-only` and no path, directory or glob argument (Node 25 exits 1 on a directory argument without running a test; a glob may not cover the suite). Redirections and flags such as `--test-reporter spec` do not disqualify it |
| `env_isolated` | validity | the init event lists only the `subject` plugin, no MCP server, and an `apiKeySource` that matches the mode (`none` for oauth, `ANTHROPIC_API_KEY` for api-key; recorded per run as `api_key_source`). Not true: the run is left out of cost and quality alike |
| `degenerate_run` | validity | no tool call, and no output tokens, nothing billed, or `is_error`: the environment ended the run before the model did anything. Left out of cost and quality alike |
| `no_tool_answer` | behaviour | no tool call, but a real answer (for example a question back instead of a fix). Both tasks ask for a delivered fix, so this is a failure the variant owns: the run stays in cost and quality, every outcome and mutation check is graded as usual (normally false), and the rate gets its own Fisher test and banner line (higher is worse) |
| `credential_exposed` | validity | a Bash command may have printed the environment (`env`, `printenv`, bare `set`, `export -p`, `npm run env`, `$CLAUDE…` / `$ANTHROPIC…`, `/proc/*/environ`, any `process.env` such as `node -e`). Counted at the top of the summary; credential values are redacted from every file either way |
| `skill_loaded` | validity | the init event lists `subject:test-driven-development` **and** the transcript shows the skill's content was loaded (its directory path, the command tag, or a Skill tool call). `false` if it never registered; `null` if registered but no evidence either way |

**How the mutation checks work.** The model's `src/split.js` is kept as
`src/split.model.js`, and a wrapper (`mutants/wrap.template.js`) takes its
place. The wrapper keeps the model's export shape (an object with other exports
passed through, or a bare function with its properties copied), calls the
model's `splitCents` first so its argument checks and throws still happen, and
for valid input (non-negative integer total, positive integer n) returns the
wrong allocation instead. Tests that only check input validation cannot kill a
mutant; only tests of the allocation can.

Suites run with `npm test`, so whatever `scripts.test` the model left in
`package.json` is what runs. Tests the script does not pick up are not run: a
new `__tests__/split.js` under the fixture's unchanged `node --test` is never
executed, and the mutation checks come out `null`.

Only the model's test work is compared. Its added or modified test files are
found by comparing the final workspace with the fixture: a path with a `test`,
`tests`, `spec` or `__tests__` segment and a JS/TS extension, or a file named
by `scripts.test`. The fixture's untouched test files are removed from the
mutant copies. Each copy's failing-test count (`ℹ fail N` / `# fail N`) is
compared with the model's own code under the same removal. First a probe: a
wrapper whose every share is `-1` (`mutants/sentinel.js`). If the model's tests
do not fail more under it, they never reach the allocation through
`src/split.js` (for example they `require` a helper the fix moved the logic
into), so a swap there proves nothing.

Each mutation check is:

- `false` when no test file was added or changed (the bug was fixed with no
  test written to catch it), or when `npm test` fails on the model's own fix;
- `null` when `npm test` prints no node test-runner summary, or the probe
  shows the model's tests do not exercise `src/split.js`;
- `true` when the model's tests fail more with this wrong allocation than with
  the model's own. An assertion added inside an existing `test()` block counts:
  the test count stays the same, but the failing count still rises.

The two heuristics return `null` when `src/split.js` was never edited through a
file tool.

### Leak guard

The model could look at the grading material: the hidden test, the mutants, the
cached eval cases (their expectations are the rubric), or earlier results. The
command denies Read and Edit on the bench directory by rule; shell reads
(`cat`, `grep`, `find`) cannot be fenced off that way. So every run also gets
`leak_suspect`: true if a tool call looked at the bench directory, `.cache`,
`hidden.test`, `mutants`, `evals/cases` or `results/raw`. Only where a call
looked is scanned: path fields (`file_path`, `notebook_path`, `path`, `glob`),
Glob's `pattern`, and Bash's `command`. What a call writes (`content`,
`old_string`, `new_string`) is not, so a test comment saying "mutants" is not
a leak. It is still deliberately broad. The summary counts these runs at the top and excludes them; inspect
their transcripts and rerun them.

### Credentials

The model's Bash tool inherits the run's environment, so a credential can reach
the transcript if the model prints the environment. The bench knows the values
it passed in (`ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`,
`ANTHROPIC_AUTH_TOKEN`) and replaces them with `[REDACTED]` in their raw,
JSON-escaped, doubly JSON-escaped and base64 forms (standard and URL-safe, with
and without padding), plus anything shaped like an Anthropic credential
(`sk-ant-` followed by 8 or more characters, which also catches a cut-off copy).
This covers everything it writes: the raw transcript, stderr, the diff, every
file's contents and name in the workspace archive (a redacted name stays
redacted on regrade; the run is marked `archive_names_redacted`), and
`results.jsonl`. It cannot catch every fragment of a token that was split,
wrapped or re-encoded some other way; `credential_exposed` marks the runs where
that could have happened, so check their raw files by hand. Values shorter than
20 characters are never redacted, and `run` refuses credentials that short. Tests the model wrote run with no `ANTHROPIC_*`,
`CLAUDE_CODE_*`, or any variable whose name contains TOKEN, KEY or SECRET.

## Running

Requires Python 3.9+ (stdlib only), node, npm, git, Claude Code on PATH, and
one credential, set in your shell environment only. Never write a token or key
into a file, `config.json`, or the repo; the bench records only the mode name.

- **Subscription**: run `claude setup-token`, then `export CLAUDE_CODE_OAUTH_TOKEN=...`
  in the shell you run the bench from. Use `--auth oauth`.
- **API key**: `export ANTHROPIC_API_KEY=...`. Use `--auth api-key`.

`--auth auto` (the default) uses whichever one is set, and refuses to start if
both or neither are. Each run's environment keeps only the credential of its
mode: the other one and `ANTHROPIC_AUTH_TOKEN` are removed. `run` refuses to
start while `ANTHROPIC_AUTH_TOKEN` or `ANTHROPIC_BASE_URL` is set in your shell,
since either can authenticate or route requests some other way; unset them
first rather than have the bench drop them silently.

```bash
python3 bench/test_bench.py                                  # first, always; free
python3 bench/run.py prepare --subject test-driven-development
# put the slimmed skill at bench/subjects/test-driven-development/after/SKILL.md
python3 bench/run.py run --subject test-driven-development --runs 10 [--warmup 1] [--model M] [--tasks 1,2] [--seed S] [--timeout 900]
python3 bench/run.py summarize bench/results/<timestamp>
python3 bench/run.py regrade bench/results/<timestamp>       # after changing a check; free
```

Paid runs per batch: `(runs + warmup) × 2 × tasks`. Each warmup round adds
2 × tasks runs, all paid, none counted. For a first smoke test that only
confirms the pipeline works (plugin resolves, transcripts parse, checks run):

```bash
python3 bench/run.py run --subject test-driven-development --runs 1 --tasks 1 --warmup 0 --model <model id>
```

That is 2 paid runs. Read `raw/*.jsonl` by hand before starting a real batch.

`run` refuses to start, listing every reason, if a prerequisite above is
missing or the cache or `after/SKILL.md` fails the integrity checks. A run that
crashes is recorded and the batch continues; if grading crashes, the run's
metrics are still written. The batch stops, loudly, when:

- claude retries a request with HTTP 401 or 403 (the `api_retry` event's
  `error_status`), or a run ends with `is_error` and Claude Code's own
  authentication error (`Not logged in`, `/login`, `API Error: 401`/`403`,
  `authentication_error`): the batch stops, because every later run would fail
  the same way. A bare `403` (say `npm ERR! 403` in a failing run) is not enough;
- a subscription quota or rate limit ends a run: either it made no tool call,
  produced nothing (no output tokens, nothing billed, or `is_error`) and its
  result mentions `limit` or `resets` (any case, as in "You've hit your 5-hour
  limit · resets 3pm"); or it had already used tools and then failed
  (`is_error` or a non-success subtype) with a usage-limit, rate-limit or reset
  message. The run is marked aborted, left out of every statistic, and the batch
  stops saying the quota ran out. A short answer that was billed and happens to
  say "limit" does not count;
- two runs in a row end with `is_error`, or two in a row are `degenerate_run`;
- you press Ctrl-C: the run in progress is killed and not recorded, and the
  summary of the finished runs is still written. Exit code 130.

Output in `bench/results/<timestamp>/` (gitignored):

- `config.json`: model, seed, commit, tool versions, SKILL.md sizes, environment record, command template
- `results.jsonl`: one line per run with metrics from the stream-json result
  event (`duration_ms`, `duration_api_ms`, `num_turns`, `total_cost_usd`, token
  usage), `api_retries` and `retry_delay_ms`, `first_turn_input_tokens`,
  `exit_code`, `timed_out`, `abort_reason`, `wall_ms`, `leak_suspect`, and every check
- `raw/<run-id>.jsonl`: the full stream-json transcript; `raw/<run-id>.diff`: what the model changed;
  `raw/<run-id>.workspace.tar.gz`: the final workspace's regular files, without `.git` and `node_modules`.
  Symlinks are not archived or followed; `results.jsonl` records them (`workspace_symlinks`,
  `workspace_has_symlinks`). A workspace over 20 MB is not archived (`archive_skipped` says why)
- `summary.md`, described next
- after `regrade`: `results.regraded.jsonl` and `summary.regraded.md`. Regrading
  extracts each archived workspace, reruns every outcome and mutation check, and
  recomputes the transcript checks and validity fields from the raw transcript.
  Cost metrics are copied, not rerun. The original files are left as they were.
  It refuses to start if the cache is not at the batch's commit or fails the
  integrity checks. Relative symlinks inside the workspace are recreated,
  absolute ones are not; a run without an archive keeps its original grades and
  gets `regrade_skipped`. `node_modules` was never archived, so a run whose tests
  needed packages the model installed can grade differently on regrade.

## Reading the summary

Cost and quality leave out different runs, and the summary lists, per
variant, how many runs each reason affected.

- **Cost** counts only runs that finished cleanly: no crash, warmup, missing
  `env_isolated`, `degenerate_run`, timeout, abort, non-zero exit, `is_error`,
  non-success result subtype, or leak suspect.
- **Quality** leaves out only runs that say nothing about the skill: harness
  crashes, warmups, runs that were not `env_isolated`, degenerate runs,
  authentication or quota aborts, and leak suspects. A `no_tool_answer` run is
  not one of them: answering instead of fixing is what the variant did. A run that timed
  out, exited non-zero, hit `is_error` or a non-success result stays in and
  never passes: its outcome checks (`hidden_pass`, `suite_pass`) count as
  failed whatever the archived workspace shows, and its other checks count as
  failed unless they are undecidable. Dropping those runs instead would make the
  variant that fails more often look better. `clean_completion`, the share of
  these runs that finished cleanly, is tested and flagged like any quality check.

- **Cost**: n, median and IQR of `duration_ms`, `duration_ms_net` (minus API
  retry waits, since 429/529 back-off is not the skill's doing), turns, output
  tokens and cost; the change in median; and the median difference
  after − before with a bootstrap 95% CI (2000 resamples, fixed seed). The main
  cost table leaves out `no_tool_answer` runs: an answer without a fix ends in
  seconds, and counting it would make the variant that acts less often look
  faster and cheaper. A second table includes them. Whenever the two variants'
  `no_tool_answer` rates differ at all, significant or not, a **Cost caution**
  line above the tables gives both rates.
- **Quality**: pass rates per check with a two-sided Fisher exact p. A lower
  `after` rate with p < 0.05 is flagged **SIGNIFICANT QUALITY REGRESSION**;
  a lower rate with p ≥ 0.05 is flagged **QUALITY DROP (not significant)**.
- **Did the skill load?** `first_turn_input_tokens` is everything sent in the
  first request (input + cache creation + cache read tokens). The skill's text
  is part of that request only if the slash command expanded it, so the median
  difference between variants should have the same sign as the SKILL.md byte
  difference, and roughly bytes/3 to bytes/4 tokens in size (an estimate, not
  measured). **If the difference is near zero, the skill was probably not
  expanded in either variant and the comparison is void.**
- **Order**: duration medians for the variant that ran first vs second.

**Small N.** With 10 runs per variant, Fisher's test cannot call a drop from
10/10 to 8/10 significant (p ≈ 0.47), and a bootstrap CI over 10 values is
wide and itself imprecise. "Not significant" is not "no drop". Treat any
flagged drop as a reason to add runs, and a CI that crosses zero as no
evidence of a cost change.

## Limitations

- `red_before_fix` and `suite_run_after_fix` read the transcript. A model that
  runs tests some other way, or edits files through `node -e`, can be misjudged.
  The raw transcript and diff are kept so any verdict can be checked by hand.
- The mutation checks depend on node's test-runner summary for test and failure
  counts; a suite on another runner makes them `null`, not wrong. A model that
  moves the allocation out of `src/split.js` and tests it there also gets `null`.
- `hidden_pass` uses the fixture's API (`const { splitCents } = require(...)`).
  A fix that changes the export to a bare function fails it, as it would break
  existing callers.
- With `--auth oauth`, `total_cost_usd` is Claude Code's estimate at API
  prices, not what the subscription is billed. Subscription quota and rate-limit
  waits also land in `duration_ms`; `api_retries` and `retry_delay_ms` record
  the waits Claude Code reports, and `duration_ms_net` subtracts them.
- API latency is noisy and shared with other traffic. Interleaving and balanced
  order spread drift across both variants but do not remove it.
- One model per batch. A result for one model says nothing about another.
- One subject so far, with two tasks drawn from the subject author's own evals.
- `after/SKILL.md` is produced by unhobble but reviewed and accepted by a
  person before it is committed. The bench measures that reviewed file, not
  unhobble's raw output.

## Not yet verified in a real run

- `env_isolated` requires the init event's `mcp_servers` list and
  `apiKeySource`. Both were seen in the oauth probe above; with `--auth api-key`
  (`--bare`) neither has been observed yet. Check the first api-key run's init
  event before trusting `env_isolated` there.
- The exact result text of a spent subscription quota and of an expired token.
- Whether `--disallowedTools "Read(//…/bench/**)"` takes effect, and whether it
  also covers Grep and Glob.
- Whether `/subject:test-driven-development <task>` under `-p` expands the skill
  (check `first_turn_input_tokens` and `skill_loaded` on the smoke test).

## Licence and attribution

The subject skill, its reference file, the fixture and the task prompts come
from [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills),
copyright (c) 2025 Addy Osmani, MIT licence. `subjects/test-driven-development/after/SKILL.md`
is a derivative of that project's `skills/test-driven-development/SKILL.md`,
distributed under the same licence; the full text is in
[`subjects/test-driven-development/LICENSE`](subjects/test-driven-development/LICENSE).
The original files are not vendored here: `prepare` clones them at the pinned commit.
