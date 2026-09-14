---
name: unhobble
description: Slim a CLAUDE.md, rules directory, skill, or harness config. Measure, re-verify facts, propose once, then run through to commit.
argument-hint: "<path> | rules | skill | harness"
disable-model-invocation: true
---

# Unhobble

Remove what a newer model no longer needs, and machine-enforce what should never
have been prose. Three modes, one skeleton.

Target: `$ARGUMENTS`. Infer the mode from it; ask only if genuinely ambiguous.

## Modes

| Mode | Target | The real problem | Verification available |
|---|---|---|---|
| `rules` | `~/.claude/CLAUDE.md`, `~/.claude/rules/`, a project `CLAUDE.md` | redundancy **and staleness** | none: read-back is all you get |
| `skill` | `~/.claude/skills/<name>/`, `.claude/skills/<name>/`, a plugin's `skills/<name>/` | step-by-step scaffolding written for an older model | whatever runnable check the skill ships (tests, evals), if it actually runs |
| `harness` | `settings.json`, plugins, MCP servers, `agents/*.md` | loaded but unused; rules that should be hooks | real usage in transcripts |

## The one criterion

**After deleting this, would my default behaviour differ in a fresh project with no
existing code to imitate?** No → delete.

Four corollaries:

- Yes → keep, even when the text reads like generic knowledge. In an existing codebase
  these rules are nearly all redundant (you follow the surrounding code); greenfield is
  the only place they act, and the only place you can't look anything up.
- Purely lookup-able (file layout, available commands) → delete, but count how many
  commands a lookup actually costs before calling it free.
- Safety and acceptance boundaries always stay: what must not be touched, what needs a
  human, what counts as done and as failed.
- Same thing stated in several places → keep one, point at it from the rest, and verify
  the target of every pointer you write.

## Skeleton (all three modes)

1. **Measure before reading.** Bytes per section, total against the thresholds in
   [`thresholds.md`](thresholds.md). Measuring first is what keeps step 3 honest:
   reading it all and then judging produces opinions, not cuts. Use
   `${CLAUDE_SKILL_DIR}/scripts/measure.py` after its `test_measure.py` passes; do not
   retype shell pipelines.
2. **Re-verify every fact.** Numbers, paths, counts, external state: test each one.
   Stale content beats verbose content for damage. It never errors; it just makes you
   confidently wrong. `measure.py facts` checks imports, paths and commands
   deterministically; spend your own judgment on what it cannot check.
3. Apply the criterion above.
4. **Propose once.** A table of cuts, each row carrying an action, bytes, reason, and the
   answer to *"what guarantees this now?"* A row with no answer is not a cut. Deleting
   whole clauses needs this confirmation; fixing a stale fact does not. Wait for one
   confirmation. Actions:
   - `delete`: the criterion says default behaviour does not change.
   - `merge`: keep one statement; the others become pointers to it.
   - `move-on-demand`: into a `paths:` rule, a skill, or a file reached by a pointer line
     that names the actions that should send you there. Never an `@import`: it loads
     at launch and saves nothing.
   - `fix-stale`: correct what step 2 disproved.
   - `mechanize`: a hook enforces it from now on. Attach a working draft built from
     `${CLAUDE_SKILL_DIR}/templates/pretooluse_hook.py`, with its test file adapted to
     at least one violating and one allowed input, plus the `settings.json` snippet.
     Draft only: the user installs it.
5. Execute the whole table on that single confirmation, except the prose of a
   `mechanize` row: it stays until the user says the hook is installed, because until
   then nothing guarantees it.
6. **Read back in both directions**: every cut is gone, **and** every keep is still
   there, item by item. Then scan the whole file for anything that pointed at what
   you removed.
7. Commit with an exact pathspec; `git show --stat HEAD` to confirm nothing else rode
   along.

### Mode deltas

- `rules`: only files **without** `paths:` frontmatter are resident. On-demand files
  are about staleness, not size; step 2 is where they pay off.
- `skill`: run the existing check before and after; a pass count that drops is a veto.
  First confirm the check actually executes. An `evals.json` may be a human review
  checklist, not a test. If nothing runs, fall back to `rules`-mode verification
  (two-direction read-back) rather than pretending a baseline exists.
- `harness`: rank by *resident cost × how often you actually used it*, measured from
  transcripts, not from opinion. Claude Code keeps transcripts at
  `~/.claude/projects/<project>/*.jsonl`; count invocations by name
  (`grep -l '"name":"Skill"'`, then grep the skill or tool name). Other harnesses keep
  transcripts elsewhere; if none are reachable, rank by resident cost alone and label
  the usage column as unmeasured. For a plugin's cost, use
  `claude plugin details <plugin>@<marketplace>`, which reports projected always-on and
  on-invoke tokens; bytes are a proxy with a unit trap of their own. It covers plugins
  only, not `CLAUDE.md` or rules. This mode proposes only: it edits no
  `settings.json`, disables no plugin, and suggests no plugin install. It subtracts and
  mechanises.

## Exit criteria

Both read-back directions pass, every command you wrote into a file has been run, the
before/after measurement is reported with the estimate's error, and it is committed.

> Rule files have no evals. Everything above proves only that the file became what
> you intended. It does not prove that removing those lines is safe. The only honest
> measure of that is a clean session before and a clean session after, given the same
> representative task, behaviour compared. Expensive; there is no cheaper substitute.

---

## Failure modes I have actually hit

These outrank the criterion above: the criterion is the general case, these are the
mistakes already made while applying it.

- **The measurement commands themselves have bugs.** Four in three runs: regex escapes
  mixed into `grep -F`, a `tr -d` set that ate the dot, the wrong file scope, a `sed`
  range cut short by a semicolon inside a comment. **All four looked like broken
  content.** Before drawing a conclusion from a tool's output, confirm it measured what
  you think it measured. A fifth surfaced while porting them to `measure.py`: the
  resident check skipped any file with frontmatter, not only files with `paths:`. That is
  why the script ships with a self-test.
- **Savings estimates run systematically high** (+33%, +33%, once −15%). The estimate
  targets a word count; compression that keeps the meaning stops short of it. **The
  proposal's number is an upper bound, not a promise.** Report the actual delta and
  how far off the estimate was.
- **Deleting a dimension leaves references dangling.** Dropping the number column from
  an event table broke every `#24–27` reference in a warning block the proposal had
  marked "unchanged". Unchanged only holds while the numbers still exist. **After
  removing any column, numbering, or section, scan the whole file for what pointed at
  it.**
- **Verifying only that the cuts are gone reads an accidental deletion as success.**
  Both directions: the cuts absent, **and** the keeps present, one by one.
- **In on-demand files, saving context is not the goal.** The criterion removes almost
  nothing there; the real problem is that they disagree with reality in several places.
  That was caught only because someone thought to run a health check. **That is why
  step 2 is mandatory.**
- **Several counts of one dataset get conflated.** One run had three: 189 source-locale
  files, 201 slugs across all locales, 207 image files. I used 207 as the article
  count, subtracted 189 and announced 18 missing (correct: 12), and declared a slug
  non-existent after checking a single locale (it lived in another), ten minutes after
  writing that exact lesson into the same file. **Every number carries its unit and its
  population. Numbers with different units are never subtracted.**
- **The criterion gets applied to the wrong question.** Round one asked "does this
  passage read like generic knowledge?" and misjudged 6 of 14 items. The user's "in a
  brand-new project, how would you know my habits?" overturned it: on inspection, a
  design-patterns section was the sole source for one language's rules (the other
  languages carried their own copies; that one did not). **Ask about greenfield
  behaviour, not whether the content looks like common sense.**
