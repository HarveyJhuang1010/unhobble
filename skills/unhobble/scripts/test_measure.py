"""Self-test for measure.py. Run before trusting any number it prints:

    python3 test_measure.py

Every expected value below is hand-countable from the fixture written in the
same test, which is the point: the measurement commands have been wrong before,
and each time the wrong output looked like broken content.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import measure  # noqa: E402


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class TempDirCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.root)


class ResidentLayer(TempDirCase):
    def test_only_files_with_paths_frontmatter_are_on_demand(self) -> None:
        write(self.root, "rules/plain.md", "0123456789")  # 10 bytes, resident
        write(self.root, "rules/scoped.md", '---\npaths:\n  - "**/*.go"\n---\nbody\n')
        # Frontmatter without paths: still loads at launch.
        no_paths = "---\ndescription: x\n---\nbody\n"
        write(self.root, "rules/meta.md", no_paths)
        write(self.root, "rules/refs/deep.md", "abcde")  # 5 bytes, resident: refs/ is not special
        write(self.root, "rules/README.md", "ignored")
        write(self.root, "CLAUDE.md", "x" * 7)

        report = measure.resident(self.root)

        self.assertEqual(
            dict(report.files),
            {"plain.md": 10, "meta.md": len(no_paths.encode()), "refs/deep.md": 5},
        )
        self.assertEqual(report.rules_total, 10 + len(no_paths.encode()) + 5)
        self.assertEqual(report.claude_md_bytes, 7)
        self.assertEqual(report.total, report.rules_total + 7)

    def test_bytes_not_characters(self) -> None:
        write(self.root, "rules/cjk.md", "中文")  # 2 characters, 6 bytes
        self.assertEqual(dict(measure.resident(self.root).files), {"cjk.md": 6})


class PerLoad(TempDirCase):
    def setUp(self) -> None:
        super().setUp()
        rules = self.root / "rules"
        write(rules, "go.md", '---\npaths:\n  - "**/*.go"\n---\n' + "g" * 100)
        write(rules, "wrap.md", '---\npaths:\n  - "**/components/**/*.go"\n---\n' + "w" * 50)
        write(rules, "web.md", '---\npaths:\n  - "src/**/*.{ts,tsx}"\n---\n')
        # Quoted globs inside comments must not count as patterns, and a comment at
        # column 0 must not end the list.
        write(rules, "noise.md", '---\npaths:\n  # "**/*.py" was removed\n# "**/*.rb" too\n  - "docs/**"\n---\n')
        write(rules, "always.md", "resident, not path-scoped")
        self.rules = rules

    def names(self, sample: str) -> set[str]:
        return {name for name, _ in measure.matching_rules(self.rules, sample)}

    def test_double_star_matches_zero_directories(self) -> None:
        self.assertEqual(self.names("main.go"), {"go.md"})

    def test_narrow_glob_only_adds_in_its_own_situation(self) -> None:
        self.assertEqual(self.names("internal/x.go"), {"go.md"})
        self.assertEqual(self.names("a/components/b/c.go"), {"go.md", "wrap.md"})

    def test_brace_expansion(self) -> None:
        self.assertEqual(self.names("src/app/page.tsx"), {"web.md"})
        self.assertEqual(self.names("page.tsx"), set())

    def test_comments_and_unscoped_files_never_match(self) -> None:
        self.assertEqual(self.names("tool.py"), set())
        self.assertEqual(self.names("tool.rb"), set())

    def test_list_continues_past_a_column_zero_comment(self) -> None:
        self.assertEqual(self.names("docs/guide.md"), {"noise.md"})

    def test_reports_bytes_per_matched_file(self) -> None:
        sizes = dict(measure.matching_rules(self.rules, "main.go"))
        self.assertEqual(sizes["go.md"], (self.rules / "go.md").stat().st_size)


class MemoryIndex(unittest.TestCase):
    def test_line_limit_cuts_first_for_many_short_lines(self) -> None:
        report = measure.memory_index("- x\n" * 250)
        self.assertEqual(report.lines, 250)
        self.assertEqual(report.loaded_by_lines, 200)
        self.assertEqual(report.loaded_lines, 200)

    def test_character_and_byte_cuts_differ_for_cjk(self) -> None:
        line = "中" * 99 + "\n"  # 100 characters, 298 bytes
        report = measure.memory_index(line * 150)
        self.assertEqual(report.chars, 15_000)
        self.assertEqual(report.bytes, 44_700)
        self.assertEqual(report.loaded_by_lines, 150)
        self.assertEqual(report.loaded_by_bytes, 25_000 // 298)
        self.assertEqual(report.loaded_by_chars, 150)
        self.assertEqual(report.loaded_lines, 25_000 // 298)

    def test_everything_loads_when_under_all_limits(self) -> None:
        report = measure.memory_index("- a\n- b\n")
        self.assertEqual(report.loaded_lines, 2)


class Facts(TempDirCase):
    def test_imports_paths_models_and_commands(self) -> None:
        write(self.root, "docs/a.md", "exists")
        write(self.root, "scripts/run.sh", "exists")
        target = write(
            self.root,
            "CLAUDE.md",
            "\n".join(
                [
                    "See @docs/a.md and @missing.md.",
                    "Mail me at someone@example.com.",
                    "Literal, not an import: `see @README`.",
                    "Run `scripts/run.sh`, not `nope/x.sh`, never `git add <paths>`.",
                    "Slash commands are not paths: `/sc:...`, `/plugin:skill`, `/help`.",
                    "Absolute paths still are: `/definitely/not/here.md`.",
                    "Subagents use claude-3-5-sonnet.",
                    "```bash",
                    "cd /tmp && git status",
                    'total=$((total + $(wc -c < "$f")))',
                    "FOO=1 definitely-not-a-cmd-xyz --flag \\",
                    "  continued-line-is-not-a-command",
                    "python3 - <<'EOF'",
                    "inside_heredoc_is_not_a_command",
                    "EOF",
                    "```",
                    "```text",
                    "@not/an/import.md",
                    "```",
                ]
            ),
        )

        report = measure.facts(target, self.root)

        self.assertEqual(dict(report.imports), {"docs/a.md": True, "missing.md": False})
        self.assertNotIn("README", dict(report.imports))
        self.assertEqual(
            dict(report.paths),
            {"scripts/run.sh": True, "nope/x.sh": False, "/definitely/not/here.md": False},
        )
        self.assertEqual(report.models, ("claude-3-5-sonnet",))
        commands = dict(report.commands)
        self.assertTrue(commands["git"])
        self.assertTrue(commands["python3"])
        self.assertFalse(commands["definitely-not-a-cmd-xyz"])
        self.assertTrue(commands["wc"])
        for skipped in ("cd", "+", "continued-line-is-not-a-command", "inside_heredoc_is_not_a_command"):
            self.assertNotIn(skipped, commands)


if __name__ == "__main__":
    unittest.main(verbosity=2)
