"""Archive a finished workspace so every check can be rerun later, and restore it. Stdlib only.

Symlinks are recorded, never followed or archived: a link to an absolute path
would make extraction fail (or reach outside the workspace). Relative links that
stay inside the workspace are recreated on restore; the rest are not.
"""
from __future__ import annotations

import io
import os
import tarfile
from pathlib import Path

import redaction

ARCHIVE_LIMIT_BYTES = 20_000_000
SKIP_DIRS = {".git", "node_modules"}


def scan(workspace: Path) -> tuple[dict[str, Path], dict[str, str]]:
    """(regular files, symlinks as relative path -> target), skipping .git and node_modules."""
    files, links = {}, {}
    for directory, dirnames, filenames in os.walk(workspace):
        here = Path(directory)
        for name in list(dirnames):
            if name in SKIP_DIRS or (here / name).is_symlink():
                dirnames.remove(name)
                if name not in SKIP_DIRS:
                    links[(here / name).relative_to(workspace).as_posix()] = os.readlink(here / name)
        for name in filenames:
            path = here / name
            rel = path.relative_to(workspace).as_posix()
            if path.is_symlink():
                links[rel] = os.readlink(path)
            elif path.is_file():
                files[rel] = path
    return files, links


def archive_workspace(workspace: Path, dest: Path, secrets: list[str]) -> dict:
    """Write dest unless the files exceed ARCHIVE_LIMIT_BYTES.

    Returns {"skipped": reason or None, "symlinks": {...}, "names_redacted": bool}. File names are
    redacted like contents; the original name is not kept anywhere, so regrade sees the redacted one."""
    files, links = scan(workspace)
    size = sum(path.stat().st_size for path in files.values())
    if size > ARCHIVE_LIMIT_BYTES:
        return {"skipped": f"workspace files total {size:,} bytes, over the {ARCHIVE_LIMIT_BYTES:,}-byte archive limit",
                "symlinks": links, "names_redacted": False}
    names_redacted = False
    with tarfile.open(dest, "w:gz") as tar:
        for rel, path in sorted(files.items()):
            data = redaction.redact_bytes(path.read_bytes(), secrets)
            name = redaction.redact(rel, secrets)
            names_redacted = names_redacted or name != rel
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), path.stat().st_mode & 0o777, int(path.stat().st_mtime)
            tar.addfile(info, io.BytesIO(data))
    return {"skipped": None, "symlinks": links, "names_redacted": names_redacted}


def extract_workspace(archive: Path, dest: Path, symlinks: dict | None = None) -> None:
    dest.mkdir(parents=True)
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        for member in members:  # the bench only ever writes regular files with relative names
            if not member.isfile() or member.name.startswith("/") or ".." in member.name.split("/"):
                raise RuntimeError(f"unexpected member in {archive}: {member.name}")
        if hasattr(tarfile, "data_filter"):
            tar.extractall(dest, filter="data")
        else:
            tar.extractall(dest)
    root = dest.resolve()
    for rel, target in (symlinks or {}).items():
        link = dest / rel
        if os.path.isabs(target) or link.exists() or link.is_symlink():
            continue
        resolved = (link.parent / target).resolve()
        if resolved == root or root in resolved.parents:
            link.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(target, link)
