#!/usr/bin/env python3
"""Deterministic measurements for unhobble. Stdlib only.

    measure.py resident <dir> [--claude-md FILE]   resident rule bytes + CLAUDE.md
    measure.py load <rules-dir> <sample-path>...    path-scoped rules one file pulls in
    measure.py memory <MEMORY.md>                   where the index gets cut off
    measure.py facts <file>... [--root DIR]         imports, paths, model IDs, commands

It lists; it does not judge. Run test_measure.py first: these commands have been
wrong before, and the wrong output looked like broken content.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

RESIDENT_TOTAL_LIMIT = 35_000
RESIDENT_FILE_LIMIT = 12_000
LOAD_LIMIT = 30_000
MEMORY_LINE_LIMIT = 200  # documented
MEMORY_BYTE_LIMIT = 25_000  # documented as "25KB"
MEMORY_CHAR_LIMIT = 24_985  # measured: the cut fell at this many characters

# ---------------------------------------------------------------- frontmatter


def frontmatter(text: str) -> list[str] | None:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return lines[1:i]
    return None


def path_patterns(text: str) -> tuple[str, ...]:
    """The `paths:` globs of a rule file; empty when it has none (= resident)."""
    fm = frontmatter(text) or []
    patterns: list[str] = []
    in_paths = False
    for line in fm:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key = re.match(r"^paths:\s*(.*)$", line)
        if key:
            in_paths = True
            patterns.extend(re.findall(r"""["']([^"']+)["']""", key.group(1)))
            continue
        if in_paths and re.match(r"^\s*-\s*", line):
            patterns.append(re.sub(r"^\s*-\s*", "", line).strip().strip("\"'"))
        elif not line.startswith((" ", "\t")):
            in_paths = False
    return tuple(p for p in patterns if p)


# ------------------------------------------------------------------- resident


@dataclass(frozen=True)
class ResidentReport:
    files: tuple[tuple[str, int], ...]
    claude_md_bytes: int

    @property
    def rules_total(self) -> int:
        return sum(size for _, size in self.files)

    @property
    def total(self) -> int:
        return self.rules_total + self.claude_md_bytes


def rule_files(rules_dir: Path) -> list[Path]:
    if not rules_dir.is_dir():
        return []
    return sorted(p for p in rules_dir.rglob("*.md") if p.name != "README.md")


def resident(base: Path, claude_md: Path | None = None) -> ResidentReport:
    rules_dir = base / "rules"
    files = tuple(
        (p.relative_to(rules_dir).as_posix(), p.stat().st_size)
        for p in rule_files(rules_dir)
        if not path_patterns(p.read_text(encoding="utf-8"))
    )
    md = claude_md if claude_md is not None else base / "CLAUDE.md"
    return ResidentReport(files, md.stat().st_size if md.is_file() else 0)


# ------------------------------------------------------------------- per load


def expand_braces(pattern: str) -> list[str]:
    match = re.search(r"\{([^{}]*)\}", pattern)
    if not match:
        return [pattern]
    head, tail = pattern[: match.start()], pattern[match.end() :]
    return [out for alt in match.group(1).split(",") for out in expand_braces(head + alt + tail)]


def glob_regex(pattern: str) -> re.Pattern[str]:
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        elif pattern[i] == "[" and "]" in pattern[i + 1 :]:
            end = pattern.index("]", i + 1)
            out.append(pattern[i : end + 1])
            i = end + 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out))


def matches(pattern: str, sample: str) -> bool:
    return any(glob_regex(p).fullmatch(sample) for p in expand_braces(pattern))


def matching_rules(rules_dir: Path, sample: str) -> tuple[tuple[str, int], ...]:
    sample = sample.lstrip("./")
    return tuple(
        (p.relative_to(rules_dir).as_posix(), p.stat().st_size)
        for p in rule_files(rules_dir)
        if any(matches(g, sample) for g in path_patterns(p.read_text(encoding="utf-8")))
    )


# --------------------------------------------------------------- memory index


@dataclass(frozen=True)
class MemoryReport:
    lines: int
    chars: int
    bytes: int
    loaded_by_lines: int
    loaded_by_bytes: int
    loaded_by_chars: int

    @property
    def loaded_lines(self) -> int:
        return min(self.loaded_by_lines, self.loaded_by_bytes, self.loaded_by_chars)


def lines_within(sizes: list[int], limit: int) -> int:
    total = 0
    for count, size in enumerate(sizes):
        total += size
        if total > limit:
            return count
    return len(sizes)


def memory_index(text: str) -> MemoryReport:
    lines = text.splitlines(keepends=True)
    return MemoryReport(
        lines=len(lines),
        chars=len(text),
        bytes=len(text.encode("utf-8")),
        loaded_by_lines=min(len(lines), MEMORY_LINE_LIMIT),
        loaded_by_bytes=lines_within([len(l.encode("utf-8")) for l in lines], MEMORY_BYTE_LIMIT),
        loaded_by_chars=lines_within([len(l) for l in lines], MEMORY_CHAR_LIMIT),
    )


# ---------------------------------------------------------------------- facts

FENCE = re.compile(r"^(`{3,}|~{3,})\s*(\S*)")
IMPORT = re.compile(r"(?<![\w@`])@(~?[\w./-]*[\w/])")
MODEL = re.compile(r"\bclaude-[a-z0-9.-]*\d[a-z0-9.-]*[a-z0-9]")
SHELL_LANGS = {"bash", "sh", "shell", "zsh", "console"}
NOT_COMMANDS = {
    "cd", "export", "source", ".", "for", "while", "until", "if", "then", "else", "elif",
    "fi", "do", "done", "case", "esac", "in", "function", "return", "exit", "set", "unset",
    "local", "read", "true", "false", "echo", "printf", "test", "[", "[[", "time", "exec",
}


@dataclass(frozen=True)
class FactsReport:
    imports: tuple[tuple[str, bool], ...]
    paths: tuple[tuple[str, bool], ...]
    models: tuple[str, ...]
    commands: tuple[tuple[str, bool], ...]


def split_fences(text: str) -> tuple[list[str], list[list[str]]]:
    """Prose lines, and the lines of every shell-language fenced block."""
    prose: list[str] = []
    shell_blocks: list[list[str]] = []
    fence: str | None = None
    current: list[str] | None = None  # the shell block being read, if any
    for line in text.splitlines():
        opener = FENCE.match(line.strip())
        if fence is None and opener:
            fence = opener.group(1)
            current = [] if opener.group(2).lower() in SHELL_LANGS else None
            if current is not None:
                shell_blocks.append(current)
        elif fence is not None and line.strip().startswith(fence):
            fence, current = None, None
        elif fence is None:
            prose.append(line)
        elif current is not None:
            current.append(line)
    return prose, shell_blocks


def resolves(ref: str, file: Path, root: Path) -> bool:
    candidates = [Path(ref).expanduser()] if ref.startswith(("~", "/")) else [file.parent / ref, root / ref]
    return any(c.exists() for c in candidates)


def unique(items: list) -> list:
    return list(dict.fromkeys(items))


def command_names(block: list[str]) -> list[str]:
    names: list[str] = []
    heredoc: str | None = None
    continued = False
    for raw in block:
        line = raw.strip()
        if heredoc is not None:
            heredoc = None if line == heredoc else heredoc
            continue
        was_continued, continued = continued, line.endswith("\\")
        if was_continued or not line or line.startswith("#"):
            continue
        marker = re.search(r"<<-?\s*['\"]?(\w+)['\"]?", line)
        heredoc = marker.group(1) if marker else None
        names.extend(re.findall(r"\$\((\w[\w.+-]*)", line))  # command substitution
        for segment in re.split(r"&&|\|\||;|\|", line.removeprefix("$ ")):
            tokens = [t for t in segment.split() if not re.match(r"^\w+=", t) and t != "sudo"]
            if tokens and re.match(r"^\w[\w.+-]*$", tokens[0]) and tokens[0] not in NOT_COMMANDS:
                names.append(tokens[0])
    return names


def facts(file: Path, root: Path) -> FactsReport:
    prose, shell_blocks = split_fences(file.read_text(encoding="utf-8"))
    body = "\n".join(prose)
    spans = re.findall(r"`([^`\n]+)`", body)
    outside_spans = re.sub(r"`[^`\n]+`", " ", body)
    path_refs = [
        s for s in spans
        if "/" in s and not s.startswith("@") and not re.search(r"[\s<>{}*$|]|://", s)
        and not re.fullmatch(r"/[^/]*", s)  # `/help`, `/plugin:skill`: slash commands
    ]
    return FactsReport(
        imports=tuple((ref, resolves(ref, file, root)) for ref in unique(IMPORT.findall(outside_spans))),
        paths=tuple((ref, resolves(ref, file, root)) for ref in unique(path_refs)),
        models=tuple(unique(MODEL.findall(body))),
        commands=tuple(
            (name, shutil.which(name) is not None)
            for name in unique([n for block in shell_blocks for n in command_names(block)])
        ),
    )


# ------------------------------------------------------------------------ CLI


def flag(value: int, limit: int) -> str:
    return f"OVER {limit:,}" if value > limit else f"ok (< {limit:,})"


def print_resident(args: argparse.Namespace) -> None:
    report = resident(Path(args.dir).expanduser(), Path(args.claude_md).expanduser() if args.claude_md else None)
    for name, size in sorted(report.files, key=lambda f: -f[1]):
        print(f"{size:>8,}  {name}  {'OVER ' + format(RESIDENT_FILE_LIMIT, ',') if size > RESIDENT_FILE_LIMIT else ''}")
    print(f"{report.rules_total:>8,}  resident rules ({len(report.files)} files)")
    print(f"{report.claude_md_bytes:>8,}  CLAUDE.md")
    print(f"{report.total:>8,}  resident total bytes  {flag(report.total, RESIDENT_TOTAL_LIMIT)}")


def print_load(args: argparse.Namespace) -> None:
    for sample in args.samples:
        found = matching_rules(Path(args.rules_dir).expanduser(), sample)
        total = sum(size for _, size in found)
        print(f"{sample}: {total:,} bytes from {len(found)} path-scoped files  {flag(total, LOAD_LIMIT)}")
        for name, size in found:
            print(f"  {size:>8,}  {name}")


def print_memory(args: argparse.Namespace) -> None:
    r = memory_index(Path(args.file).expanduser().read_text(encoding="utf-8"))
    print(f"{r.lines:,} lines, {r.chars:,} characters, {r.bytes:,} bytes")
    print(f"  lines loaded under the {MEMORY_LINE_LIMIT}-line limit (documented): {r.loaded_by_lines:,}")
    print(f"  lines loaded under the {MEMORY_BYTE_LIMIT:,}-byte limit (documented as 25KB): {r.loaded_by_bytes:,}")
    print(f"  lines loaded under the {MEMORY_CHAR_LIMIT:,}-character limit (measured): {r.loaded_by_chars:,}")
    lost = r.lines - r.loaded_lines
    print(f"  => {r.loaded_lines:,} of {r.lines:,} lines load" + (f"; last {lost:,} never reach context" if lost else ""))


def print_facts(args: argparse.Namespace) -> None:
    root = Path(args.root).expanduser()
    for name in args.files:
        report = facts(Path(name).expanduser(), root)
        print(f"== {name}")
        for label, rows in (("@import", report.imports), ("path", report.paths), ("command", report.commands)):
            for ref, ok in rows:
                print(f"  {'ok     ' if ok else 'MISSING'}  {label:<8} {ref}")
        for model in report.models:
            print(f"  JUDGE    model    {model}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("resident")
    p.add_argument("dir")
    p.add_argument("--claude-md")
    p.set_defaults(run=print_resident)
    p = sub.add_parser("load")
    p.add_argument("rules_dir")
    p.add_argument("samples", nargs="+")
    p.set_defaults(run=print_load)
    p = sub.add_parser("memory")
    p.add_argument("file")
    p.set_defaults(run=print_memory)
    p = sub.add_parser("facts")
    p.add_argument("files", nargs="+")
    p.add_argument("--root", default=".")
    p.set_defaults(run=print_facts)
    args = parser.parse_args(argv)
    args.run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
