# Thresholds and measurement

Defaults measured on one Claude Code setup (2026-08, re-baselined 2026-08-23).
Override them for yours; the *units* matter more than the numbers.

| Object | Threshold | Action |
|---|---|---|
| All `paths:` files loaded together when touching one file extension | > 30 KB | extract on-demand reference files (see below) |
| A single resident rule file (**no** `paths:` frontmatter) | > 12 KB | split |
| Resident layer total (resident rule files + `CLAUDE.md`) | > 35 KB | move domain-specific content behind `paths:` or into on-demand docs |
| Auto-memory index (`MEMORY.md`) | > 24,985 **characters** | split it into a resident layer plus on-demand archive files (see below); trimming notes alone stops working well before this |

## Units that lie

- **`paths:` files are measured per load, not per file.** Every rule file whose glob
  matches the same extension loads together. Splitting one 16 KB `paths:` file into
  two 8 KB ones saves zero tokens. Only two moves reduce the load: delete a duplicate,
  or move deep content into a separate file whose own `paths:` fires only in the
  narrower situation, leaving a 3–5 line summary plus "read the full file when
  triggered" in place. The trigger is the `paths:` frontmatter, not the directory:
  a file moved into `refs/` without frontmatter is still resident.
- **The memory index cap is characters, not bytes.** The runtime limit is a token cap
  (≈ 25,000); CJK text is ~1.5× denser in bytes than in characters, so `wc -c` overstates
  by that factor. Measure with `python3 -c "print(len(open('MEMORY.md').read()))"`.
  Line counts never predict this at all: one 71-line index blew the cap.
- **A harness warning is not the cap.** The memory-index hook warns at ~17 KB
  ("approaching"); the real ceiling is the row above. One session spent hours compressing
  toward the warning, believing it was the limit.

## Measurement commands

Resident layer (files with no `paths:` frontmatter; recursive, because `refs/` files
without frontmatter are resident too):

```bash
cd ~/.claude && total=0
for f in $(find rules -name '*.md' ! -name 'README.md'); do
  head -1 "$f" | grep -q '^---$' || total=$((total + $(wc -c < "$f")))
done
echo "resident rules: $total bytes"; wc -c CLAUDE.md
```

Per-extension load (upper bound: it cannot tell a narrow glob like
`**/components/**/*.go` from `**/*.go`, so check each file's glob before acting):

```bash
cd ~/.claude/rules
for ext in go ts tsx py swift; do
  total=0
  for f in $(find . -name '*.md' ! -name 'README.md'); do
    awk '/^paths:/{f=1;next} /^---$/{if(f)exit} f' "$f" | grep -E '^[[:space:]]*- ' \
      | grep -q "\*\.$ext\"" && total=$((total + $(wc -c < "$f")))
  done
  echo ".$ext → $total bytes"
done
```

Memory index, in characters:

```bash
python3 -c "import sys;print(len(open(sys.argv[1]).read()))" \
  ~/.claude/projects/<project>/memory/MEMORY.md
```

Each of these has been wrong once (see the failure modes in `SKILL.md`). Sanity-check
the output against one file you can count by hand before trusting the total.

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
