"""One MIRA runtime per user: election, forwarding, races and stale state.

The election and the client are Qt-free and tested directly. The behaviour
of the launchers is tested on real `python -m mira.main` processes on the
offscreen platform, each test in its own private runtime directory
(`conftest.isolate_runtime_directory`), so nothing here can reach the
developer's own running MIRA. Assertions wait on events (a log line, a
process exit), each with a generous bound, never on a fixed delay.
"""

from __future__ import annotations

import os
import queue
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from mira.application.instance import (
    LOCK_NAME,
    SOCKET_NAME,
    Forwarded,
    InstanceError,
    Primary,
    SurfaceRequest,
    claim_or_forward,
    runtime_directory,
    send_request,
    try_lock,
)

REPO = Path(__file__).resolve().parents[1]
MAIN = SurfaceRequest("main window")


@pytest.fixture
def directory():
    return runtime_directory()


# --- Identity ------------------------------------------------------------------------------


def test_the_runtime_directory_is_private_per_user_and_not_the_working_directory(tmp_path, monkeypatch):
    first = runtime_directory()
    monkeypatch.chdir(tmp_path)

    assert runtime_directory() == first
    assert first == Path(os.environ["XDG_RUNTIME_DIR"]) / "mira"
    info = first.stat()
    assert info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700


def test_without_xdg_runtime_dir_a_private_tmp_directory_is_used(tmp_path):
    # Another uid's name, so no real /tmp directory of this user is touched;
    # it exists after mkdir but belongs to us, not to that uid: refused.
    with pytest.raises(InstanceError):
        runtime_directory({}, uid=os.getuid() + 100000)


def test_a_shared_or_foreign_directory_is_refused(tmp_path):
    xdg = tmp_path / "xdg"
    (xdg / "mira").mkdir(parents=True)
    (xdg / "mira").chmod(0o777)

    with pytest.raises(InstanceError):
        runtime_directory({"XDG_RUNTIME_DIR": str(xdg)})


def test_requests_round_trip_and_reject_junk():
    request = SurfaceRequest("notch", "/home/u/n.json")

    assert SurfaceRequest.decode(request.encode().strip()) == request
    for junk in (b"", b"{", b"[]", b'{"show": "desktop"}', b'{"show": "notch", "x": 1}',
                 b'{"show": "notch", "notch_config": 3}'):
        with pytest.raises(ValueError):
            SurfaceRequest.decode(junk)


# --- Election ------------------------------------------------------------------------------


def test_the_first_claim_becomes_primary_and_the_lock_is_exclusive(directory):
    first = claim_or_forward(directory, MAIN, wait_s=0)
    try:
        assert isinstance(first, Primary)
        assert try_lock(directory) is None
    finally:
        first.release()
    fd = try_lock(directory)
    assert fd is not None
    os.close(fd)


def test_a_stale_socket_file_does_not_block_a_new_primary(directory):
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(directory / SOCKET_NAME))
    stale.close()  # the file stays; nobody listens: what a crash leaves
    assert (directory / SOCKET_NAME).exists()

    assert send_request(directory / SOCKET_NAME, MAIN) is None
    claimed = claim_or_forward(directory, MAIN, wait_s=0)
    assert isinstance(claimed, Primary)
    claimed.release()


def test_a_killed_primary_leaves_the_lock_free(directory):
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import sys, time; from pathlib import Path; from mira.application.instance import try_lock;"
         "fd = try_lock(Path(sys.argv[1])); print('locked' if fd else 'busy', flush=True); time.sleep(60)",
         str(directory)],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "locked"
        assert try_lock(directory) is None
        holder.send_signal(signal.SIGKILL)
        holder.wait(10)
    finally:
        holder.kill()

    fd = try_lock(directory)
    assert fd is not None
    os.close(fd)


def test_a_primary_that_never_listens_is_an_error_not_a_second_runtime(directory):
    fd = try_lock(directory)
    now = [0.0]

    def sleep(seconds):
        now[0] += seconds

    try:
        with pytest.raises(InstanceError, match="not answering"):
            claim_or_forward(directory, MAIN, wait_s=1.0, sleep=sleep, clock=lambda: now[0])
    finally:
        os.close(fd)


def test_the_lock_file_records_the_primary_and_is_not_inherited(directory):
    claimed = claim_or_forward(directory, MAIN, wait_s=0)
    try:
        assert (directory / LOCK_NAME).read_text().strip() == str(os.getpid())
        assert os.get_inheritable(claimed.lock_fd) is False
    finally:
        claimed.release()


# --- Real launchers --------------------------------------------------------------------------


