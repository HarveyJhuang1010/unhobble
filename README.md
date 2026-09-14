# unhobble

[繁體中文](README.zh-TW.md)

A Claude Code skill that slims your `CLAUDE.md`, rules, skills and harness config for newer models.
It measures first, re-verifies every fact, proposes the cuts once, and verifies the result in both directions.

## Why

Instructions written for an older model don't stay helpful. Step-by-step scaffolding, repeated reminders and
rules restated in three places turn from support into a hobble once the model can do the job on its own, and every
line of them is paid for in context on every session.

This skill was inspired by Boris Cherny's talk at Y Combinator,
[**"We Cut 80% of Claude Code's Prompt"**](https://www.youtube.com/watch?v=qyPCVqFUyDo). The title is the idea:
the Claude Code team cut most of its own prompt. unhobble turns that into a repeatable procedure you can run on
your own setup.

> Not affiliated with or endorsed by Anthropic or Boris Cherny.

## Results so far

On the author's own Claude Code setup, most skills put through unhobble now run in **about half the time**.
That is a personal observation on one setup, not a controlled benchmark. Your numbers will differ.

## What it does

| Mode | Target | The real problem |
|---|---|---|
| `rules` | `CLAUDE.md`, `~/.claude/rules/` | redundancy **and staleness** |
| `skill` | a skill directory | step-by-step scaffolding written for an older model |
| `harness` | `settings.json`, plugins, MCP servers, agents | loaded but unused; rules that should be hooks (proposes only, never edits) |

One criterion decides every cut: **after deleting this, would the model's default behaviour differ in a fresh
project with no existing code to imitate?** If not, it goes.

Every run follows the same skeleton:

1. Measure before reading
2. Re-verify every fact (stale content is worse than verbose content: it never errors)
3. Apply the criterion
4. Propose once: a table of cuts, each answering *"what guarantees this now?"*
5. Execute the whole table on a single confirmation
6. Read back in both directions: the cuts are gone **and** the keeps are still there
7. Commit with an exact pathspec

Each proposed row carries an action: `delete`, `merge`, `move-on-demand`, `fix-stale`, or `mechanize`, where
`mechanize` comes with a working PreToolUse hook draft for you to install.

Measurement and fact checks run through a bundled script, [`skills/unhobble/scripts/measure.py`](skills/unhobble/scripts/measure.py) (Python 3.9+, stdlib only), which
ships with a self-test the skill runs before trusting any number. The skill also ships the failure modes hit while building it (buggy measurement commands, inflated savings
estimates, dangling references) and the thresholds used to decide when a file is too big
([`thresholds.md`](skills/unhobble/thresholds.md)). The thresholds were measured on one setup: override them
for yours.

## How it compares

Several tools already slim or lint agent instructions. Claude Code's built-in `/doctor` is the closest: it
trims checked-in `CLAUDE.md` files by cutting what Claude could derive from the codebase, finds unused skills,
MCP servers and plugins, and asks before changing anything. What unhobble adds on top:

| | unhobble |
|---|---|
| **Scope** | One criterion across three targets: `CLAUDE.md`/rules, the *content* of skill directories, and harness config |
| **Every cut is accountable** | Each proposed cut must answer *"what guarantees this behaviour now?"* A row with no answer is not a cut |
| **Staleness, not just length** | Every number, path and count is re-tested before judging; a stale rule is treated as worse than a long one |
| **Verifies the edit, not only the result** | Two-direction read-back: every cut is gone **and** every keep is still there, item by item |
| **Skill mode has a veto** | Runs the skill's existing tests or evals before and after; a lower pass count blocks the change (after first confirming the check actually runs) |
| **Harness mode never edits** | Ranks plugins, MCP servers and agents by resident size × real usage counted from transcripts, then only proposes |
| **Measurement is a tested script** | Resident bytes, per-load cost of path-scoped rules, memory-index cuts, and `@import`/path/command checks come from `measure.py`, not retyped pipelines; its self-test runs first |
| **Rules can become hooks** | A `mechanize` row ships a PreToolUse hook draft tried against a violating and an allowed input |
| **Measurement traps are written down** | `paths:` rule files cost per load, not per file; a memory index has several cuts in different units (lines, bytes, characters); a harness warning is not the cap; plus the failure modes hit while building it |

### When to use something else

- **A quick built-in pass on `CLAUDE.md` and unused extensions:** [`/doctor`](https://code.claude.com/docs/en/commands).
- **A CI gate or deterministic fact checks:** [agents-lint](https://github.com/giacomo/agents-lint) verifies that
  referenced paths exist and `npm run` scripts are in `package.json`, with CI exit codes. unhobble's re-verification
  is done by the model: slower, costs tokens, and it is user-invoked by design.
- **Many agent formats at once** (Cursor, Copilot, Windsurf, Gemini…): [ctxlint](https://github.com/YawLabs/ctxlint)
  covers 16 formats and flags staleness from git history. unhobble is built around Claude Code's files
  (`CLAUDE.md`, rules, skills, `settings.json`).
- **A skill that doesn't trigger reliably:** Anthropic's
  [skill-creator](https://github.com/anthropics/claude-plugins-official/tree/main/plugins/skill-creator) optimizes
  descriptions against trigger evals. unhobble cuts content; it does not tune triggering.
- **Adding missing guidance rather than cutting:**
  [claude-md-management](https://github.com/anthropics/claude-plugins-official/tree/main/plugins/claude-md-management)
  audits `CLAUDE.md` against the codebase and captures session learnings. unhobble subtracts: it deletes, merges
  duplicates behind a pointer and fixes stale facts, but never looks for what is missing.
- **`CLAUDE.md` only, rewriting blocks into on-demand pointers:**
  [skill-claudemd](https://github.com/qiaeru/skill-claudemd) verifies each kept command, path and pointer and ships
  eval cases.
- **Content you don't keep in files** (tool output, retrieved text): runtime prompt compression such as
  [LLMLingua](https://github.com/microsoft/LLMLingua). unhobble edits versioned files once; it does nothing at
  inference time.

> Descriptions of other tools are based on their documentation and READMEs as of September 2026. They change;
> corrections are welcome.

## Install

### Option A: plugin (recommended)

In Claude Code:

```
/plugin marketplace add HarveyJhuang1010/unhobble
/plugin install unhobble@unhobble
```

Invoke with `/unhobble:unhobble <target>`.

### Option B: copy the skill

```bash
git clone https://github.com/HarveyJhuang1010/unhobble.git
mkdir -p ~/.claude/skills
cp -R unhobble/skills/unhobble ~/.claude/skills/unhobble
```

Invoke with `/unhobble <target>`.

## Usage

Examples use the manual-install name; with the plugin, type `/unhobble:unhobble` instead.

```
/unhobble ~/.claude/CLAUDE.md
/unhobble rules
/unhobble skill ~/.claude/skills/my-skill
/unhobble harness
```

The skill is user-invoked only (`disable-model-invocation: true`): Claude will never start it on its own, because
every run ends in deleted text and needs your confirmation on the proposal table.

## License

[MIT](LICENSE)
