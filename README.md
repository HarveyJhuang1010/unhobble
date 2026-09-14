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

The skill also ships the failure modes hit while building it (buggy measurement commands, inflated savings
estimates, dangling references) and the thresholds used to decide when a file is too big
([`thresholds.md`](skills/unhobble/thresholds.md)). The thresholds were measured on one setup: override them
for yours.

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
