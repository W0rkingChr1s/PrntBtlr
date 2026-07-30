"""Safe subprocess helper.

Every command is executed as an argument list (never through a shell), so user
supplied values such as printer names cannot trigger shell injection.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TypeVar

from ..config import settings

log = logging.getLogger("prntbtlr.shell")

T = TypeVar("T")


@dataclass
class Result:
    ok: bool
    returncode: int
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        """Best-effort combined human-readable output."""
        return (self.stdout or self.stderr).strip()


class CommandError(RuntimeError):
    """Raised for callers that prefer exceptions over Result inspection."""

    def __init__(self, result: Result, cmd: list[str]):
        self.result = result
        self.cmd = cmd
        super().__init__(f"`{' '.join(cmd)}` failed ({result.returncode}): {result.output}")


def which(binary: str) -> str | None:
    """Return the resolved path of *binary* or ``None`` if it is not installed."""
    return shutil.which(binary)


def run(
    cmd: list[str],
    *,
    timeout: int | None = None,
    check: bool = False,
    input_text: str | None = None,
) -> Result:
    """Run *cmd* and capture its output.

    Returns a :class:`Result`. When ``check`` is true a non-zero exit raises
    :class:`CommandError`.
    """
    timeout = timeout or settings.command_timeout
    log.debug("exec: %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            input=input_text,
            check=False,
        )
    except FileNotFoundError:
        result = Result(False, 127, "", f"command not found: {cmd[0]}")
    except subprocess.TimeoutExpired:
        result = Result(False, 124, "", f"timed out after {timeout}s: {' '.join(cmd)}")
    else:
        result = Result(
            ok=proc.returncode == 0,
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
        )

    if check and not result.ok:
        raise CommandError(result, cmd)
    return result


def gather(tasks: list[Callable[[], T]], *, max_workers: int | None = None) -> list[T]:
    """Run independent zero-argument callables concurrently, preserving order.

    Most of a page's latency is spent waiting on blocking shell-outs
    (``systemctl``, ``lpstat``, ``scanimage`` …) that don't depend on one
    another. Fanning them across threads collapses N sequential waits into
    roughly one — the win that matters on a Raspberry Pi, where each subprocess
    spawn is tens of milliseconds. Results come back in the same order as
    *tasks*; the first exception raised is re-raised to the caller.

    A fresh, right-sized pool is created per call (thread creation is cheap next
    to a subprocess spawn), so nested ``gather`` calls each get their own
    workers and can't deadlock on a shared, saturated pool.
    """
    if not tasks:
        return []
    if len(tasks) == 1:
        return [tasks[0]()]
    workers = max_workers or min(len(tasks), 16)
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="prntbtlr") as pool:
        return [future.result() for future in [pool.submit(task) for task in tasks]]
