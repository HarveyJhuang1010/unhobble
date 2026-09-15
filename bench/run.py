#!/usr/bin/env python3
"""A/B bench: one public skill, original (before) vs unhobble-slimmed (after). Stdlib only.

    run.py prepare --subject NAME                  clone the subject repo at its pinned commit
    run.py run --subject NAME --runs N [...]       interleaved runs, raw transcripts, results.jsonl
    run.py summarize RESULTS_DIR                   medians, CIs, pass rates -> stdout + summary.md
    run.py regrade RESULTS_DIR                     rerun every check on the archived workspaces

The two variants differ in exactly one file: the skill's SKILL.md. Run
test_bench.py first; `run` spends API money and a broken check wastes all of it.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import random
import shutil
import sys
import tempfile
import time
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path

import archive
import checks
import redaction
from grading import grade, transcript_fields
from prep import GIT_ENV, Subject, git, load_subject, prepare, preflight, resolve_auth
from proc import run_process, stream_process
from regrade import regrade, regrade_preflight  # noqa: F401 - part of this module's interface
from summary import read_rows, write_summary

BENCH = Path(__file__).resolve().parent
SUBJECTS = BENCH / "subjects"
CACHE = BENCH / ".cache"
RESULTS = BENCH / "results"
VARIANTS = ("before", "after")
DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_TIMEOUT = 900
MAX_CONSECUTIVE_ERRORS = 2

# File tools plus a short list of shell prefixes. No sed/perl/tee: edits to the
# source should go through Edit/Write, where the transcript checks can see them.
ALLOWED_TOOLS = ["Read", "Edit", "Write", "Glob", "Grep"] + [
    f"Bash({name}:*)" for name in
    ("node", "npm", "npx", "git", "ls", "cat", "cd", "pwd", "head", "tail", "grep", "find", "wc", "diff")
]
# Keep file tools out of the bench itself (hidden test, mutants, cached evals, earlier results).
# `//` marks an absolute path in permission rules. Shell reads (cat, grep, find) cannot be
# fenced off this way; leak_suspect is what catches those.
DENIED_TOOLS = [f"Read(/{BENCH}/**)", f"Edit(/{BENCH}/**)"]
# Environment variables that change Claude Code's behaviour or where it sends requests.
BEHAVIOUR_VARS = {"MAX_THINKING_TOKENS", "BASH_DEFAULT_TIMEOUT_MS", "BASH_MAX_TIMEOUT_MS", "MCP_TIMEOUT",
                  "HTTP_PROXY", "HTTPS_PROXY", "NODE_OPTIONS"}
PLAIN_VALUES = {"MAX_THINKING_TOKENS", "BASH_DEFAULT_TIMEOUT_MS", "BASH_MAX_TIMEOUT_MS", "MCP_TIMEOUT"}

# ------------------------------------------------------------------- schedule


@dataclass(frozen=True)
class Slot:
    run_index: int
    task: int
    variant: str
    position: int  # 0 or 1: which of the pair ran first
    sequence: int  # global order in the batch
    warmup: bool = False  # run and recorded, never counted

    @property
    def run_id(self) -> str:
        return f"t{self.task}-{'w' if self.warmup else 'r'}{self.run_index:03d}-{self.variant}"


def pair(order: list[str], run_index: int, task: int, start: int, warmup: bool) -> list[Slot]:
    return [Slot(run_index, task, variant, position, start + position, warmup) for position, variant in enumerate(order)]


def schedule(runs: int, tasks: list[int], seed: int, warmup: int = 0) -> list[Slot]:
    """Warmup rounds, then counted rounds; in each, every task runs both variants back to back.

    Within a task, `before` goes first in exactly half the counted rounds (one
    more for either side when runs is odd), in a seeded random selection of
    rounds. Interleaving spreads API latency drift over both variants; the
    balance keeps any first-or-second effect from favouring one."""
    rng = random.Random(seed)
    before_first = {}
    for task in tasks:
        flags = [True] * (runs // 2) + [False] * (runs // 2) + ([rng.random() < 0.5] if runs % 2 else [])
        rng.shuffle(flags)
        before_first[task] = flags
    slots: list[Slot] = []
    for run_index in range(warmup):
        for task in tasks:
            order = list(VARIANTS)
            rng.shuffle(order)
            slots += pair(order, run_index, task, len(slots), warmup=True)
    for run_index in range(runs):
        for task in tasks:
            order = list(VARIANTS) if before_first[task][run_index] else list(reversed(VARIANTS))
            slots += pair(order, run_index, task, len(slots), warmup=False)
    return slots


# ------------------------------------------------------------------ execution


def build_workspace(dest: Path, fixture: Path) -> str:
    shutil.copytree(fixture, dest)
    git(dest, "init", "--quiet")
    git(dest, "add", "-A")
    git(dest, "commit", "--quiet", "-m", "baseline")
    return git(dest, "rev-parse", "HEAD")


def build_plugin(dest: Path, subject: Subject, variant: str) -> Path:
    """A one-skill plugin. Extra files keep the skill's relative links (../../references/...) resolving."""
    manifest = dest / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"name": subject.plugin}) + "\n", encoding="utf-8")
    skill_dir = dest / "skills" / subject.skill
    skill_dir.mkdir(parents=True)
    shutil.copyfile(subject.skill_file(variant), skill_dir / "SKILL.md")
    for target, source in subject.extra_files.items():
        (dest / target).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(subject.cache / source, dest / target)
    return dest


