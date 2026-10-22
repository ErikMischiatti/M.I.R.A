"""Bounded, cancellable child processes for providers that ask other programs.

A device information provider runs a short read-only command (``adb``,
``upower``) on a worker thread and waits for it, at most its timeout. At
shutdown nobody should wait that long: `CancellableRunner.cancel` ends the
wait at once.

`CancellableRunner` is called like `subprocess.run` (the arguments the
providers use), so a test can pass a scripted stand-in instead. Each call
starts one child process and remembers it until it has been reaped;
`cancel` kills exactly those children — never another program's process,
and never adb's server, which is no longer our child once it has
daemonized — and makes every later call fail at once with `Cancelled`.

Calls run on worker threads, `cancel` on the UI thread: starting a child
and cancelling take the same lock, so a call either sees the cancellation
before it starts a child or its child is among those killed. A child that
finishes as it is being killed is harmless: `Popen.kill` does nothing once
the child has been reaped. Every child is reaped before its call returns.
"""

from __future__ import annotations

import subprocess
import threading


class Cancelled(Exception):
    """The runner was cancelled: the command did not run, or was stopped."""


class CancellableRunner:
    """`subprocess.run` for the providers, which `cancel` can stop; see the module."""

    def __init__(self):
        self._lock = threading.Lock()
        self._children: set[subprocess.Popen] = set()
        self._cancelled = False

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def __call__(
        self,
        command: list[str],
        *,
        timeout: float,
        capture_output: bool = True,
        text: bool = True,
        stdin=subprocess.DEVNULL,
        check: bool = False,
    ) -> subprocess.CompletedProcess:
        pipe = subprocess.PIPE if capture_output else None
        with self._lock:
            if self._cancelled:
                raise Cancelled()
            process = subprocess.Popen(command, stdin=stdin, stdout=pipe, stderr=pipe, text=text)
            self._children.add(process)
        try:
            try:
                stdout, stderr = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()  # reap it
                raise
        finally:
            with self._lock:
                self._children.discard(process)
        if self._cancelled:
            raise Cancelled()
        completed = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        if check:
            completed.check_returncode()
        return completed

    def cancel(self) -> None:
        """Kill this runner's children and refuse new calls; idempotent."""
        with self._lock:
            self._cancelled = True
            children = list(self._children)
        for child in children:
            try:
                child.kill()
            except OSError:
                pass  # already gone
