"""Subject definition, pinned checkout, and the checks that must pass before any paid run. Stdlib only."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

# Git inside a run ignores the machine's git config, so commits behave the same everywhere.
GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "bench", "GIT_AUTHOR_EMAIL": "bench@example.invalid",
    "GIT_COMMITTER_NAME": "bench", "GIT_COMMITTER_EMAIL": "bench@example.invalid",
}


@dataclass(frozen=True)
class Subject:
    name: str
    dir: Path
    cache: Path
    repo: str
    commit: str
    plugin: str
    skill: str
    skill_path: str
    extra_files: dict
    fixture: str
    source: str
    test_command: tuple
    hidden_test: str
    mutants: dict
    mutant_wrapper: str
    sentinel: str
    tasks: dict

    @property
    def command(self) -> str:
        return f"{self.plugin}:{self.skill}"

    def skill_file(self, variant: str) -> Path:
        return self.cache / self.skill_path if variant == "before" else self.dir / "after" / "SKILL.md"


def load_subject(subject_dir: Path, cache_root: Path) -> Subject:
    spec = json.loads((subject_dir / "subject.json").read_text(encoding="utf-8"))
    return Subject(
        name=subject_dir.name, dir=subject_dir, cache=cache_root / subject_dir.name,
        repo=spec["repo"], commit=spec["commit"], plugin=spec["plugin"], skill=spec["skill"],
        skill_path=spec["skill_path"], extra_files=dict(spec.get("extra_files", {})), fixture=spec["fixture"],
        source=spec["source"], test_command=tuple(spec["test_command"]),
        hidden_test=spec["hidden_test"], mutants=dict(spec["mutants"]), mutant_wrapper=spec["mutant_wrapper"], sentinel=spec["sentinel"],
        tasks={int(t["id"]): t["prompt"] for t in spec["tasks"]},
    )


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, env={**os.environ, **GIT_ENV}, capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {cwd}: {done.stderr.strip()}")
    return done.stdout.strip()


def git_bytes(cwd: Path, *args: str) -> bytes | None:
    done = subprocess.run(["git", *args], cwd=cwd, env={**os.environ, **GIT_ENV}, capture_output=True)
    return done.stdout if done.returncode == 0 else None


def frontmatter_name(text: str) -> str | None:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            return None
        match = re.match(r"^name:\s*(.*?)\s*$", line)
        if match:
            return match.group(1).strip("'\"")
    return None


def git_head(path: Path) -> str | None:
    if not (path / ".git").exists():
        return None
    try:
        return git(path, "rev-parse", "HEAD")
    except RuntimeError:
        return None


def prepare(subject: Subject, log=print) -> None:
    if git_head(subject.cache) == subject.commit:
        log(f"{subject.cache} already at {subject.commit}")
        return
    if not subject.cache.exists():
        subject.cache.parent.mkdir(parents=True, exist_ok=True)
        git(subject.cache.parent, "clone", "--quiet", subject.repo, subject.cache.name)
    elif git_head(subject.cache) is None:
        raise RuntimeError(f"{subject.cache} exists but is not a git checkout; remove it and rerun prepare")
    else:
        git(subject.cache, "fetch", "--quiet", "origin")
    git(subject.cache, "checkout", "--quiet", "--detach", subject.commit)
    log(f"{subject.cache} at {subject.commit}")


API_KEY, OAUTH_TOKEN = "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"
AUTH_HELP = ("set ANTHROPIC_API_KEY for --auth api-key (runs with --bare), or run `claude setup-token` and export "
             "the token as CLAUDE_CODE_OAUTH_TOKEN for --auth oauth (subscription). Keep either in your shell "
             "environment only, never in a file")


def resolve_auth(requested: str, env: dict) -> tuple[str | None, str | None]:
    """(mode, None) or (None, why not). auto uses the only credential set and refuses to pick between two."""
    has = {"api-key": bool(env.get(API_KEY)), "oauth": bool(env.get(OAUTH_TOKEN))}
    names = {"api-key": API_KEY, "oauth": OAUTH_TOKEN}
    if requested in has:
        return (requested, None) if has[requested] else (None, f"--auth {requested} needs {names[requested]}: {AUTH_HELP}")
    if all(has.values()):
        return None, f"both {API_KEY} and {OAUTH_TOKEN} are set: pass --auth api-key or --auth oauth"
    found = [mode for mode, present in has.items() if present]
    return (found[0], None) if found else (None, f"no credential found: {AUTH_HELP}")


def claude_files_above(start: Path) -> list[Path]:
    """Every .claude/ directory and CLAUDE.md in start or any directory above it (symlinked and real paths)."""
    found, seen = [], set()
    for chain in ([start, *start.parents], [start.resolve(), *start.resolve().parents]):
        for directory in chain:
            for candidate, exists in ((directory / ".claude", Path.is_dir), (directory / "CLAUDE.md", Path.is_file)):
                if exists(candidate) and candidate.resolve() not in seen:
                    seen.add(candidate.resolve())
                    found.append(candidate)
    return found


def preflight(subject: Subject, env: dict, which=shutil.which, head=git_head, auth: str = "auto",
              temp_root: Path | None = None) -> list[str]:
    """Every reason `run` cannot start, so one attempt shows them all."""
    errors = []
    mode, problem = resolve_auth(auth, env)
    if problem:
        errors.append(problem)
    for name in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN"):
        if env.get(name) and len(env[name]) < 20:
            errors.append(f"{name} is only {len(env[name])} characters; real credentials are at least 20 characters, "
                          "and the bench cannot redact a value that short without mangling ordinary output")
    for name in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
        if env.get(name):
            errors.append(f"{name} is set: it can authenticate or route requests outside the chosen --auth mode, "
                          f"and the bench will not silently drop it. unset {name} before running the bench")
    if mode == "oauth":
        above = claude_files_above(temp_root or Path(tempfile.gettempdir()))
        if above:
            errors.append("--auth oauth runs without --bare, so Claude Code reads CLAUDE.md and .claude/ from every "
                          f"directory above a run's workspace. Found: {', '.join(map(str, above))}. Move them, or "
                          "point TMPDIR at a directory with none above it")
    after = subject.skill_file("after")
    if not after.is_file():
        errors.append(f"missing {after}: put the unhobble-slimmed SKILL.md there; the bench never writes it")
    for tool, why in (("claude", "it is what we measure"), ("node", "the quality checks run node"),
                      ("npm", "the quality checks run the repo's npm test"), ("git", "each workspace is a git repo")):
        if which(tool) is None:
            errors.append(f"{tool} not found on PATH ({why})")
    found = head(subject.cache)
    if found is None:
        errors.append(f"no checkout at {subject.cache}: run `python3 bench/run.py prepare --subject {subject.name}`")
    elif found != subject.commit:
        errors.append(f"{subject.cache} is at {found}, expected {subject.commit}: rerun prepare")
    if found is not None:
        errors += cache_integrity(subject)
    if after.is_file() and subject.skill_file("before").is_file():
        errors += variant_sanity(subject)
    return errors


def cache_integrity(subject: Subject) -> list[str]:
    """HEAD alone does not prove the files are the pinned ones."""
    errors = []
    dirty = git(subject.cache, "status", "--porcelain")
    if dirty:
        errors.append(f"{subject.cache} has uncommitted changes ({dirty.splitlines()[0].strip()} ...): "
                      "remove it and rerun prepare")
    before = subject.skill_file("before")
    pinned = git_bytes(subject.cache, "show", f"{subject.commit}:{subject.skill_path}")
    if pinned is None or not before.is_file() or before.read_bytes() != pinned:
        errors.append(f"{before} differs from the pinned commit {subject.commit}: remove the cache and rerun prepare")
    return errors


def variant_sanity(subject: Subject) -> list[str]:
    """after must be a different file for the same skill."""
    before, after = subject.skill_file("before"), subject.skill_file("after")
    errors = []
    if after.read_bytes() == before.read_bytes():
        errors.append(f"{after} is byte-identical to the original: there is nothing to compare")
    names = [frontmatter_name(f.read_text(encoding="utf-8")) for f in (before, after)]
    if names[0] != names[1]:
        errors.append(f"frontmatter name differs: before {names[0]!r}, after {names[1]!r}; "
                      "the slimmed skill must keep the original name")
    return errors