def claude_command(subject: Subject, prompt: str, plugin: Path, model: str, auth: str = "api-key") -> list[str]:
    """api-key: --bare. oauth: --bare would ignore the token, so --strict-mcp-config stands in for its MCP isolation."""
    return [
        "claude", *(["--bare"] if auth == "api-key" else []), "-p", f"/{subject.command} {prompt}",
        "--plugin-dir", str(plugin), "--add-dir", str(plugin), "--model", model,
        "--output-format", "stream-json", "--verbose", "--no-session-persistence",
        *(["--strict-mcp-config"] if auth == "oauth" else []),
        "--permission-mode", "acceptEdits",
        "--disallowedTools", *DENIED_TOOLS,  # variadic, ended by the next flag
        "--allowedTools", *ALLOWED_TOOLS,  # variadic: keep it last
    ]


def child_env(base: dict, config_dir: Path, auth: str = "api-key") -> dict:
    """The caller's environment minus anything from an enclosing Claude Code session, with one credential only."""
    def keep(name: str) -> bool:
        if name == "CLAUDE_CODE_OAUTH_TOKEN":
            return auth == "oauth"
        if name == "ANTHROPIC_API_KEY":
            return auth == "api-key"
        if name == "ANTHROPIC_AUTH_TOKEN":
            return False  # a third credential would let either mode authenticate some other way
        return name not in ("CLAUDECODE", "CLAUDE_CONFIG_DIR") and not name.startswith("CLAUDE_CODE_")
    return {**{k: v for k, v in base.items() if keep(k)}, "CLAUDE_CONFIG_DIR": str(config_dir), **GIT_ENV}