class Runtime:
    """A real `python -m mira.main` process and its log, read on a thread."""

    def __init__(self, *arguments: str, env: dict[str, str]):
        self.process = subprocess.Popen(
            [sys.executable, "-u", "-m", "mira.main", *arguments],
            cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL),
        )
        self.lines: list[str] = []
        self._queue: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.process.stdout:
            self._queue.put(line.rstrip("\n"))

    def wait_for(self, text: str, timeout: float = 30.0) -> str:
        deadline = time.monotonic() + timeout
        for line in self.lines:
            if text in line:
                return line
        while time.monotonic() < deadline:
            try:
                line = self._queue.get(timeout=0.2)
            except queue.Empty:
                if self.process.poll() is not None and self._queue.empty():
                    break
                continue
            self.lines.append(line)
            if text in line:
                return line
        raise AssertionError(f"never saw {text!r} in:\n" + "\n".join(self.lines))

    def count(self, text: str) -> int:
        self.drain()
        return sum(text in line for line in self.lines)

    def drain(self):
        while True:
            try:
                self.lines.append(self._queue.get_nowait())
            except queue.Empty:
                return

    def stop(self) -> int:
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)
        try:
            return self.process.wait(30)
        finally:
            self.process.kill()
            self.drain()


@pytest.fixture
def env(tmp_path):
    environment = {**os.environ, "QT_QPA_PLATFORM": "offscreen",
                   "MIRA_NOTCH_CONFIG": str(tmp_path / "notch.json"), "PYTHONUNBUFFERED": "1"}
    for name in ("MIRA_CAMERA", "MIRA_INTENT_ENGINE"):
        environment.pop(name, None)
    return environment


@pytest.fixture
def runtimes(env):
    started: list[Runtime] = []

    def start(*arguments: str) -> Runtime:
        runtime = Runtime(*arguments, env=env)
        started.append(runtime)
        return runtime

    yield start
    for runtime in started:
        runtime.stop()


def launch(env, *arguments: str) -> subprocess.CompletedProcess:
    """A launcher that is expected to forward and exit."""
    return subprocess.run([sys.executable, "-m", "mira.main", *arguments], cwd="/", env=env,
                          capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize(("first", "second", "built"), [
    ((), ("--notch",), "notch created"),
    (("--notch",), (), "main window created"),
])
def test_the_second_launcher_joins_the_first_runtime(runtimes, env, first, second, built):
    primary = runtimes(*first)
    primary.wait_for("[mira] ready")

    joined = launch(env, *second)

    assert joined.returncode == 0, joined.stdout + joined.stderr
    assert "connected to the running M.I.R.A." in joined.stdout
    assert f"(pid {primary.process.pid})" in joined.stdout
    primary.wait_for(built)
    assert primary.process.poll() is None
    assert primary.count("primary runtime") == 1


@pytest.mark.parametrize(("arguments", "surface"), [((), "main window"), (("--notch",), "notch")])
def test_repeated_launchers_reuse_the_surface(runtimes, env, arguments, surface):
    primary = runtimes(*arguments)
    primary.wait_for("[mira] ready")

    for _ in range(3):
        assert launch(env, *arguments).returncode == 0

    primary.wait_for(f"{surface} reused")
    deadline = time.monotonic() + 30
    while primary.count(f"{surface} reused") < 3 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert primary.count(f"{surface} reused") == 3
    assert primary.count(f"{surface} created") == 1


def test_both_surfaces_end_up_in_one_runtime_when_launched_together(runtimes):
    first, second = runtimes(), runtimes("--notch")

    exits = []
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not exits:
        exits = [runtime for runtime in (first, second) if runtime.process.poll() is not None]
        time.sleep(0.05)

    # Exactly one of the two forwarded and exited; the other is the runtime.
    assert len(exits) == 1, "both kept running: two runtimes"
    forwarded = exits[0]
    survivor = second if forwarded is first else first
    assert forwarded.process.returncode == 0
    forwarded.wait_for("connected to the running M.I.R.A.")
    survivor.wait_for("main window created")
    survivor.wait_for("notch created")
    assert survivor.process.poll() is None


def test_a_crashed_runtime_is_replaced_by_the_next_launcher(runtimes, env):
    crashed = runtimes()
    crashed.wait_for("[mira] ready")
    crashed.process.send_signal(signal.SIGKILL)
    crashed.process.wait(10)
    stale = runtime_directory() / SOCKET_NAME
    assert stale.exists()  # left behind, as after any crash

    replacement = runtimes("--notch")
    replacement.wait_for("primary runtime")
    replacement.wait_for("notch created")

    joined = launch(env)
    assert joined.returncode == 0 and f"(pid {replacement.process.pid})" in joined.stdout


def test_a_clean_shutdown_removes_the_socket_and_frees_the_runtime(runtimes):
    primary = runtimes()
    primary.wait_for("[mira] ready")

    assert primary.stop() == 0

    assert not (runtime_directory() / SOCKET_NAME).exists()
    fd = try_lock(runtime_directory())
    assert fd is not None
    os.close(fd)
