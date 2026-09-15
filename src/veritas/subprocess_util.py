"""Bounded subprocess execution.

`subprocess.run(..., timeout=...)` can hang after the timeout fires: it kills the
child and then re-enters `communicate()`, which blocks forever if the pipes were
inherited by a grandchild process. On Windows this has been observed to hang
indefinitely, which silently stalls a whole benchmark run.

`run_bounded()` uses Popen + wait(timeout) + kill and drains the pipes after the
child is dead, so a timeout always returns.
"""

from __future__ import annotations

import subprocess
from typing import Any


class CompletedLike:
    def __init__(self, args: list[str], returncode: int, stdout: str, stderr: str, timed_out: bool = False):
        self.args = args
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out


def run_bounded(
    argv: list[str],
    *,
    cwd: str | None = None,
    timeout_s: float | None = None,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
    stdin_devnull: bool = False,
) -> CompletedLike:
    """Run `argv`, always returning within ~timeout_s.

    On timeout the process is killed and `timed_out=True` is reported; the
    caller decides how to classify it.
    """
    popen_kwargs: dict[str, Any] = {
        "cwd": cwd,
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "shell": False,
    }
    if stdin_devnull:
        popen_kwargs["stdin"] = subprocess.DEVNULL
    elif input_text is not None:
        popen_kwargs["stdin"] = subprocess.PIPE

    process = subprocess.Popen(argv, **popen_kwargs)
    timed_out = False
    try:
        stdout, stderr = process.communicate(input=input_text, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        try:
            stdout, stderr = process.communicate(timeout=15)
        except Exception:  # noqa: BLE001 - a stubborn child must not stall the run
            try:
                process.wait(timeout=5)
            except Exception:  # noqa: BLE001
                pass
            stdout, stderr = "", ""
    return CompletedLike(argv, process.returncode if process.returncode is not None else -1, stdout or "", stderr or "", timed_out)