def env_record(env: dict) -> dict:
    """Which behaviour-relevant variables a run saw. Values only for names that cannot hold secrets."""
    record = {name: {"set": bool(env.get(name))} for name in ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN")}
    for name in sorted(env):
        if not (name.startswith(("ANTHROPIC_", "DISABLE_")) or name in BEHAVIOUR_VARS):
            continue
        secret = name not in PLAIN_VALUES and any(w in name for w in ("KEY", "TOKEN", "SECRET", "AUTH", "PROXY"))
        plain = name.endswith("_MODEL") or name in PLAIN_VALUES or name.startswith("DISABLE_")  # a URL may hold a password
        record[name] = {"set": True, "value": env[name]} if plain and not secret else {"set": True}
    return record


def claude_runner(cmd: list[str], cwd: Path, env: dict, timeout: float):
    return stream_process(cmd, cwd, env, timeout, should_abort=checks.auth_failure)


def workspace_diff(workspace: Path, base: str) -> str:
    git(workspace, "add", "-A")
    return git(workspace, "diff", "--cached", base)


def execute(slot: Slot, subject: Subject, model: str, seed: int, out_dir: Path, runner, timeout: float,
            env: dict, auth: str, secrets: list[str]) -> dict:
    with tempfile.TemporaryDirectory(prefix="bench-run-") as tmp:
        root = Path(tmp)
        workspace = root / "workspace"
        base = build_workspace(workspace, subject.cache / subject.fixture)
        plugin = build_plugin(root / "plugin", subject, slot.variant)
        config = root / "config"
        config.mkdir()
        started = time.time()
        outcome = runner(claude_command(subject, subject.tasks[slot.task], plugin, model, auth),
                         workspace, child_env(env, config, auth), timeout)
        wall_ms = round((time.time() - started) * 1000)

        stdout = redaction.redact(outcome.stdout, secrets)  # nothing unredacted is written or kept
        raw = Path("raw") / f"{slot.run_id}.jsonl"
        (out_dir / raw).write_text(stdout, encoding="utf-8")
        if outcome.stderr:
            (out_dir / "raw" / f"{slot.run_id}.stderr.txt").write_text(redaction.redact(outcome.stderr, secrets),
                                                                        encoding="utf-8")
        events = checks.parse_events(stdout)
        skill_dir = plugin / "skills" / subject.skill
        skill_dirs = list(dict.fromkeys([str(skill_dir), str(skill_dir.resolve())]))
        row = {
            **slot_fields(slot, subject, model, seed, timeout),
            "started_at": datetime.datetime.fromtimestamp(started, datetime.timezone.utc).isoformat(),
            "skill_md_bytes": subject.skill_file(slot.variant).stat().st_size,
            "exit_code": outcome.exit_code, "timed_out": outcome.timed_out,
            "abort_reason": redaction.redact(outcome.abort_reason, secrets) if outcome.abort_reason
            else checks.quota_exhausted(events),
            "wall_ms": wall_ms, **checks.metrics(events), **transcript_fields(events, subject, auth), "auth": auth,
            "raw": raw.as_posix(), "skill_dirs": skill_dirs,
        }
        try:  # the run is already paid for: a failing check must not lose its metrics
            packed = Path("raw") / f"{slot.run_id}.workspace.tar.gz"
            info = archive.archive_workspace(workspace, out_dir / packed, secrets)
            row.update(workspace_has_symlinks=bool(info["symlinks"]), workspace_symlinks=info["symlinks"],
                       archive_names_redacted=info["names_redacted"])
            if info["skipped"]:
                row["archive_skipped"] = info["skipped"]
            else:
                row["workspace"] = packed.as_posix()
            row["checks"] = grade(subject, workspace, events, skill_dirs)
            diff = Path("raw") / f"{slot.run_id}.diff"
            (out_dir / diff).write_text(redaction.redact(workspace_diff(workspace, base), secrets), encoding="utf-8")
            row["diff"] = diff.as_posix()
        except Exception as exc:  # noqa: BLE001
            row.update(error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc())
        return row


def slot_fields(slot: Slot, subject: Subject, model: str, seed: int, timeout: float) -> dict:
    return {"subject": subject.name, **asdict(slot), "run_id": slot.run_id, "model": model, "seed": seed,
            "timeout": timeout}


def run_batch(subject: Subject, slots: list[Slot], model: str, seed: int, out_dir: Path, runner=claude_runner,
              timeout: float = DEFAULT_TIMEOUT, env: dict | None = None, log=print, auth: str = "api-key") -> str | None:
    """One results.jsonl line per slot. Returns why the batch stopped early, or None.

    A crashed run is recorded and the batch goes on. An authentication failure or
    spent quota, or MAX_CONSECUTIVE_ERRORS runs in a row ending in is_error or
    degenerate, stops it. Credential values are redacted from everything written."""
    (out_dir / "raw").mkdir(parents=True, exist_ok=True)
    env = dict(os.environ) if env is None else env
    secrets = redaction.credentials(env)
    errors_in_a_row = degenerate_in_a_row = 0
    for slot in slots:
        try:
            row = execute(slot, subject, model, seed, out_dir, runner, timeout, env, auth, secrets)
        except Exception as exc:  # noqa: BLE001 - one bad run must not lose the rest of the batch
            row = {**slot_fields(slot, subject, model, seed, timeout),
                   "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}
        row = json.loads(redaction.redact(json.dumps(row, ensure_ascii=False), secrets))
        with (out_dir / "results.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        log(f"[{slot.sequence + 1}/{len(slots)}] {slot.run_id}: {status(row)}")
        if row.get("abort_reason"):
            return f"batch stopped: {row['abort_reason']}"
        errors_in_a_row = errors_in_a_row + 1 if row.get("is_error") is True else 0
        if errors_in_a_row >= MAX_CONSECUTIVE_ERRORS:
            return f"{errors_in_a_row} runs in a row ended with is_error, batch stopped; see raw/{slot.run_id}.jsonl"
        degenerate_in_a_row = degenerate_in_a_row + 1 if row.get("degenerate_run") is True else 0
        if degenerate_in_a_row >= MAX_CONSECUTIVE_ERRORS:
            return (f"{degenerate_in_a_row} degenerate runs in a row (no tool call and nothing produced), batch stopped: "
                    f"the environment is ending runs, not the model; see raw/{slot.run_id}.jsonl")
    return None


def status(row: dict) -> str:
    if "error" in row:
        return f"CRASHED ({row['error']})"
    if row.get("abort_reason"):
        return f"ABORTED ({row['abort_reason']})"
    return "timed out" if row.get("timed_out") else f"exit {row.get('exit_code')}"


def run_and_summarize(subject: Subject, slots: list[Slot], model: str, seed: int, out_dir: Path,
                      runner=claude_runner, timeout: float = DEFAULT_TIMEOUT, env: dict | None = None,
                      log=print, auth: str = "api-key") -> int:
    """Run the batch and always summarise what finished. 0 done, 1 stopped early, 130 Ctrl-C."""
    try:
        stopped = run_batch(subject, slots, model, seed, out_dir, runner, timeout, env, log, auth)
        code = 1 if stopped else 0
    except KeyboardInterrupt:
        stopped, code = "interrupted (Ctrl-C); the run in progress was killed and not recorded", 130
    if (out_dir / "results.jsonl").is_file():
        log(write_summary(out_dir))
    if stopped:
        print(f"STOPPED: {stopped}", file=sys.stderr)
    return code


# ------------------------------------------------------------------------ CLI


def version(cmd: list[str]) -> str | None:
    try:
        return run_process(cmd, BENCH, timeout=30).stdout.strip() or None
    except OSError:
        return None


def cmd_prepare(args: argparse.Namespace) -> int:
    prepare(load_subject(SUBJECTS / args.subject, CACHE))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    subject = load_subject(SUBJECTS / args.subject, CACHE)
    tasks = sorted(subject.tasks) if args.tasks is None else [int(t) for t in args.tasks.split(",")]
    errors = preflight(subject, dict(os.environ), auth=args.auth)
    errors += [f"task {t} is not defined in subject.json" for t in tasks if t not in subject.tasks]
    errors += ["--runs must be at least 1"] if args.runs < 1 else []
    errors += ["--warmup cannot be negative"] if args.warmup < 0 else []
    if errors:
        print("cannot run:\n" + "\n".join(f"  - {e}" for e in errors), file=sys.stderr)
        return 2
    auth, _ = resolve_auth(args.auth, dict(os.environ))
    seed = random.randrange(2**32) if args.seed is None else args.seed
    out_dir = RESULTS / datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    write_config(out_dir, subject, model=args.model, runs=args.runs, warmup=args.warmup, tasks=tasks, seed=seed,
                 timeout=args.timeout, auth=auth, env=dict(os.environ))
    print(f"results -> {out_dir} (seed {seed}, auth {auth})")
    slots = schedule(args.runs, tasks, seed, warmup=args.warmup)
    return run_and_summarize(subject, slots, args.model, seed, out_dir, timeout=args.timeout, auth=auth)


def write_config(out_dir: Path, subject: Subject, *, model: str, runs: int, warmup: int, tasks: list[int], seed: int,
                 timeout: float, auth: str, env: dict, versions: dict | None = None) -> None:
    """Everything needed to reproduce the batch. The auth mode is recorded by name; no credential, length or prefix."""
    if versions is None:
        versions = {f"{tool}_version": version([tool, "--version"]) for tool in ("claude", "node", "npm")}
    config = {
        "subject": subject.name, "repo": subject.repo, "commit": subject.commit, "model": model, "auth": auth,
        "runs": runs, "warmup": warmup, "tasks": tasks, "seed": seed, "timeout": timeout,
        "skill_md_bytes": {v: subject.skill_file(v).stat().st_size for v in VARIANTS}, **versions,
        "env": env_record(child_env(env, Path("<per-run>"), auth)),
        "command": claude_command(subject, "<task prompt>", Path("<plugin>"), model, auth),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "config.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def cmd_summarize(args: argparse.Namespace) -> int:
    print(write_summary(Path(args.results_dir)))
    return 0


def cmd_regrade(args: argparse.Namespace) -> int:
    out_dir = Path(args.results_dir)
    config = json.loads((out_dir / "config.json").read_text(encoding="utf-8"))
    subject = load_subject(SUBJECTS / config["subject"], CACHE)
    errors = regrade_preflight(out_dir, subject)
    if errors:
        print("cannot regrade:\n" + "\n".join(f"  - {e}" for e in errors), file=sys.stderr)
        return 2
    regrade(out_dir, subject)
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare", help="clone the subject repo at its pinned commit")
    p.add_argument("--subject", required=True)
    p.set_defaults(run=cmd_prepare)
    p = sub.add_parser("run", help="run both variants, interleaved (spends API money)")
    p.add_argument("--subject", required=True)
    p.add_argument("--runs", type=int, required=True, help="counted rounds; each runs every task once per variant")
    p.add_argument("--warmup", type=int, default=1, help="uncounted rounds before them (default 1)")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--tasks", help="comma-separated task ids (default: all in subject.json)")
    p.add_argument("--seed", type=int, help="schedule seed (default: random, recorded in config.json)")
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="seconds per claude run")
    p.add_argument("--auth", choices=("auto", "api-key", "oauth"), default="auto",
                   help="api-key: ANTHROPIC_API_KEY with --bare; oauth: CLAUDE_CODE_OAUTH_TOKEN (claude setup-token); "
                        "auto: whichever one is set")
    p.set_defaults(run=cmd_run)
    p = sub.add_parser("summarize", help="summarise a results directory")
    p.add_argument("results_dir")
    p.set_defaults(run=cmd_summarize)
    p = sub.add_parser("regrade", help="rerun all checks on archived workspaces -> results.regraded.jsonl")
    p.add_argument("results_dir")
    p.set_defaults(run=cmd_regrade)
    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
