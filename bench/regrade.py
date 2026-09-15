"""Rerun every check on archived workspaces and raw transcripts. Stdlib only.

Outcome and mutation checks rerun on the restored workspace; transcript checks
are recomputed from raw/<run-id>.jsonl; cost metrics are copied. node_modules
was never archived, so a run whose tests needed packages the model installed
can grade differently here than it did originally.
"""
from __future__ import annotations

import datetime
import json
import tempfile
import traceback
from pathlib import Path

import checks
import summary
from archive import extract_workspace
from grading import grade, transcript_fields
from prep import Subject, cache_integrity, git_head


def regrade_preflight(out_dir: Path, subject: Subject) -> list[str]:
    config = json.loads((out_dir / "config.json").read_text(encoding="utf-8"))
    found = git_head(subject.cache)
    if found != config["commit"]:
        return [f"{subject.cache} is at {found}, not {config['commit']}: run prepare"]
    return cache_integrity(subject)


def regrade_row(row: dict, subject: Subject, out_dir: Path) -> dict:
    if "workspace" not in row:
        if row.get("archive_skipped"):
            return {**row, "regrade_skipped": f"no archive to regrade: {row['archive_skipped']}"}
        return row  # crashed before claude finished: nothing to grade
    events = checks.parse_events((out_dir / row["raw"]).read_text(encoding="utf-8"))
    base = {k: v for k, v in row.items() if k not in ("error", "traceback")}
    try:
        with tempfile.TemporaryDirectory(prefix="bench-regrade-") as tmp:
            workspace = Path(tmp) / "workspace"
            extract_workspace(out_dir / row["workspace"], workspace, row.get("workspace_symlinks"))
            graded = grade(subject, workspace, events, row["skill_dirs"])
    except Exception as exc:  # noqa: BLE001 - keep going, like the batch
        return {**base, "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}
    return {**base, "checks": graded, **transcript_fields(events, subject, row.get("auth", "api-key")),
            "regraded_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}


def regrade(out_dir: Path, subject: Subject, log=print) -> None:
    """Writes results.regraded.jsonl and summary.regraded.md; the original files stay untouched."""
    rows = [regrade_row(row, subject, out_dir) for row in summary.read_rows(out_dir / "results.jsonl")]
    with (out_dir / "results.regraded.jsonl").open("w", encoding="utf-8") as fh:
        fh.writelines(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    log(summary.write_summary(out_dir, "results.regraded.jsonl", "summary.regraded.md"))
