# Thresholds and measurement

Defaults measured on one Claude Code setup (2026-08, re-baselined 2026-08-23).
Override them for yours; the *units* matter more than the numbers.

| Object | Threshold | Action |
|---|---|---|
| All `paths:` files loaded together when touching one file extension | > 30 KB | extract on-demand reference files (see below) |
| A single resident rule file (**no** `paths:` frontmatter) | > 12 KB | split |
| Resident layer total (resident rule files + `CLAUDE.md`) | > 35 KB | move domain-specific content behind `paths:` or into on-demand docs |
| Auto-memory index (`MEMORY.md`) | whichever cuts first: 200 lines or 25KB (documented), 24,985 **characters** (measured) | split it into a resident layer plus on-demand archive files (see below); trimming notes alone stops working well before this |

## Units that lie

- **`paths:` files are measured per load, not per file.** Every rule file whose glob
  matches the same extension loads together. Splitting one 16 KB `paths:` file into
  two 8 KB ones saves zero tokens. Only two moves reduce the load: delete a duplicate,
  or move deep content into a separate file whose own `paths:` fires only in the
  narrower situation, leaving a 3–5 line summary plus "read the full file when
  triggered" in place. The trigger is the `paths:` frontmatter, not the directory:
  a file moved into `refs/` without frontmatter is still resident.
- **The memory index has more than one cut, in different units.** The docs say the first
  200 lines or the first 25KB load, whichever comes first. One real truncation fell at
  24,985 **characters** (a 71-line index, so the line limit was not what cut it). CJK text
  is ~1.5× denser in bytes than in characters, so a CJK-heavy index can pass one reading
  and fail the other. `measure.py memory` reports all three; when they disagree, find the
  real cut by comparing the first and last injected lines in a fresh session.
- **`@path` imports save nothing.** An imported file is expanded into context at launch
  with the `CLAUDE.md` that imports it. Only a prose pointer ("read X before doing Y"),
  a `paths:` rule or a skill actually defers loading.
- **A harness warning is not the cap.** The memory-index hook warns at ~17 KB
  ("approaching"); the real ceiling is the row above. One session spent hours compressing
  toward the warning, believing it was the limit.

## Measurement commands

Use the bundled script instead of retyping shell pipelines. Run its self-test first:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/test_measure.py
python3 ${CLAUDE_SKILL_DIR}/scripts/measure.py resident ~/.claude
python3 ${CLAUDE_SKILL_DIR}/scripts/measure.py load ~/.claude/rules src/app/page.tsx internal/x.go
python3 ${CLAUDE_SKILL_DIR}/scripts/measure.py memory ~/.claude/projects/<project>/memory/MEMORY.md
python3 ${CLAUDE_SKILL_DIR}/scripts/measure.py facts CLAUDE.md --root .
```

- `resident` counts every rule file without a `paths:` key, recursively. The shell version
  this replaced skipped any file that merely *had* frontmatter, which is wrong: frontmatter
  without `paths:` still loads at launch.
- `load` takes a real file path and sums the path-scoped rules whose globs match it (brace
  expansion and `**` included). The old per-extension grep was only an upper bound: it
  could not tell `**/components/**/*.go` from `**/*.go`.
- `facts` lists `@imports`, backticked paths and shell commands with whether each
  resolves, and model IDs for you to judge. `MISSING` means "not found from the file's
  directory or `--root`", not "wrong": a project-relative path mentioned in a user-level
  file is expected to be missing there.

The script lists; it does not judge. Sanity-check one output against a file you can count
by hand before acting on a total.

## When trimming stops working: the two-tier split

An index of one line per item has a floor you cannot compress: the link syntax itself.
Measured on a 342-entry memory index (2026-09-10): filenames 12,473 chars, titles 5,931,
`[]()` 1,784, bullets 416 — **20,188 chars, 81% of the cap**, before a single word of
annotation. Stripping every note buys roughly 80 more entries and costs the triggers that
make the index fire at all. Past that point the only real lever is structural: keep a
**resident layer** in the loaded file and move the rest into **on-demand archive files**,
each reached by one pointer line that stays resident.

Two things decide whether the split is safe, and both must be checked first:

- **Is there content-based recall, or is the index the only discovery path?** Check the
  transcripts before assuming. On Claude Code the "This memory is N days old" reminder is
  stamped on your own `Read` of a memory file — it is not autonomous retrieval. Where the
  index is the only path, anything moved behind a pointer that never fires is gone. Split
  by **topic**, never by age: a topic has a nameable trigger, "old" does not.
- **Does the loader take the whole directory or one filename?** Verify from a real session
  (which files actually arrived in context), not from the docs.

Write each pointer as a hook: name the **actions** that should send you to the file (verbs,
tool names, pipeline names), never the conclusions inside it. The archive layer costs no
resident context, so annotations there are free — restore the full text you compressed out
of the index. The split's real cost is the bet that the pointers fire, and that is not
observable in the session that makes it.
