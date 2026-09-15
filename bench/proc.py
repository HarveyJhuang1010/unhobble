"""Subprocess helpers shared by the runner and the quality checks. Stdlib only.

Children run in their own process group so a timeout, an abort, or Ctrl-C can
kill grandchildren too (claude and node --test both fork). A new session also
means the terminal's Ctrl-C no longer reaches them, so every path that leaves
early kills the group itself.
"""
from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Outcome:
    stdout: str
    stderr: str
    exit_code: int | None  # None when the bench killed the process
    timed_out: bool
    abort_reason: str | None = None  # set when should_abort stopped the run


def kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def run_process(cmd: list[str], cwd: Path, env: dict | None = None, timeout: float | None = None) -> Outcome:
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        kill_group(proc)
        out, err = proc.communicate()
        timed_out = True
    except BaseException:
        kill_group(proc)
        proc.wait()
        raise
    return Outcome(out.decode("utf-8", "replace"), err.decode("utf-8", "replace"),
                   None if timed_out else proc.returncode, timed_out)


def no_abort(line: str) -> str | None:
    return None


def stream_process(cmd: list[str], cwd: Path, env: dict | None, timeout: float, should_abort=no_abort) -> Outcome:
    """Read stdout line by line; kill the group as soon as should_abort(line) returns a reason."""
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=err, start_new_session=True)
        expired = threading.Event()

        def expire() -> None:
            expired.set()
            kill_group(proc)

        timer = threading.Timer(timeout, expire)
        timer.daemon = True
        timer.start()
        lines: list[str] = []
        reason = None
        try:
            for raw in proc.stdout:
                line = raw.decode("utf-8", "replace")
                lines.append(line)
                reason = should_abort(line)
                if reason:
                    kill_group(proc)
                    break
            proc.wait()
        except BaseException:
            kill_group(proc)
            proc.wait()
            raise
        finally:
            timer.cancel()
            proc.stdout.close()
        kill_group(proc)  # orphans left behind by a finished run
        err.seek(0)
        stderr = err.read().decode("utf-8", "replace")
    killed = reason is not None or expired.is_set()
    return Outcome("".join(lines), stderr, None if killed else proc.returncode,
                   expired.is_set() and reason is None, reason)
